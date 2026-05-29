"""Compose 카드 상태 직렬화/역직렬화."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

KST = timezone(timedelta(hours=9))
_ATT_SEP = "\x1f"
_PIPE = "|"


def empty_compose_state() -> dict[str, Any]:
    return {
        "calendar_id": "",
        "meeting_date": "",
        "meeting_time": "",
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
        "duration_minutes": 60,
        "last_reservation_id": "",
        "last_event_id": "",
        "last_api_calendar_id": "",
    }


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


def state_to_button_params(state: dict[str, Any]) -> dict[str, str]:
    return {
        "attendees_pipe": serialize_attendees(state.get("attendees") or []),
        "meet_url": str(state.get("meet_url") or ""),
        "want_meet": "1" if state.get("want_meet") or state.get("auto_meet") else "",
        "picked_room_id": str(state.get("picked_room_id") or ""),
        "picked_room_name": str(state.get("picked_room_name") or ""),
        "title": str(state.get("title") or ""),
        "equipment_keywords": ",".join(state.get("equipment_keywords") or []),
        "location_keyword": str(state.get("location_keyword") or ""),
        "room_name_keyword": str(state.get("room_name_keyword") or ""),
        "duration_minutes": str(state.get("duration_minutes") or 60),
        "last_reservation_id": str(state.get("last_reservation_id") or ""),
        "last_event_id": str(state.get("last_event_id") or ""),
        "last_api_calendar_id": str(state.get("last_api_calendar_id") or ""),
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
    state["meeting_date"] = _safe_form_value(form_inputs, "meeting_date") or parameters.get(
        "meeting_date", ""
    )
    state["meeting_time"] = _safe_form_value(form_inputs, "meeting_time") or parameters.get(
        "meeting_time", ""
    )
    state["title"] = _safe_form_value(form_inputs, "title") or parameters.get("title", "")
    state["meet_url"] = parameters.get("meet_url", state.get("meet_url", ""))
    state["want_meet"] = parameters.get("want_meet", "") in ("1", "true", "yes")
    state["last_reservation_id"] = parameters.get("last_reservation_id", "")
    state["last_event_id"] = parameters.get("last_event_id", "")
    state["last_api_calendar_id"] = parameters.get("last_api_calendar_id", "")
    state["picked_room_id"] = parameters.get("picked_room_id", "")
    state["picked_room_name"] = parameters.get("picked_room_name", "")
    pipe = parameters.get("attendees_pipe", "")
    state["attendees"] = deserialize_attendees(pipe)
    eq = parameters.get("equipment_keywords", "")
    if eq:
        state["equipment_keywords"] = [x.strip() for x in eq.split(",") if x.strip()]
    state["location_keyword"] = parameters.get("location_keyword", "")
    state["room_name_keyword"] = parameters.get("room_name_keyword", "")
    try:
        state["duration_minutes"] = int(parameters.get("duration_minutes") or 60)
    except ValueError:
        state["duration_minutes"] = 60
    return state


def format_date_korean(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.year}년 {d.month:02d}월 {d.day:02d}일"
    except ValueError:
        return date_str
