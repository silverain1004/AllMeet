"""간편예약 compose 화면용 Calendar 스냅샷 — API 중복 제거·병렬 조회."""

from __future__ import annotations

import threading
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

# 조각별 캐시 — 예전에는 스냅샷 전체를 (시간대 + 참석자 + 회의실) 한 키로 묶어서,
# 참석자를 한 명 추가하면 참석자와 무관한 회의실·그룹 예약까지 전부 재조회됐다.
# 참석자 목록에 의존하는 조각만 따로 떼어 캐시 miss 범위를 좁힌다.
_part_cache: dict[str, dict[tuple[Any, ...], tuple[float, Any]]] = {
    "room_busy": {},
    "people_busy": {},
    "group_bookings": {},
    "booker_events": {},
}
_part_lock = threading.Lock()


def _part_get(part: str, key: tuple[Any, ...]) -> tuple[bool, Any]:
    with _part_lock:
        cached = _part_cache[part].get(key)
    if cached and (time.monotonic() - cached[0]) < _SNAPSHOT_TTL_SEC:
        return True, cached[1]
    return False, None


def _part_put(part: str, key: tuple[Any, ...], value: Any) -> None:
    with _part_lock:
        _part_cache[part][key] = (time.monotonic(), value)


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

    group_enabled = bool(get_room_calendar_config().get("group_calendar_id"))
    room_key = (start_iso, end_iso, tuple(sorted(resource_ids)))
    people_key = (start_iso, end_iso, tuple(sorted(e.lower() for e in people_emails)))
    group_key = (start_iso, end_iso)
    booker_key = (start_iso, end_iso, api_calendar_id or "primary")

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
        if not group_enabled:
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

    # (조각 이름, 필요 여부, 캐시 키, 조회 함수)
    parts: list[tuple[str, bool, tuple[Any, ...], Any]] = [
        ("room_busy", bool(resource_ids), room_key, _fetch_room_busy),
        ("people_busy", bool(people_emails), people_key, _fetch_people_busy),
        ("group_bookings", group_enabled, group_key, _fetch_group_bookings),
        ("booker_events", fetch_booker_events, booker_key, _fetch_booker_events),
    ]

    resolved: dict[str, Any] = {}
    pending: list[tuple[str, tuple[Any, ...], Any]] = []
    for name, needed, key, fn in parts:
        if not needed:
            continue
        if use_cache:
            hit, value = _part_get(name, key)
            if hit:
                resolved[name] = value
                continue
        pending.append((name, key, fn))

    if pending:
        with ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {name: (key, executor.submit(fn)) for name, key, fn in pending}
            for name, (key, future) in futures.items():
                try:
                    value = future.result()
                except Exception:
                    continue
                resolved[name] = value
                if use_cache:
                    _part_put(name, key, value)

    if isinstance(resolved.get("room_busy"), dict):
        room_busy = resolved["room_busy"]
    if isinstance(resolved.get("people_busy"), set):
        attendee_busy_emails = resolved["people_busy"]
    if isinstance(resolved.get("group_bookings"), list):
        group_bookings = resolved["group_bookings"]
    if isinstance(resolved.get("booker_events"), list):
        booker_events = resolved["booker_events"]

    return ComposeCalendarSnapshot(
        start_iso=start_iso,
        end_iso=end_iso,
        room_busy=room_busy,
        attendee_busy_emails=attendee_busy_emails,
        group_bookings=group_bookings,
        booker_events=booker_events,
    )


def clear_snapshot_cache() -> None:
    with _part_lock:
        for bucket in _part_cache.values():
            bucket.clear()
