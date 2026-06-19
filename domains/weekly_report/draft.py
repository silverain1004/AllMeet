"""주간보고초안 진입점 + 백그라운드 합성.

흐름:
1. ``handle_weekly_report_draft`` 가 챗 이벤트 받아 즉시 "분석 중" 카드 반환.
2. 백그라운드 thread 가 ``_run_draft_background`` 로 데이터 수집 + Vertex 분석.
3. 결과 카드를 Google Chat REST API 로 같은 스페이스에 push.

CONVENTIONS.md §11.2 — 도메인 합성. ``api/*`` 의 generic CRUD 를 호출해 비즈니스 의미 부여.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any

from api.calendar.events import list_events
from api.chat.messages import post_message_to_space
from api.chat.progress import LiveProgress
from api.confluence.pages import list_pages_modified
from api.drive.files import list_files_modified
from config.settings import ALLMEET_DRAFT_MODEL, LOCATION, PROJECT_ID
from domains.weekly_report.cards import (
    build_draft_card,
    build_in_progress_card,
    build_no_data_card,
    build_team_not_found_card,
)
from domains.weekly_report.prompts import RESPONSE_SCHEMA, build_draft_prompt
from domains.weekly_report.timewindow import (
    fallback_meeting_date_kst,
    find_meeting_date,
    kst_now,
    two_weeks_around_utc_iso,
)
from firestore.team_config import find_team_by_email, get_team_config, get_team_from_index

logger = logging.getLogger(__name__)

_vertex_lock = threading.Lock()
_vertex_model = None


# 메인 진입 — 챗 이벤트 → 즉시 응답 + 백그라운드 thread.
def handle_weekly_report_draft(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``주간보고초안`` 트리거 진입점. main.py 의 dispatch 에서 호출.

    Returns:
        즉시 응답용 카드 (Google Chat 동기 응답 ~30초 제한 내). 실제 결과는 백그라운드 thread 가
        Chat REST API 로 푸시.
    """
    space_name = ((chat_event or {}).get("space") or {}).get("name") or ""
    user = (chat_event or {}).get("user") or {}
    user_email = str(user.get("email") or "").strip()
    user_display_name = str(user.get("displayName") or user_email or "사용자").strip()

    if not space_name or not user_email:
        return {"text": "사용자 또는 스페이스 정보를 가져오지 못했어요. (Google Chat 이벤트 형식 확인 필요)"}

    # 백그라운드 시작 — Cloud Run --cpu-always-allocated 필요.
    threading.Thread(
        target=_run_draft_background,
        args=(space_name, user_email, user_display_name),
        daemon=False,
    ).start()

    return build_in_progress_card()


# 진행상황 라이브 메시지 단계 — 수집 6종 + AI 분석. _collect_all_services 의 key 와 일치.
_PROGRESS_STAGES: list[tuple[str, str]] = [
    ("calendar", "팀 캘린더"),
    ("drive", "Drive"),
    ("confluence", "Confluence"),
    ("gmail", "Gmail"),
    ("personal_calendar", "개인 캘린더"),
    ("personal_drive", "내 Drive"),
    ("analyze", "AI 분석"),
]


def _run_draft_background(space_name: str, user_email: str, user_display_name: str) -> None:
    """백그라운드 thread: 수집·분석 후 Chat REST 로 push (진행상황 라이브 갱신)."""
    reporter = LiveProgress(
        space_name=space_name, title="주간보고초안 분석 중...", stages=_PROGRESS_STAGES
    )
    reporter.begin()  # 실패해도 live=False 로 폴백 — 결과만 새 메시지로 push.
    try:
        result = _collect_and_draft(
            user_email=user_email,
            user_display_name=user_display_name,
            progress=reporter,
        )
        if reporter.live:
            reporter.finish(result)
        else:
            post_message_to_space(space_name=space_name, payload=result)
    except Exception:
        logger.exception("weekly_report background 실패")
        err = {"text": "주간보고초안 처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."}
        if reporter.live:
            reporter.finish(err)
        else:
            post_message_to_space(space_name=space_name, payload=err)


