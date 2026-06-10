"""간편예약 compose 화면용 Calendar 스냅샷 — API 중복 제거·병렬 조회."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from domains.schedule_management.calendar_client import KST, freebusy_query, list_events, to_kst_iso
from domains.schedule_management.room_calendar_store import get_room_calendar_config
from domains.schedule_management.rooms import (
    _availability_duration_minutes,
    _busy_emails_from_freebusy,
    ordered_rooms_for_state,
)
from domains.schedule_management.rooms_group import list_group_room_bookings

FREE_BUSY_ROOM_CANDIDATE_LIMIT = 8
_SNAPSHOT_TTL_SEC = 45
_snapshot_cache: dict[tuple[Any, ...], tuple[float, ComposeCalendarSnapshot]] = {}


@dataclass
class ComposeCalendarSnapshot:
    start_iso: str
    end_iso: str
    room_busy: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    attendee_busy_emails: set[str] = field(default_factory=set)
    group_bookings: list[dict[str, Any]] = field(default_factory=list)
    booker_events: list[dict[str, Any]] = field(default_factory=list)


def _slot_iso_bounds(
    state: dict[str, Any],
    *,
    duration_minutes: int = 60,
) -> tuple[str, str] | None:
    date = str(state.get("meeting_date") or "").strip()
    time_str = str(state.get("meeting_time") or "").strip()
    duration_mode = str(state.get("duration_mode") or "").strip()
    if not date or not time_str:
        return None
    if duration_mode == "custom" and not str(state.get("meeting_end_time") or "").strip():
        return None
    try:
        start_iso = to_kst_iso(date, time_str)
        eff_duration = _availability_duration_minutes(state, duration_minutes)
        if duration_mode == "custom":
            end_time = str(state.get("meeting_end_time") or "").strip()
            end_iso = to_kst_iso(date, end_time)
        else:
            end_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M") + timedelta(
                minutes=eff_duration
            )
            end_iso = end_dt.replace(tzinfo=KST).isoformat()
        return start_iso, end_iso
    except ValueError:
        return None


def can_fetch_compose_snapshot(state: dict[str, Any]) -> bool:
    return _slot_iso_bounds(state) is not None


def _people_emails(state: dict[str, Any], booker_email: str) -> list[str]:
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


def _cache_key(
    *,
    start_iso: str,
    end_iso: str,
    people_emails: list[str],
    resource_ids: list[str],
    fetch_booker_events: bool,
) -> tuple[Any, ...]:
    return (
        start_iso,
        end_iso,
        tuple(sorted(people_emails)),
        tuple(sorted(resource_ids)),
        fetch_booker_events,
    )


def fetch_compose_snapshot(
    state: dict[str, Any],
    *,
    access_token: str | None,
    rooms: list[dict[str, Any]],
    api_calendar_id: str = "",
    booker_email: str = "",
    fetch_booker_events: bool = False,
    room_candidate_limit: int = FREE_BUSY_ROOM_CANDIDATE_LIMIT,
    duration_minutes: int = 60,
    use_cache: bool = True,
) -> ComposeCalendarSnapshot | None:
    bounds = _slot_iso_bounds(state, duration_minutes=duration_minutes)
    if not bounds or not access_token:
        return None
    start_iso, end_iso = bounds

    ordered = ordered_rooms_for_state(state, rooms)
    resource_ids = [
        str(r.get("calendar_resource_id") or "").strip()
        for r in ordered[:room_candidate_limit]
        if str(r.get("calendar_resource_id") or "").strip()
    ]
    people_emails = _people_emails(state, booker_email)

    cache_key = _cache_key(
        start_iso=start_iso,
        end_iso=end_iso,
        people_emails=people_emails,
        resource_ids=resource_ids,
        fetch_booker_events=fetch_booker_events,
    )
    if use_cache:
        cached = _snapshot_cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _SNAPSHOT_TTL_SEC:
            return cached[1]

    room_busy: dict[str, list[dict[str, str]]] = {}
    attendee_busy_emails: set[str] = set()
    group_bookings: list[dict[str, Any]] = []
    booker_events: list[dict[str, Any]] = []

    def _fetch_room_busy() -> dict[str, list[dict[str, str]]]:
        if not resource_ids:
            return {}
        result = freebusy_query(
            calendar_ids=resource_ids,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        return result.busy if result.ok else {}

    def _fetch_people_busy() -> set[str]:
        if not people_emails:
            return set()
        result = freebusy_query(
            calendar_ids=people_emails,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        if not result.ok:
            return set()
        return _busy_emails_from_freebusy(
            result.busy,
            attendee_emails=people_emails,
            time_min_iso=start_iso,
            time_max_iso=end_iso,
        )

    def _fetch_group_bookings() -> list[dict[str, Any]]:
        if not get_room_calendar_config().get("group_calendar_id"):
            return []
        return list_group_room_bookings(
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )

    def _fetch_booker_events() -> list[dict[str, Any]]:
        if not fetch_booker_events:
            return []
        booker_cal = api_calendar_id or "primary"
        result = list_events(
            calendar_id=booker_cal,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        return list(result.events or []) if result.ok else []

    futures: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        if resource_ids:
            futures["room_busy"] = executor.submit(_fetch_room_busy)
        if people_emails:
            futures["people_busy"] = executor.submit(_fetch_people_busy)
        if get_room_calendar_config().get("group_calendar_id"):
            futures["group_bookings"] = executor.submit(_fetch_group_bookings)
        if fetch_booker_events:
            futures["booker_events"] = executor.submit(_fetch_booker_events)

        for name, future in futures.items():
            try:
                value = future.result()
            except Exception:
                value = None
            if name == "room_busy" and isinstance(value, dict):
                room_busy = value
            elif name == "people_busy" and isinstance(value, set):
                attendee_busy_emails = value
            elif name == "group_bookings" and isinstance(value, list):
                group_bookings = value
            elif name == "booker_events" and isinstance(value, list):
                booker_events = value

    snapshot = ComposeCalendarSnapshot(
        start_iso=start_iso,
        end_iso=end_iso,
        room_busy=room_busy,
        attendee_busy_emails=attendee_busy_emails,
        group_bookings=group_bookings,
        booker_events=booker_events,
    )
    if use_cache:
        _snapshot_cache[cache_key] = (time.monotonic(), snapshot)
    return snapshot


def clear_snapshot_cache() -> None:
    _snapshot_cache.clear()
