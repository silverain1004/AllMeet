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
import threading
from datetime import datetime, timedelta
from typing import Any

from api.calendar.events import list_events
from api.chat.messages import post_message_to_space
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
from firestore.team_config import find_team_by_email, get_team_from_index

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


def _run_draft_background(space_name: str, user_email: str, user_display_name: str) -> None:
    """백그라운드 thread: 수집·분석 후 Chat REST 로 push."""
    try:
        result = _collect_and_draft(user_email=user_email, user_display_name=user_display_name)
        post_message_to_space(space_name=space_name, payload=result)
    except Exception:
        logger.exception("weekly_report background 실패")
        post_message_to_space(
            space_name=space_name,
            payload={"text": "주간보고초안 처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."},
        )


def _collect_and_draft(*, user_email: str, user_display_name: str) -> dict[str, Any]:
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

    # 2. 주간회의 일자 (KST 기준 ±30일 검색)
    meeting_date_dt = _find_meeting_date_for_team(calendar_id)

    meeting_date_str = meeting_date_dt.strftime("%Y-%m-%d (%a)")

    # 3. 한 주 범위 — KST 회의주 + 전주 → UTC RFC3339
    time_min, time_max = two_weeks_around_utc_iso(meeting_date_dt)

    # 4. 서비스별 raw 수집 (try/except 격리)
    raw, errors = _collect_all_services(
        user_email=user_email,
        calendar_id=calendar_id,
        space_key=space_key,
        time_min=time_min,
        time_max=time_max,
    )

    # 5. 모든 섹션 빈 결과 + 모든 에러 무 → 활동 없음 안내
    if _has_any_data(raw) is False and _has_any_error(errors) is False:
        return build_no_data_card(user_display_name, team_name, meeting_date_str)

    # 6. Vertex 분석 (실패해도 raw 만으로 카드)
    draft = _vertex_analyze(user_display_name, meeting_date_str, raw)

    # 7. 카드 빌드
    return build_draft_card(
        user_name=user_display_name,
        user_email=user_email,
        team_name=team_name,
        meeting_date=meeting_date_str,
        raw=raw,
        draft=draft,
        errors=errors,
    )


def _find_meeting_date_for_team(calendar_id: str) -> datetime:
    """팀 캘린더에서 '주간회의' 검색 → 가장 가까운 일자. 없으면 폴백."""
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
        found = find_meeting_date(res.events, reference=now)
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
) -> tuple[dict[str, Any], dict[str, str]]:
    """서비스별 호출 — 각각 try/except 격리. 한 곳 실패가 전체 죽이지 않게."""
    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}

    # Calendar (공용)
    if calendar_id:
        try:
            cal_res = list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max)
            if cal_res.ok:
                raw["calendar"] = cal_res.events
            else:
                errors["calendar"] = cal_res.error_kind or "unknown"
        except Exception as e:
            logger.warning("calendar 호출 예외: %s", e)
            errors["calendar"] = "exception"
    else:
        errors["calendar"] = "calendar_id_missing"

    # Drive
    try:
        drv_res = list_files_modified(
            modified_by_email=user_email, time_min=time_min, time_max=time_max
        )
        if drv_res.ok:
            raw["drive"] = drv_res.files
        else:
            errors["drive"] = drv_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("drive 호출 예외: %s", e)
        errors["drive"] = "exception"

    # Confluence
    if space_key:
        try:
            pg_res = list_pages_modified(
                space_key=space_key,
                modified_by_email=user_email,
                time_min=time_min,
                time_max=time_max,
            )
            if pg_res.ok:
                raw["confluence"] = pg_res.pages
            else:
                errors["confluence"] = pg_res.error_kind or "unknown"
        except Exception as e:
            logger.warning("confluence 호출 예외: %s", e)
            errors["confluence"] = "exception"
    else:
        errors["confluence"] = "space_key_missing"

    # Gmail (Phase 2 — OAuth 동의자 한정)
    try:
        from api.gmail.messages import list_messages

        gm_res = list_messages(user_email=user_email, time_min=time_min, time_max=time_max)
        if gm_res.ok:
            raw["gmail"] = gm_res.messages
        else:
            errors["gmail"] = gm_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("gmail 호출 예외: %s", e)
        errors["gmail"] = "exception"

    # 개인 Calendar (Phase 2 — OAuth 동의자 한정, 본인 primary)
    try:
        pcal_res = list_events(
            calendar_id="primary",
            time_min=time_min,
            time_max=time_max,
            credentials_source="user_oauth",
            user_email=user_email,
        )
        if pcal_res.ok:
            raw["personal_calendar"] = pcal_res.events
        else:
            errors["personal_calendar"] = pcal_res.error_kind or "unknown"
    except Exception as e:
        logger.warning("personal_calendar 호출 예외: %s", e)
        errors["personal_calendar"] = "exception"

    return raw, errors


def _has_any_data(raw: dict[str, Any]) -> bool:
    return any(bool(v) for v in raw.values())


def _has_any_error(errors: dict[str, str]) -> bool:
    """auth_required 같은 '미설정' 류는 에러로 표시되지만 진짜 활동 없음과 구분."""
    real_errors = {k: v for k, v in errors.items() if v not in ("", "auth_required")}
    return bool(real_errors)


# 함수 — Vertex Gemini 호출 (실패 시 None — 카드는 raw 만으로 빌드).
def _vertex_analyze(
    user_name: str, meeting_date: str, raw: dict[str, Any]
) -> dict[str, Any] | None:
    try:
        model = _get_vertex_model()
        from vertexai.generative_models import GenerationConfig

        prompt = build_draft_prompt(user_name=user_name, meeting_date=meeting_date, raw=raw)
        res = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                max_output_tokens=2048,
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