def _collect_and_draft(
    *,
    user_email: str,
    user_display_name: str,
    progress: LiveProgress | None = None,
) -> dict[str, Any]:
    """팀 매칭 → 회의 일자 → 두 주치 raw 수집 → Vertex 분석 → 카드 빌드."""
    # 1. 사용자 → 팀
    team_id = find_team_by_email(user_email)
    if not team_id:
        return build_team_not_found_card(user_email)

    team_index = get_team_from_index(team_id) or {}
    team_name = str(team_index.get("name") or team_id)
    calendar_id = str(team_index.get("calendar_id") or "").strip()
    space_key = str(
        team_index.get("space_key") or team_index.get("confluence_space_key") or ""
    ).strip()
    raw_drive_ids = team_index.get("shared_drive_ids") or []
    shared_drive_ids = (
        [str(x).strip() for x in raw_drive_ids if str(x).strip()]
        if isinstance(raw_drive_ids, list)
        else []
    )
    confluence_fallback_names = _confluence_fallback_names(team_id, user_email)

    # 2. 주간회의 일자 (KST 기준 ±30일 검색) — 공유 캘린더라 본인 팀 회의로 좁힌다.
    meeting_date_dt = _find_meeting_date_for_team(calendar_id, team_name)

    meeting_date_str = meeting_date_dt.strftime("%Y-%m-%d (%a)")

    # 3. 한 주 범위 — KST 회의주 + 전주 → UTC RFC3339
    time_min, time_max = two_weeks_around_utc_iso(meeting_date_dt)
    logger.info(
        "weekly_report _collect_and_draft team_id=%s user_email=%s meeting_date=%s time_min=%s time_max=%s drive_ids=%s",
        team_id,
        user_email,
        meeting_date_str,
        time_min,
        time_max,
        shared_drive_ids,
    )

    # 4. 서비스별 raw 수집 (try/except 격리)
    raw, errors = _collect_all_services(
        user_email=user_email,
        calendar_id=calendar_id,
        space_key=space_key,
        shared_drive_ids=shared_drive_ids,
        time_min=time_min,
        time_max=time_max,
        confluence_fallback_names=confluence_fallback_names,
        progress=progress,
    )

    # 5. 모든 섹션 빈 결과 + 모든 에러 무 → 활동 없음 안내
    if _has_any_data(raw) is False and _has_any_error(errors) is False:
        return build_no_data_card(user_display_name, team_name, meeting_date_str)

    # 6. Vertex 분석 (실패해도 raw 만으로 카드).
    # 카드 리스트 표시용 raw 는 그대로, LLM 입력만 주간회의 자체 항목 제거 — 자기참조 요약 방지.
    if progress:
        progress.active("analyze")
    draft = _vertex_analyze(
        user_display_name, user_email, meeting_date_str, _filter_for_draft(raw)
    )

    # 7. 초안 Firestore 임시 저장 (버튼 파라미터 크기 제한 회피용).
    draft_ref_id = _save_weekly_draft(user_email=user_email, draft=draft)

    # 8. 카드 빌드
    return build_draft_card(
        user_name=user_display_name,
        user_email=user_email,
        team_name=team_name,
        meeting_date=meeting_date_str,
        raw=raw,
        draft=draft,
        errors=errors,
        draft_ref_id=draft_ref_id,
    )


def _find_meeting_date_for_team(calendar_id: str, team_name: str = "") -> datetime:
    """팀 캘린더에서 본인 팀 '주간회의' 검색 → 가장 가까운 일자. 없으면 폴백.

    공유 캘린더에는 여러 팀의 주간회의가 섞여 있어 ``team_name`` 으로 제목을 좁힌다.
    좁히지 않으면 가장 가까운 *남의 팀* 회의 일자가 찍힌다.
    """
    if not calendar_id:
        return fallback_meeting_date_kst()
    now = kst_now()
    search_min = (now - timedelta(days=30)).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
    search_max = (now + timedelta(days=14)).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")

    res = list_events(
        calendar_id=calendar_id,
        time_min=search_min,
        time_max=search_max,
        q="주간회의",
    )
    if res.ok and res.events:
        found = find_meeting_date(res.events, reference=now, team_name=team_name)
        if found is not None:
            return found
    return fallback_meeting_date_kst(now)


