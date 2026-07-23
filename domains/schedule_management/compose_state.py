"""Compose 카드 상태 직렬화/역직렬화."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

ATTENDEE_COUNT_OPTIONS: tuple[int, ...] = (4, 8, 10, 15)
BUSINESS_HOUR_START = "08:00"
BUSINESS_HOUR_END = "19:00"
_ATT_SEP = "\x1f"
_PIPE = "|"


def empty_compose_state() -> dict[str, Any]:
    return {
        "calendar_id": "",
        "meeting_date": "",
        "meeting_time": "",
        "meeting_end_time": "",
        "duration_mode": "",
        "compose_step": "quick",
        "attendee_count": None,
        "title": "",
        "attendees": [],
        "meet_url": "",
        "want_meet": False,
        "picked_room_id": "",
        "picked_room_name": "",
        "equipment_keywords": [],
        "location_keyword": "",
        "room_name_keyword": "",
        "auto_meet": False,
        "pending_candidates": [],
        "errors": [],
        "duration_minutes": 0,
        "last_reservation_id": "",
        "last_event_id": "",
        "last_api_calendar_id": "",
        "ignore_conflict": False,
    }


def pick_attendee_count_option(headcount: int) -> int | None:
    """ATTENDEE_COUNT_OPTIONS 중 headcount 이상 최소값 (예: 2명 → 4)."""
    n = max(int(headcount), 1)
    for option in ATTENDEE_COUNT_OPTIONS:
        if option >= n:
            return option
    return None


def sync_attendee_count_from_headcount(state: dict[str, Any]) -> None:
    """attendee_count 미설정 시 참석자 수로 인원 버튼 값 자동 선택."""
    if state.get("attendee_count") is not None:
        return
    n = len(state.get("attendees") or [])
    if n <= 0:
        return
    picked = pick_attendee_count_option(n)
    if picked is not None:
        state["attendee_count"] = picked


def ready_for_quick_room_preview(state: dict[str, Any]) -> bool:
    if str(state.get("compose_step") or "quick") != "quick":
        return True
    if not str(state.get("meeting_date") or "").strip():
        return False
    if not str(state.get("meeting_time") or "").strip():
        return False
    if state.get("attendee_count") is None:
        return False
    if str(state.get("duration_mode") or "") == "custom":
        if not str(state.get("meeting_end_time") or "").strip():
            return False
    return True


def serialize_attendees(attendees: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for a in attendees or []:
        name = str(a.get("name") or "").strip()
        email = str(a.get("email") or "").strip()
        if not email and not name:
            continue
        parts.append(f"{name}{_ATT_SEP}{email}")
    return _PIPE.join(parts)


def deserialize_attendees(pipe: str) -> list[dict[str, str]]:
    if not pipe:
        return []
    out: list[dict[str, str]] = []
    for chunk in pipe.split(_PIPE):
        if not chunk:
            continue
        if _ATT_SEP in chunk:
            name, email = chunk.split(_ATT_SEP, 1)
        else:
            name, email = "", chunk
        out.append({"name": name.strip(), "email": email.strip()})
    return out


def attendee_emails_for_event(state: dict[str, Any], booker_email: str) -> list[str]:
    """이벤트 참석자 이메일 — 예약자 본인을 항상 포함."""
    emails: list[str] = []
    seen: set[str] = set()
    booker = str(booker_email or "").strip()
    if booker:
        emails.append(booker)
        seen.add(booker.lower())
    for a in state.get("attendees") or []:
        e = str(a.get("email") or "").strip()
        if e and e.lower() not in seen:
            emails.append(e)
            seen.add(e.lower())
    return emails


def state_to_button_params(state: dict[str, Any]) -> dict[str, str]:
    ac = state.get("attendee_count")
    return {
        "compose_step": str(state.get("compose_step") or "quick"),
        "duration_mode": str(state.get("duration_mode") or ""),
        "meeting_date": str(state.get("meeting_date") or ""),
        "meeting_time": str(state.get("meeting_time") or ""),
        "meeting_end_time": str(state.get("meeting_end_time") or ""),
        "attendee_count": str(ac) if ac is not None else "",
        "calendar_id": str(state.get("calendar_id") or ""),
        "attendees_pipe": serialize_attendees(state.get("attendees") or []),
        "meet_url": str(state.get("meet_url") or ""),
        "want_meet": "1" if state.get("want_meet") or state.get("auto_meet") else "",
        "picked_room_id": str(state.get("picked_room_id") or ""),
        "picked_room_name": str(state.get("picked_room_name") or ""),
        "title": str(state.get("title") or ""),
        "equipment_keywords": ",".join(state.get("equipment_keywords") or []),
        "location_keyword": str(state.get("location_keyword") or ""),
        "room_name_keyword": str(state.get("room_name_keyword") or ""),
        "duration_minutes": str(state.get("duration_minutes") or ""),
        "last_reservation_id": str(state.get("last_reservation_id") or ""),
        "last_event_id": str(state.get("last_event_id") or ""),
        "last_api_calendar_id": str(state.get("last_api_calendar_id") or ""),
        "ignore_conflict": "1" if state.get("ignore_conflict") else "",
    }


def _safe_form_value(form_inputs: dict[str, Any], key: str) -> str:
    raw = form_inputs.get(key)
    if isinstance(raw, dict):
        string_inputs = raw.get("stringInputs") or {}
        values = string_inputs.get("value") or []
        if isinstance(values, list) and values:
            return str(values[0]).strip()
        date_input = raw.get("dateInput") or {}
        ms = date_input.get("msSinceEpoch")
        if ms is not None:
            try:
                # DATE_ONLY(timezoneOffsetDate=KST 피커): 선택일 00:00 KST의 epoch ms가 온다.
                # UTC 00:00이 오더라도 KST 해석은 같은 날짜(09:00 KST)라 안전.
                dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=KST)
                return dt.strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
    if raw is None:
        return ""
    return str(raw).strip()


def compose_state_from(
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
) -> dict[str, Any]:
    state = empty_compose_state()
    state["calendar_id"] = _safe_form_value(form_inputs, "calendar_id") or parameters.get(
        "calendar_id", ""
    )
    if "meeting_date" in parameters and not _safe_form_value(form_inputs, "meeting_date"):

        state["meeting_date"] = str(parameters.get("meeting_date") or "")

    else:

        state["meeting_date"] = _safe_form_value(form_inputs, "meeting_date") or parameters.get(

            "meeting_date", ""

        )
    state["meeting_time"] = _safe_form_value(form_inputs, "meeting_time") or parameters.get(
        "meeting_time", ""
    )
    state["meeting_end_time"] = _safe_form_value(form_inputs, "meeting_end_time") or parameters.get(
        "meeting_end_time", ""
    )
    state["duration_mode"] = (
        _safe_form_value(form_inputs, "duration_mode")
        or parameters.get("duration_mode", "")
    )
    state["compose_step"] = parameters.get("compose_step", "") or "quick"
    ac_raw = _safe_form_value(form_inputs, "attendee_count") or parameters.get("attendee_count", "")
    if ac_raw:
        try:
            state["attendee_count"] = max(int(ac_raw), 1)
        except ValueError:
            state["attendee_count"] = None
    state["title"] = _safe_form_value(form_inputs, "title") or parameters.get("title", "")
    state["meet_url"] = parameters.get("meet_url", state.get("meet_url", ""))
    state["want_meet"] = parameters.get("want_meet", "") in ("1", "true", "yes")
    state["last_reservation_id"] = parameters.get("last_reservation_id", "")
    state["last_event_id"] = parameters.get("last_event_id", "")
    state["last_api_calendar_id"] = parameters.get("last_api_calendar_id", "")
    state["ignore_conflict"] = parameters.get("ignore_conflict", "") in ("1", "true", "yes")
    state["picked_room_id"] = parameters.get("picked_room_id", "")
    state["picked_room_name"] = parameters.get("picked_room_name", "")
    pipe = parameters.get("attendees_pipe", "")
    state["attendees"] = deserialize_attendees(pipe)
    eq = parameters.get("equipment_keywords", "")
    if eq:
        state["equipment_keywords"] = [x.strip() for x in eq.split(",") if x.strip()]
    state["location_keyword"] = parameters.get("location_keyword", "")
    state["room_name_keyword"] = parameters.get("room_name_keyword", "")
    dm_raw = parameters.get("duration_minutes", "")
    if dm_raw:
        try:
            state["duration_minutes"] = int(dm_raw)
        except ValueError:
            state["duration_minutes"] = 0
    apply_duration_mode(state)
    return state


def apply_duration_mode(state: dict[str, Any]) -> None:
    """duration_mode에 따라 duration_minutes를 동기화."""
    mode = str(state.get("duration_mode") or "").strip()
    if not mode:
        return
    if mode == "1h":
        state["duration_minutes"] = 60
    elif mode == "2h":
        state["duration_minutes"] = 120
    elif mode == "custom":
        date = str(state.get("meeting_date") or "").strip()
        start = str(state.get("meeting_time") or "").strip()
        end = str(state.get("meeting_end_time") or "").strip()
        if date and start and end:
            try:
                start_dt = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M")
                end_dt = datetime.strptime(f"{date} {end}", "%Y-%m-%d %H:%M")
                diff = int((end_dt - start_dt).total_seconds() / 60)
                if diff > 0:
                    state["duration_minutes"] = diff
            except ValueError:
                pass


def resolve_end_time(state: dict[str, Any]) -> str:
    """표시·예약용 종료 시각(HH:MM)."""
    mode = str(state.get("duration_mode") or "").strip()
    date = str(state.get("meeting_date") or "").strip()
    start = str(state.get("meeting_time") or "").strip()
    if not mode:
        return ""
    if mode == "custom":
        return str(state.get("meeting_end_time") or "").strip()
    if not date or not start:
        return ""
    duration = int(state.get("duration_minutes") or 60)
    try:
        start_dt = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=max(duration, 10))
        return end_dt.strftime("%H:%M")
    except ValueError:
        return ""


def format_date_korean(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.year}년 {d.month:02d}월 {d.day:02d}일"
    except ValueError:
        return date_str
