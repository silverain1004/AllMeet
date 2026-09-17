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


def match_booking_to_rooms(
    event: dict[str, Any],
    rooms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """이벤트가 잡은 회의실 전부. 한 회의가 회의실 여러 개를 잡을 수 있다."""
    room_by_id = {
        str(r.get("calendar_resource_id") or "").strip(): r
        for r in rooms
        if str(r.get("calendar_resource_id") or "").strip()
    }
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for att in event.get("attendees") or []:
        if not isinstance(att, dict) or not att.get("resource"):
            continue
        email = str(att.get("email") or "").strip()
        if email in room_by_id and email not in seen:
            matched.append(room_by_id[email])
            seen.add(email)
    if matched:
        return matched
    location = str(event.get("location") or "").lower()
    summary = str(event.get("summary") or "").lower()
    for room in rooms:
        name = str(room.get("name") or "").lower()
        if name and (name in location or name in summary):
            rid = str(room.get("calendar_resource_id") or "").strip()
            if rid and rid in seen:
                continue
            matched.append(room)
            if rid:
                seen.add(rid)
    return matched


def match_booking_to_room(
    event: dict[str, Any],
    rooms: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """첫 번째로 매칭된 회의실. 전부 필요하면 ``match_booking_to_rooms`` 를 쓴다."""
    matched = match_booking_to_rooms(event, rooms)
    return matched[0] if matched else None


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
        for matched in match_booking_to_rooms(event, rooms):
            rid = str(matched.get("calendar_resource_id") or "").strip()
            if rid:
                busy.add(rid)
    return busy


def _range_label(start_raw: str, end_raw: str) -> str:
    try:
        s = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(KST)
        e = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return ""
    return f"{s.strftime('%H:%M')}~{e.strftime('%H:%M')}"


def build_room_booking_reasons(
    bookings: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    *,
    time_min_iso: str,
    time_max_iso: str,
    room_busy: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, str]:
    """회의실별 "왜 막혔는지" 한 줄 — 카드의 회의실 행에 그대로 붙는다.

    집계 캘린더 예약은 제목까지 알 수 있고, 리소스 캘린더 freebusy 로만 확인되는
    직접 예약은 시간만 알 수 있다. 한 회의실에 여러 건이면 첫 건만 쓰고 접는다.
    """
    entries: dict[str, list[str]] = {}

    for event in bookings:
        start = str(event.get("start") or "")
        end = str(event.get("end") or "")
        if not _interval_overlaps(start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso):
            continue
        when = _range_label(start, end)
        summary = str(event.get("summary") or "").strip() or "(제목 없음)"
        for room in match_booking_to_rooms(event, rooms):
            rid = str(room.get("calendar_resource_id") or "").strip()
            if rid:
                entries.setdefault(rid, []).append(f"{when} {summary}")

    for room in rooms:
        rid = str(room.get("calendar_resource_id") or "").strip()
        # 집계 캘린더로 이미 제목까지 안 회의실은 freebusy 를 겹쳐 세지 않는다.
        if not rid or rid in entries:
            continue
        for span in (room_busy or {}).get(rid) or []:
            start = str(span.get("start") or "")
            end = str(span.get("end") or "")
            if not _interval_overlaps(
                start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso
            ):
                continue
            entries.setdefault(rid, []).append(f"{_range_label(start, end)} (예약됨)")

    reasons: dict[str, str] = {}
    for rid, lines in entries.items():
        extra = len(lines) - 1
        reasons[rid] = lines[0] + (f" 외 {extra}건" if extra > 0 else "")
    return reasons