def _collect_all_services(
    *,
    user_email: str,
    calendar_id: str,
    space_key: str,
    time_min: str,
    time_max: str,
    shared_drive_ids: list[str] | None = None,
    confluence_fallback_names: list[str] | None = None,
    progress: LiveProgress | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """서비스별 호출 — 각각 try/except 격리. 한 곳 실패가 전체 죽이지 않게.

    ``progress`` 가 주어지면 각 서비스 완료 직후 진행상황 메시지를 갱신한다(베스트-에포트).
    """
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def _report(key: str) -> None:
        if progress is not None:
            progress.done(key, len(raw.get(key) or []))

    # Calendar (공용) — 본인이 참여한 이벤트만 필터, 노이즈(단순 상태표시·자기참조·전사 공지) 추가 제외.
    if calendar_id:
        try:
            cal_res = list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max)
            if cal_res.ok:
                raw["calendar"] = [
                    ev for ev in cal_res.events
                    if _is_user_involved(ev, user_email) and not _is_calendar_noise(ev, user_email)
                ]
            else:
                errors["calendar"] = cal_res.error_kind or "unknown"
        except Exception as e:
            logger.warning("calendar 호출 예외: %s", e)
            errors["calendar"] = "exception"
    else:
        errors["calendar"] = "calendar_id_missing"
    _report("calendar")

    # Drive — 팀이 등록한 Shared Drive 들 각각 조회 후 합산. 비어 있으면 env 폴백(미설정 시 안내).
    # 쿼리(`'<email>' in writers`)는 권한 기반이라 본인이 멤버인 한 다른 사람이 마지막 수정자인 파일도
    # 같이 올라옴 — 본인 활동만 추리려고 lastModifyingUser 후처리.
    drive_ids = [d for d in (shared_drive_ids or []) if d]
    if drive_ids:
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        per_drive_errors: list[str] = []
        per_drive_kinds: list[str] = []
        for d_id in drive_ids:
            try:
                # 사용자 OAuth 로 Shared Drive 조회 — 외부 SA 호출 시 lastModifyingUser.emailAddress
                # 가 비어 본인 매칭이 실패하던 문제 회피. 미동의자는 auth_required 로 폴백.
                drv_res = list_files_modified(
                    modified_by_email=user_email,
                    time_min=time_min,
                    time_max=time_max,
                    drive_id=d_id,
                    credentials_source="user_oauth",
                    user_email=user_email,
                )
                if drv_res.ok:
                    filtered = _only_my_modifications(drv_res.files, user_email)
                    sample = [
                        (f.get("name") or "")[:30] + ":" + (f.get("last_modifier_email") or "")
                        for f in (drv_res.files or [])[:5]
                    ]
                    logger.info(
                        "weekly_report drive drive_id=%s raw=%d filtered_by_me=%d sample=%s",
                        d_id,
                        len(drv_res.files),
                        len(filtered),
                        sample,
                    )
                    for f in filtered:
                        fid = str(f.get("id") or "")
                        if fid and fid in seen_ids:
                            continue
                        if fid:
                            seen_ids.add(fid)
                        merged.append(f)
                else:
                    per_drive_errors.append(f"{d_id}:{drv_res.error_kind or 'unknown'}")
                    per_drive_kinds.append(drv_res.error_kind or "unknown")
            except Exception as e:
                logger.warning("drive 호출 예외 drive_id=%s: %s", d_id, e)
                per_drive_errors.append(f"{d_id}:exception")
                per_drive_kinds.append("exception")
        raw["drive"] = merged
        if per_drive_errors and not merged:
            # 모든 드라이브가 동일하게 OAuth 미동의면 카드가 'OAuth 미연결' 안내를 띄우도록 정규화
            # (그 외에는 디버깅 위해 "{drive_id}:{kind}" 원문 유지).
            if per_drive_kinds and all(k == "auth_required" for k in per_drive_kinds):
                errors["drive"] = "auth_required"
            else:
                errors["drive"] = ";".join(per_drive_errors)
    else:
        try:
            drv_res = list_files_modified(
                modified_by_email=user_email, time_min=time_min, time_max=time_max
            )
            if drv_res.ok:
                raw["drive"] = _only_my_modifications(drv_res.files, user_email)
            else:
                errors["drive"] = drv_res.error_kind or "unknown"
        except Exception as e:
            logger.warning("drive 호출 예외: %s", e)
            errors["drive"] = "exception"
    _report("drive")

    # Confluence
    if space_key:
        try:
            pg_res = list_pages_modified(
                space_key=space_key,
                modified_by_email=user_email,
                time_min=time_min,
                time_max=time_max,
                fallback_display_names=confluence_fallback_names,
            )
            if pg_res.ok:
                # 주간회의 회의록 제거 — 본 초안을 위한 회의라 자기참조.
                raw["confluence"] = [
                    p for p in pg_res.pages
                    if not _CONFLUENCE_NOISE_TITLE_RE.search(str(p.get("title") or ""))
                ]
            else:
                errors["confluence"] = pg_res.error_kind or "unknown"
        except Exception as e:
            logger.warning("confluence 호출 예외: %s", e)
            errors["confluence"] = "exception"
    else:
        errors["confluence"] = "space_key_missing"
    _report("confluence")

    # Gmail (Phase 2 — OAuth 동의자 한정). 노이즈 메일은 raw 단계에서 사전 제거 → 카드 리스트도 깔끔.
    try:
        from api.gmail.messages import list_messages

        gm_res = list_messages(user_email=user_email, time_min=time_min, time_max=time_max)
        if gm_res.ok:
            raw["gmail"] = _filter_gmail_noise(gm_res.messages, user_email)
        else:
            errors["gmail"] = gm_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("gmail 호출 예외: %s", e)
        errors["gmail"] = "exception"
    _report("gmail")

    # 개인 Calendar (Phase 2 — OAuth 동의자 한정, 본인 primary). 단순 상태표시·자기참조·전사 공지 제거.
    try:
        pcal_res = list_events(
            calendar_id="primary",
            time_min=time_min,
            time_max=time_max,
            credentials_source="user_oauth",
            user_email=user_email,
        )
        if pcal_res.ok:
            raw["personal_calendar"] = [
                ev for ev in pcal_res.events if not _is_calendar_noise(ev, user_email)
            ]
        else:
            errors["personal_calendar"] = pcal_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("personal_calendar 호출 예외: %s", e)
        errors["personal_calendar"] = "exception"
    _report("personal_calendar")

    # 내 Drive (Phase 2 — OAuth 동의자 한정, 본인 My Drive)
    try:
        pdrv_res = list_files_modified(
            modified_by_email=user_email,
            time_min=time_min,
            time_max=time_max,
            credentials_source="user_oauth",
            user_email=user_email,
        )
        if pdrv_res.ok:
            raw["personal_drive"] = _only_my_modifications(pdrv_res.files, user_email)
        else:
            errors["personal_drive"] = pdrv_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("personal_drive 호출 예외: %s", e)
        errors["personal_drive"] = "exception"
    _report("personal_drive")

    return raw, errors


