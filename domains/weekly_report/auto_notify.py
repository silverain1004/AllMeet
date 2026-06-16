"""평일 주간보고 초안 자동 DM 발송 + Confluence 미입력 리마인드.

Cloud Scheduler 트리거:
  POST /trigger/weekly-draft-notify        → send_weekly_draft_to_all()
  POST /trigger/weekly-confluence-reminder → send_confluence_reminder_to_all()

동작:
  · 평일 10:00 KST: 다음 영업일에 '주간회의' 이벤트가 있는 팀원에게 초안 DM
  · 평일 14:00 KST: 위 조건 + 오늘 Confluence 미수정 팀원에게 초안 재발송
  · 금(4)→+3일, 토(5)→+2일, 일(6)→+1일 로 다음 영업일 계산
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_CONVERSATIONS_COLLECTION = "conversations"
_CONFIG_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def send_weekly_draft_to_all() -> dict[str, Any]:
    """평일 10:00 — 다음 영업일 주간회의 팀원에게 초안 DM 발송."""
    teams = _collect_members_by_team()
    logger.info("[auto_notify] draft 대상 팀 %d개", len(teams))

    result: dict[str, list[str]] = {
        "sent": [], "skipped_no_meeting": [], "skipped_no_space": [], "skipped_error": [],
    }
    for team in teams:
        if not is_weekly_meeting_next_workday(team["calendar_id"]):
            result["skipped_no_meeting"].append(team["team_id"])
            continue
        for m in team["members"]:
            status = _send_draft_to_user(user_email=m["email"], user_display_name=m["name"])
            result[status].append(m["email"])

    logger.info(
        "[auto_notify] draft 완료 — 발송: %d, 회의없음팀: %d, space없음: %d, 오류: %d",
        len(result["sent"]), len(result["skipped_no_meeting"]),
        len(result["skipped_no_space"]), len(result["skipped_error"]),
    )
    return result


def send_confluence_reminder_to_all() -> dict[str, Any]:
    """평일 14:00 — 다음 영업일 주간회의 팀 중 Confluence 미입력 시 초안 재발송."""
    teams = _collect_members_by_team()
    logger.info("[auto_notify] reminder 대상 팀 %d개", len(teams))

    result: dict[str, list[str]] = {
        "sent": [], "skipped_no_meeting": [], "skipped_already_edited": [],
        "skipped_no_space": [], "skipped_error": [],
    }
    for team in teams:
        team_id = team["team_id"]

        if not is_weekly_meeting_next_workday(team["calendar_id"]):
            result["skipped_no_meeting"].append(team_id)
            continue

        # 페이지 없으면 "아직 안 씀"으로 간주 → 리마인드 발송
        page_id = _get_this_week_page_id(team["report_root_page_id"], team["team_name"])
        if page_id and has_human_edit_this_week(page_id):
            result["skipped_already_edited"].append(team_id)
            continue

        for m in team["members"]:
            status = _send_draft_to_user(
                user_email=m["email"],
                user_display_name=m["name"],
                reminder=True,
            )
            result[status].append(m["email"])

    logger.info(
        "[auto_notify] reminder 완료 — 발송: %d, 회의없음팀: %d, 이미수정: %d, 오류: %d",
        len(result["sent"]), len(result["skipped_no_meeting"]),
        len(result["skipped_already_edited"]), len(result["skipped_error"]),
    )
    return result


# ---------------------------------------------------------------------------
# 캘린더 판단
# ---------------------------------------------------------------------------

def _next_workday_kst(from_dt: datetime) -> datetime:
    """from_dt 기준 다음 영업일. 금(4)→+3, 토(5)→+2, 일(6)→+1, 그 외→+1."""
    weekday = from_dt.weekday()
    delta = {4: 3, 5: 2, 6: 1}.get(weekday, 1)
    return from_dt + timedelta(days=delta)


def is_weekly_meeting_next_workday(calendar_id: str) -> bool:
    """다음 영업일(금→월, 그 외→내일) 팀 캘린더에 주간회의 이벤트가 있으면 True."""
    from api.calendar.events import list_events
    from domains.weekly_report.timewindow import KST

    if not calendar_id:
        return False

    now_kst = datetime.now(KST)
    target  = _next_workday_kst(now_kst)
    day_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = target.replace(hour=23, minute=59, second=59, microsecond=0)

    def _to_utc_z(d: datetime) -> str:
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = list_events(
        calendar_id=calendar_id,
        time_min=_to_utc_z(day_start),
        time_max=_to_utc_z(day_end),
        q="주간회의",
        credentials_source="service_account",
    )
    if not result.ok:
        logger.warning("[auto_notify] 캘린더 조회 실패 calendar_id=%s", calendar_id)
        return False
    return len(result.events) > 0


# ---------------------------------------------------------------------------
# Confluence 수정이력 판단
# ---------------------------------------------------------------------------

def has_human_edit_this_week(page_id: str) -> bool:
    """오늘 자정 이후 봇 외 수정이력이 있으면 True.

    봇 계정 = config/team_list.global.user_email (Confluence API 호출 계정).
    버전 조회 실패 시 True 반환 (fail-safe: 오탐보다 누락이 낫다).
    """
    from api.confluence.client import ConfluenceClient
    from api.confluence.users import resolve_account_id
    from firestore.team_config import get_global_settings
    from domains.weekly_report.timewindow import KST, kst_now

    bot_email      = (get_global_settings().get("user_email") or "").strip()
    bot_account_id = (resolve_account_id(bot_email) or {}).get("account_id") or ""
    # 오늘 자정 기준: "10시 초안 발송 이후 수정했는가?" 를 판단.
    # week_monday_kst 기준이면 금→월 시나리오에서 지난주 수정을 이번주로 오인함.
    today_kst = kst_now().replace(hour=0, minute=0, second=0, microsecond=0)

    client = ConfluenceClient()
    try:
        versions = client.list_page_versions(page_id, limit=50)
    except Exception as e:
        logger.warning("[auto_notify] 버전이력 조회 실패 page_id=%s: %s", page_id, e)
        return True  # fail-safe

    for v in versions:
        when_str = str(v.get("when") or "")
        try:
            when_dt = datetime.fromisoformat(when_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when_dt < today_kst:
            continue
        editor_id = str((v.get("by") or {}).get("accountId") or "")
        if editor_id and editor_id != bot_account_id:
            return True

    return False


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _collect_members_by_team() -> list[dict[str, Any]]:
    """Firestore → 팀별 {team_id, team_name, calendar_id, report_root_page_id, members[]} 목록."""
    try:
        from firestore.team_config import get_team_list, get_team_config
    except Exception as e:
        logger.warning("[auto_notify] team_config import 실패: %s", e)
        return []

    try:
        teams_info = get_team_list()
    except Exception as e:
        logger.warning("[auto_notify] team_list 로드 실패: %s", e)
        return []

    result: list[dict[str, Any]] = []
    for t in teams_info:
        team_id   = str(t.get("id") or "").strip()
        team_name = str(t.get("name") or "").strip()
        if not team_id:
            continue
        try:
            cfg = get_team_config(team_id) or {}
        except Exception as e:
            logger.warning("[auto_notify] team_config 로드 실패 team_id=%s: %s", team_id, e)
            continue

        calendar_id       = str(cfg.get("calendar_id") or "").strip()
        report_root_page_id = str(cfg.get("report_root_page_id") or "").strip()

        members: list[dict[str, str]] = []
        for m in cfg.get("team_members") or []:
            if not isinstance(m, dict):
                continue
            email = str(m.get("email") or "").strip().lower()
            if not email:
                continue
            members.append({"email": email, "name": str(m.get("name") or email).strip()})

        if not members:
            continue

        result.append({
            "team_id": team_id,
            "team_name": team_name,
            "calendar_id": calendar_id,
            "report_root_page_id": report_root_page_id,
            "members": members,
        })

    return result


def _get_this_week_page_id(report_root_page_id: str, team_name: str) -> str | None:
    """이번 주 Confluence 주간회의 페이지 ID. 없거나 실패 시 None."""
    if not report_root_page_id:
        return None
    try:
        from api.confluence.client import ConfluenceClient
        from api.confluence.previous_report import get_latest_weekly_report_page_id_for_team_root
        client = ConfluenceClient()
        return get_latest_weekly_report_page_id_for_team_root(
            client, report_root_page_id, team_name
        )
    except Exception as e:
        logger.warning("[auto_notify] Confluence 페이지 ID 조회 실패 team=%s: %s", team_name, e)
        return None


def _send_draft_to_user(
    *, user_email: str, user_display_name: str, reminder: bool = False
) -> str:
    """단일 사용자에게 초안 DM 발송 → 결과 키('sent'/'skipped_no_space'/'skipped_error')."""
    space_name = _get_space_name_by_email(user_email)
    if not space_name:
        logger.info("[auto_notify] space 없음(봇 대화 이력 없음): %s", user_email)
        return "skipped_no_space"

    try:
        from domains.weekly_report.draft import _collect_and_draft
        payload = _collect_and_draft(user_email=user_email, user_display_name=user_display_name)
    except Exception as e:
        logger.warning("[auto_notify] 초안 생성 실패 user=%s: %s", user_email, e)
        return "skipped_error"

    # cardsV2가 있는 카드 페이로드에만 안내 문구를 붙임.
    # 에러/no-data 케이스는 text만 있는 dict이므로 덮어쓰지 않음.
    if "cardsV2" in payload:
        if reminder:
            payload["text"] = "아직 주간회의 내용을 작성하지 않으셨습니다! 초안을 다시 보내드립니다."
        else:
            payload["text"] = "내일 주간회의가 있네요! 주간보고 초안을 보내드리겠습니다."

    try:
        from api.chat.messages import post_message_to_space
        ok = post_message_to_space(space_name=space_name, payload=payload)
        if ok:
            logger.info("[auto_notify] DM 발송 완료 reminder=%s: %s", reminder, user_email)
            return "sent"
        logger.warning("[auto_notify] DM 발송 응답 오류: %s", user_email)
        return "skipped_error"
    except Exception as e:
        logger.warning("[auto_notify] DM 발송 예외 user=%s: %s", user_email, e)
        return "skipped_error"


def _get_space_name_by_email(user_email: str) -> str | None:
    """Firestore conversations 에서 이메일로 space_name('spaces/XXX') 조회."""
    try:
        from firestore import get_client
        db = get_client()
        docs = list(
            db.collection(_CONVERSATIONS_COLLECTION)
            .where("user_context.email", "==", user_email)
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        data = docs[0].to_dict() or {}
        space_id = str(data.get("space_id") or "").strip()
        if not space_id:
            return None
        return space_id if space_id.startswith("spaces/") else f"spaces/{space_id}"
    except Exception as e:
        logger.warning("[auto_notify] space_id 조회 실패 user=%s: %s", user_email, e)
        return None
