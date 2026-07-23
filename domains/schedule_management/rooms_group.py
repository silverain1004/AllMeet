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


def format_group_booking_summary(
    bookings: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    *,
    time_min_iso: str,
    time_max_iso: str,
    max_lines: int = 3,
    room_busy: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    """선택 구간의 회의실 예약 현황 한 줄 요약.

    집계 캘린더 이벤트뿐 아니라 ``room_busy`` (회의실 리소스 캘린더 freebusy) 로만
    확인되는 직접 예약도 포함한다. 예전에는 집계 캘린더만 읽어서, 카드에 "사용 중"
    으로 표시된 회의실이 요약에는 빠지는 불일치가 있었다.
    """
    entries: list[str] = []
    covered: set[str] = set()

    for event in bookings:
        start = str(event.get("start") or "")
        end = str(event.get("end") or "")
        if not _interval_overlaps(start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso):
            continue
        when = _range_label(start, end)
        summary = str(event.get("summary") or "(제목 없음)")
        matched = match_booking_to_rooms(event, rooms)
        if not matched:
            entries.append(f"{str(event.get('location') or '회의실')} {when} {summary}")
            continue
        # 한 회의가 회의실 여러 개를 잡았으면 회의실 수만큼 줄이 나가야 카드의 빨간불 개수와 맞는다.
        for room in matched:
            rid = str(room.get("calendar_resource_id") or "").strip()
            if rid:
                covered.add(rid)
            entries.append(f"{str(room.get('name') or '회의실')} {when} {summary}")

    for room in rooms:
        rid = str(room.get("calendar_resource_id") or "").strip()
        if not rid or rid in covered:
            continue
        for span in (room_busy or {}).get(rid) or []:
            start = str(span.get("start") or "")
            end = str(span.get("end") or "")
            if not _interval_overlaps(
                start, end, time_min_iso=time_min_iso, time_max_iso=time_max_iso
            ):
                continue
            covered.add(rid)
            when = _range_label(start, end)
            # freebusy 는 제목을 주지 않는다 — 집계 캘린더에 없는 직접 예약.
            entries.append(f"{str(room.get('name') or '회의실')} {when} (예약됨)")

    if not entries:
        return ""
    lines = entries[:max_lines]
    extra = len(entries) - len(lines)
    suffix = f" 외 {extra}건" if extra > 0 else ""
    return "군산 예약 현황: " + " | ".join(lines) + suffix