def _confluence_fallback_names(team_id: str, user_email: str) -> list[str]:
    """팀 멤버 중 ``user_email`` 매칭 항목의 ``name`` + ``nickname[]`` 후보 리스트.

    Confluence accountId 해석이 실패했을 때 ``version.by.displayName`` 매칭에 사용.
    """
    if not team_id or not user_email:
        return []
    try:
        cfg = get_team_config(team_id) or {}
    except Exception as e:
        logger.warning("team_config 로드 실패 team_id=%s: %s", team_id, e)
        return []
    needle = user_email.strip().lower()
    out: list[str] = []
    for m in cfg.get("team_members") or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("email") or "").strip().lower() != needle:
            continue
        name = str(m.get("name") or "").strip()
        if name:
            out.append(name)
        for nick in m.get("nickname") or []:
            s = str(nick or "").strip()
            if s:
                out.append(s)
        break
    return out


def _is_user_involved(event: dict[str, Any], user_email: str) -> bool:
    """팀 캘린더 이벤트 중 사용자가 실제로 참여한 것만 통과시킴.

    팀 캘린더에는 다른 팀원의 휴가·재택 같은 attendees 없는 단순 일정도 섞여 있어,
    주간보고초안 본인 회의 이력에서는 빼야 함.

    통과 조건 (둘 중 하나):
    - attendees 에 본인 이메일이 있고 ``response_status != "declined"``
    - organizer/creator 가 본인 (본인이 만든 회의)
    """
    if not user_email:
        return True  # 안전한 폴백 — 이메일 없으면 필터링 자체 의미 없음
    me = user_email.strip().lower()
    if str(event.get("organizer_email") or "").strip().lower() == me:
        return True
    if str(event.get("creator_email") or "").strip().lower() == me:
        return True
    for a in event.get("attendees") or []:
        if str(a.get("email") or "").strip().lower() != me:
            continue
        if str(a.get("response_status") or "") == "declined":
            return False
        return True
    return False


