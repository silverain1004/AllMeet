"""군산 회의실 예약 집계 캘린더 조회."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from domains.schedule_management.calendar_client import KST, list_events
from domains.schedule_management.room_calendar_store import get_room_calendar_config


def list_group_room_bookings(
    *,
    time_min: str,
    time_max: str,
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    config = get_room_calendar_config()
    group_id = str(config.get("group_calendar_id") or "").strip()
    if not group_id:
        return []
    result = list_events(
        calendar_id=group_id,
        time_min=time_min,
        time_max=time_max,
        max_results=50,
        access_token=access_token,
    )
    if not result.ok:
        return []
    return list(result.events)


def _interval_overlaps(
    start_raw: str,
    end_raw: str,
    *,
    time_min_iso: str,
    time_max_iso: str,
) -> bool:
    try:
        t0 = datetime.fromisoformat(time_min_iso).astimezone(KST)
        t1 = datetime.fromisoformat(time_max_iso).astimezone(KST)
        s = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(KST)
        e = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return False
    return s < t1 and e > t0


def match_booking_to_room(
    event: dict[str, Any],
    rooms: list[dict[str, Any]],
) -> dict[str, Any] | None:
    room_by_id = {
        str(r.get("calendar_resource_id") or "").strip(): r
        for r in rooms
        if str(r.get("calendar_resource_id") or "").strip()
    }
    for att in event.get("attendees") or []:
        if not isinstance(att, dict) or not att.get("resource"):
            continue
        email = str(att.get("email") or "").strip()
        if email in room_by_id:
            return room_by_id[email]
    location = str(event.get("location") or "").lower()
    summary = str(event.get("summary") or "").lower()
    for room in rooms:
        name = str(room.get("name") or "").lower()
        if name and (name in location or name in summary):
            return room
    return None


def busy_resource_ids_from_group_bookings(
    bookings: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    *,
    time_min_iso: str,
    time_max_iso: str,
) -> set[str]:
    busy: set[str] = set()
    for event in bookings:
        start = str(event.get("start") or "")
        end = str(event.get("end") or "")
        if not start or not end:
            continue
        if not _interval_overlaps(start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso):
            continue
        matched = match_booking_to_room(event, rooms)
        if matched:
            rid = str(matched.get("calendar_resource_id") or "").strip()
            if rid:
                busy.add(rid)
    return busy


def format_group_booking_summary(
    bookings: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    *,
    time_min_iso: str,
    time_max_iso: str,
    max_lines: int = 3,
) -> str:
    lines: list[str] = []
    for event in bookings:
        start = str(event.get("start") or "")
        end = str(event.get("end") or "")
        if not _interval_overlaps(start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso):
            continue
        room = match_booking_to_room(event, rooms)
        room_name = str((room or {}).get("name") or event.get("location") or "회의실")
        summary = str(event.get("summary") or "(제목 없음)")
        try:
            s = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(KST)
            e = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(KST)
            when = f"{s.strftime('%H:%M')}~{e.strftime('%H:%M')}"
        except ValueError:
            when = ""
        lines.append(f"{room_name} {when} {summary}")
        if len(lines) >= max_lines:
            break
    if not lines:
        return ""
    extra = len(bookings) - len(lines)
    suffix = f" 외 {extra}건" if extra > 0 else ""
    return "군산 예약 현황: " + " | ".join(lines) + suffix