# 단순 상태 표시·자기참조 회의 — 주간보고초안 입력으로 부적합.
# 정규식은 strip 후 대상이므로 ^...$ 가 안전.
_CAL_NOISE_TITLE_RE = re.compile(
    r"^사무실$"
    r"|^출근$"
    r"|^퇴근$"
    r"|^재택$"
    r"|^반차$"
    r"|^오전\s*반차$"
    r"|^오후\s*반차$"
    r"|^연차$"
    r"|^오프$"
    r"|^휴가$"
    r"|^OOO$"
    r"|주간\s*회의"  # 주간보고초안 컨텍스트 — 본 초안을 위한 회의라 자기참조
)

# 대규모 공지/설명회의 임계 인원 — 이상이고 본인 organizer 가 아니면 청자 입장으로 간주.
_CAL_BROADCAST_ATTENDEE_THRESHOLD = 50

# Confluence 페이지 노이즈 — 주간보고초안 컨텍스트에서 자기참조 회의록(주간회의) 제거.
_CONFLUENCE_NOISE_TITLE_RE = re.compile(r"주간\s*회의")


def _is_calendar_noise(event: dict[str, Any], user_email: str) -> bool:
    """주간보고초안 입력으로 부적합한 이벤트.

    - 단순 상태 표시 (사무실/재택/반차 등) — 업무 산출물 아님.
    - 주간회의 자체 — 본 초안을 위한 회의라 자기참조.
    - 대규모 공지/설명회 (참석자 ``>= _CAL_BROADCAST_ATTENDEE_THRESHOLD``) 에 본인이
      organizer 가 아닐 때 — 본인은 청자 입장.
    """
    title = str(event.get("summary") or "").strip()
    if title and _CAL_NOISE_TITLE_RE.search(title):
        return True

    attendees = event.get("attendees") or []
    if len(attendees) >= _CAL_BROADCAST_ATTENDEE_THRESHOLD:
        me = (user_email or "").strip().lower()
        organizer = str(event.get("organizer_email") or "").strip().lower()
        if not me or organizer != me:
            return True

    return False


# Gmail 노이즈 패턴 — 본인 업무 아닌 자동발신/공유/뉴스레터 류. raw 수집 단계에서 제거되어
# 카드 리스트와 LLM 입력 모두에서 사라짐. 본인 발신 메일은 별도 검사로 항상 통과.
# 정보 공유·후기·안내·공지·생산계획 류 — 본인이 안 보낸 정보 수신은 본인 업무 아님.
_INBOUND_SHARE_SUBJECT_RE = re.compile(
    r"공유|안내|후기|생산계획|\[공지\]|\[전사\s*공지\]|초대장:"
)
_NOISE_SUBJECT_RE = re.compile(
    r"Claude\.ai\s*로그인용\s*보안\s*링크"
    r"|보안\s*알림"
    r"|일일\s*다이제스트를?\s*놓치지\s*마세요"
    r"|^Re:\s*\[Slashpage\]"
    r"|\[Slashpage\]"
    r"|^\(광고\)"
    r"|광고\s*\|"
    # 사내 캠페인·복지·복리후생 — 본인 업무 아님 (수혜자/대상자).
    r"|베이비\s*패키지"
    r"|칭찬\s*챌린지"
    r"|힐링\s*명상"
    r"|체어\s*요가"
    r"|복리\s*후생"
    r"|\+\s*\d+\s*M\s*적립"
    r"|채널\s*개설"
    r"|지원\s*정책"
    # 외부 SaaS 마케팅·뉴스레터 (영문 패턴).
    r"|^Composer\b.*\bis now available"
    r"|\bnew\s+features\s+in\b"
    r"|\bnow\s+available\b"
    r"|^Complete\s+your\s+sign[- ]?up"
    r"|customer\s+evidence"
    # 전사 공지·수요조사 — 본인이 주관 아니면 청자 입장 (대형 broadcast 는 Layer 2 가 보조).
    r"|성과관리제도.*설명회"
    r"|수요\s*조사",
    re.IGNORECASE,
)
_NOISE_SENDER_RE = re.compile(
    r"no-reply@|noreply@"
    r"|Anthropic.*via\s*VNTG\s*AI"
    r"|Claude\s*Team"
    r"|Mermaid\b"
    r"|@email\.cl[ai]"
    r"|no-reply@accounts\.goog"
    r"|@slashpage\."
    r"|info@mkt-email\."
    # 외부 SaaS 마케팅·트랜잭션 메일 발신 도메인.
    r"|@mail\.cursor\."
    r"|@cursor\.com"
    r"|@e\.atlassian\.co"
    r"|@atlassian\.com"
    r"|@dify\.ai"
    r"|@light\.tickitack"
    r"|@orblit\."
    r"|hello@dify"
    r"|team@mail\.cursor"
    r"|info@e\.atlassian",
    re.IGNORECASE,
)


def _filter_gmail_noise(
    emails: list[dict[str, Any]], user_email: str
) -> list[dict[str, Any]]:
    """본인 업무 아닌 노이즈 메일 제거. 카드 리스트·LLM 입력 모두에 영향.

    제거 대상:
    - 동료가 보낸 공유/후기/인사이트 메일 (제목 패턴 + 발신자 ≠ 본인)
    - Claude.ai 로그인 보안 링크, Google 보안 알림, 광고, 뉴스레터, Confluence 일일 다이제스트
    - no-reply / 알려진 뉴스레터 발신자

    본인 발신 메일은 무조건 통과 (예: 본인이 보낸 후기 공유는 본인 행위라 유지).
    """
    user = (user_email or "").strip().lower()
    out: list[dict[str, Any]] = []
    for m in emails or []:
        sender = str(m.get("from") or "").lower()
        # 본인 발신은 항상 통과
        if user and user in sender:
            out.append(m)
            continue
        subject = str(m.get("subject") or "")
        if _INBOUND_SHARE_SUBJECT_RE.search(subject):
            continue
        if _NOISE_SUBJECT_RE.search(subject):
            continue
        if _NOISE_SENDER_RE.search(sender):
            continue
        out.append(m)
    return out


def _only_my_modifications(
    files: list[dict[str, Any]], user_email: str
) -> list[dict[str, Any]]:
    """본인이 마지막 수정자인 파일만 반환.

    Drive API 의 ``'<email>' in writers`` 쿼리는 권한 보유자 필터라, Shared Drive 멤버이기만
    하면 다른 팀원이 수정한 파일도 같이 올라옴. ``lastModifyingUser.emailAddress`` 로 후처리.
    user_email 빈 값이면 필터 안 함(안전한 폴백).

    Workspace 의 외부→내부 이메일 노출 정책으로 SA 호출 시 emailAddress 가 비어 오기도 함.
    그 경우를 위해 ``last_modifier_is_me`` (lastModifyingUser.me) 도 본인 신호로 인정.
    """
    me = (user_email or "").strip().lower()
    if not me:
        return list(files or [])
    return [
        f for f in (files or [])
        if str(f.get("last_modifier_email") or "").strip().lower() == me
        or bool(f.get("last_modifier_is_me"))
    ]


def _filter_for_draft(raw: dict[str, Any]) -> dict[str, Any]:
    """LLM 입력용 raw 사본 — 주간회의 자체 항목 제거.

    주간회의를 위해 작성하는 초안이라 '주간회의 진행' 같은 자기참조 요약을 막아야 함.
    카드 리스트(원본 raw)에는 그대로 남기고, 분석 단계에서만 빠짐.
    """
    keyword = "주간회의"
    filtered = dict(raw)

    for cal_key in ("calendar", "personal_calendar"):
        items = filtered.get(cal_key) or []
        filtered[cal_key] = [
            e for e in items if keyword not in str(e.get("summary") or "")
        ]

    pages = filtered.get("confluence") or []
    filtered["confluence"] = [
        p for p in pages if keyword not in str(p.get("title") or "")
    ]

    return filtered


def _has_any_data(raw: dict[str, Any]) -> bool:
    return any(bool(v) for v in raw.values())


def _has_any_error(errors: dict[str, str]) -> bool:
    """auth_required 같은 '미설정' 류는 에러로 표시되지만 진짜 활동 없음과 구분."""
    real_errors = {k: v for k, v in errors.items() if v not in ("", "auth_required")}
    return bool(real_errors)


# 함수 — Vertex Gemini 호출 (실패 시 None — 카드는 raw 만으로 빌드).
def _vertex_analyze(
    user_name: str,
    user_email: str,
    meeting_date: str,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        model = _get_vertex_model()
        from vertexai.generative_models import GenerationConfig

        prompt = build_draft_prompt(
            user_name=user_name,
            user_email=user_email,
            meeting_date=meeting_date,
            raw=raw,
        )
        res = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                max_output_tokens=8192,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
        text = (res.text or "").strip()
        if not text:
            return None
        return json.loads(text)
    except Exception as e:
        logger.warning("Vertex 분석 실패, raw 만 카드로 표시: %s", e)
        return None


# 함수 — Firestore weekly_drafts 임시 저장.
def _save_weekly_draft(*, user_email: str, draft: dict | None) -> str | None:
    """초안을 Firestore weekly_drafts/{email} 에 7일 TTL 로 저장 → draft_ref_id 반환."""
    if not draft:
        return None
    try:
        from datetime import timezone

        from firestore import get_client

        db = get_client()
        expire_at = datetime.now(timezone.utc) + timedelta(days=7)
        db.collection("weekly_drafts").document(user_email).set(
            {
                "draft": draft,
                "user_email": user_email,
                "created_at": datetime.now(timezone.utc),
                "expire_at": expire_at,
            }
        )
        return user_email
    except Exception as e:
        logger.warning("weekly_draft Firestore 저장 실패: %s", e)
        return None


# 함수 — Vertex GenerativeModel 싱글톤.
def _get_vertex_model():
    global _vertex_model
    if _vertex_model is not None:
        return _vertex_model
    with _vertex_lock:
        if _vertex_model is not None:
            return _vertex_model
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _vertex_model = GenerativeModel(ALLMEET_DRAFT_MODEL)
    return _vertex_model
