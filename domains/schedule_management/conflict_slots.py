"""일정 충돌 감지 및 대안 시간 슬롯 제안."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from domains.schedule_management.calendar_client import KST, freebusy_query, list_events, to_kst_iso
from domains.schedule_management.compose_availability import ComposeCalendarSnapshot
from domains.schedule_management.compose_state import (
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
    apply_duration_mode,
    resolve_end_time,
)
from domains.schedule_management.rooms import (
    _attendee_count_for_state,
    _busy_emails_from_freebusy,
    _sort_rooms_for_recommendation,
    ordered_rooms_for_state,
)
from domains.schedule_management.room_calendar_store import get_room_calendar_config
from domains.schedule_management.rooms_group import (
    busy_resource_ids_from_group_bookings,
    list_group_room_bookings,
)
from domains.schedule_management.rooms_store import get_rooms

# Google freeBusy 는 요청당 items 50개 제한.
_FREEBUSY_ITEM_LIMIT = 50


@dataclass
class ConflictInfo:
    kind: str  # booker | attendee
    label: str
    event_summary: str
    start_iso: str
    end_iso: str
    html_link: str
    display_time: str = ""


@dataclass
class SlotSuggestion:
    meeting_date: str
    meeting_time: str
    meeting_end_time: str
    free_room_count: int
    top_room_name: str


@dataclass
class ConflictCheckResult:
    has_conflict: bool = False
    conflicts: list[ConflictInfo] = field(default_factory=list)
    alternatives: list[SlotSuggestion] = field(default_factory=list)
    requested_time: str = ""


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _minutes_to_hhmm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def _interval_overlaps(
    start_iso: str,
    end_iso: str,
    other_start: str,
    other_end: str,
) -> bool:
    try:
        t0 = datetime.fromisoformat(start_iso).astimezone(KST)
        t1 = datetime.fromisoformat(end_iso).astimezone(KST)
        s = datetime.fromisoformat(other_start.replace("Z", "+00:00")).astimezone(KST)
        e = datetime.fromisoformat(other_end.replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return False
    return s < t1 and e > t0


def _display_time_range(start_iso: str, end_iso: str) -> str:
    try:
        s = datetime.fromisoformat(start_iso).astimezone(KST)
        e = datetime.fromisoformat(end_iso).astimezone(KST)
        return f"{s.strftime('%H:%M')}~{e.strftime('%H:%M')}"
    except ValueError:
        return ""


def _slot_bounds(state: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    date = str(state.get("meeting_date") or "").strip()
    time_str = str(state.get("meeting_time") or "").strip()
    if not date or not time_str:
        return None
    work = dict(state)
    if not str(work.get("duration_mode") or "").strip():
        work["duration_mode"] = "1h"
        work["duration_minutes"] = 60
    apply_duration_mode(work)
    end_time = resolve_end_time(work)
    if not end_time:
        try:
            duration = int(work.get("duration_minutes") or 60)
            start_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            end_time = (start_dt + timedelta(minutes=max(duration, 10))).strftime("%H:%M")
        except ValueError:
            return None
    try:
        start_iso = to_kst_iso(date, time_str)
        end_iso = to_kst_iso(date, end_time)
    except ValueError:
        return None
    return start_iso, end_iso, date, time_str, end_time


def _attendee_emails(state: dict[str, Any]) -> list[str]:
    return [
        str(a.get("email") or "").strip()
        for a in (state.get("attendees") or [])
        if str(a.get("email") or "").strip()
    ]


def _attendee_label(email: str, state: dict[str, Any]) -> str:
    for a in state.get("attendees") or []:
        if str(a.get("email") or "").strip().lower() == email.lower():
            name = str(a.get("name") or "").strip()
            return name or email
    return email


def _conflicts_from_events(
    events: list[dict[str, Any]],
    *,
    start_iso: str,
    end_iso: str,
    kind: str,
    label: str,
) -> list[ConflictInfo]:
    out: list[ConflictInfo] = []
    for ev in events:
        ev_start = str(ev.get("start") or "")
        ev_end = str(ev.get("end") or "")
        if not ev_start or not ev_end or "T" not in ev_start:
            continue
        if not _interval_overlaps(start_iso, end_iso, ev_start, ev_end):
            continue
        summary = str(ev.get("summary") or "").strip() or "(제목 없음)"
        out.append(
            ConflictInfo(
                kind=kind,
                label=label,
                event_summary=summary,
                start_iso=ev_start,
                end_iso=ev_end,
                html_link=str(ev.get("html_link") or ""),
                display_time=_display_time_range(ev_start, ev_end),
            )
        )
    return out


def _light_busy_conflict(
    *,
    kind: str,
    label: str,
    start_iso: str,
    end_iso: str,
) -> ConflictInfo:
    return ConflictInfo(
        kind=kind,
        label=label,
        event_summary="(일정 있음)",
        start_iso=start_iso,
        end_iso=end_iso,
        html_link="",
        display_time="",
    )


def _conflicts_for_slot(
    state: dict[str, Any],
    *,
    start_iso: str,
    end_iso: str,
    access_token: str,
    user_email: str,
    user_name: str,
    api_calendar_id: str,
    snapshot: ComposeCalendarSnapshot | None = None,
    mode: str = "full",
) -> list[ConflictInfo]:
    conflicts: list[ConflictInfo] = []
    booker_cal = api_calendar_id or "primary"
    booker_email = str(user_email or "").strip().lower()
    attendee_emails = _attendee_emails(state)

    if mode == "light" and snapshot:
        if booker_email and booker_email in {e.lower() for e in snapshot.attendee_busy_emails}:
            conflicts.append(
                _light_busy_conflict(
                    kind="booker",
                    label=user_name or "나",
                    start_iso=start_iso,
                    end_iso=end_iso,
                )
            )
        attendee_set = {e.lower() for e in attendee_emails}
        for email in snapshot.attendee_busy_emails:
            el = email.lower()
            if booker_email and el == booker_email:
                continue
            if el not in attendee_set:
                continue
            conflicts.append(
                _light_busy_conflict(
                    kind="attendee",
                    label=_attendee_label(email, state),
                    start_iso=start_iso,
                    end_iso=end_iso,
                )
            )
        return conflicts

    booker_events: list[dict[str, Any]] = []
    if snapshot and snapshot.booker_events:
        booker_events = snapshot.booker_events
    else:
        booker_result = list_events(
            calendar_id=booker_cal,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        if booker_result.ok:
            booker_events = list(booker_result.events or [])
    if booker_events:
        conflicts.extend(
            _conflicts_from_events(
                booker_events,
                start_iso=start_iso,
                end_iso=end_iso,
                kind="booker",
                label=user_name or "나",
            )
        )

    if not attendee_emails:
        return conflicts

    busy_emails: set[str]
    if snapshot:
        busy_emails = {
            e
            for e in snapshot.attendee_busy_emails
            if e in attendee_emails
        }
    else:
        fb = freebusy_query(
            calendar_ids=attendee_emails,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        if not fb.ok:
            return conflicts
        busy_emails = _busy_emails_from_freebusy(
            fb.busy,
            attendee_emails=attendee_emails,
            time_min_iso=start_iso,
            time_max_iso=end_iso,
        )

    # 바쁜 참석자별 일정 제목 조회 — 직렬로 돌면 참석자 수에 비례해 느려져 병렬로 친다.
    busy_list = sorted(busy_emails)
    if not busy_list:
        return conflicts

    def _detail(email: str) -> list[dict[str, Any]]:
        result = list_events(
            calendar_id=email,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        return list(result.events or []) if result.ok else []

    events_by_email: dict[str, list[dict[str, Any]]] = {}
    if len(busy_list) == 1:
        events_by_email[busy_list[0]] = _detail(busy_list[0])
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(busy_list))) as executor:
            futures = {email: executor.submit(_detail, email) for email in busy_list}
            for email, future in futures.items():
                try:
                    events_by_email[email] = future.result()
                except Exception:
                    events_by_email[email] = []

    for email in busy_list:
        label = _attendee_label(email, state)
        found = _conflicts_from_events(
            events_by_email.get(email) or [],
            start_iso=start_iso,
            end_iso=end_iso,
            kind="attendee",
            label=label,
        )
        if found:
            conflicts.extend(found)
        else:
            conflicts.append(
                _light_busy_conflict(
                    kind="attendee",
                    label=label,
                    start_iso=start_iso,
                    end_iso=end_iso,
                )
            )
    return conflicts


def _candidate_start_times(
    date: str,
    requested_time: str,
    duration_minutes: int,
) -> list[str]:
    biz_start = _hhmm_to_minutes(BUSINESS_HOUR_START)
    biz_end = _hhmm_to_minutes(BUSINESS_HOUR_END)
    last_start = biz_end - duration_minutes
    if last_start < biz_start:
        return []
    req_min = _hhmm_to_minutes(requested_time) if requested_time else biz_start
    candidates: list[str] = []
    cursor = biz_start
    while cursor <= last_start:
        candidates.append(_minutes_to_hhmm(cursor))
        cursor += 30
    candidates.sort(key=lambda t: abs(_hhmm_to_minutes(t) - req_min))
    return candidates


def _busy_ranges(spans: list[dict[str, str]] | None) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for span in spans or []:
        try:
            s = datetime.fromisoformat(
                str(span.get("start") or "").replace("Z", "+00:00")
            ).astimezone(KST)
            e = datetime.fromisoformat(
                str(span.get("end") or "").replace("Z", "+00:00")
            ).astimezone(KST)
        except ValueError:
            continue
        out.append((s, e))
    return out


def _ranges_overlap(ranges: list[tuple[datetime, datetime]], t0: datetime, t1: datetime) -> bool:
    return any(s < t1 and e > t0 for s, e in ranges)


def _freebusy_chunked(
    calendar_ids: list[str],
    *,
    time_min: str,
    time_max: str,
    access_token: str | None,
) -> dict[str, list[dict[str, str]]]:
    """freeBusy items 50개 제한을 넘기면 나눠 조회 후 병합."""
    busy: dict[str, list[dict[str, str]]] = {}
    for i in range(0, len(calendar_ids), _FREEBUSY_ITEM_LIMIT):
        chunk = calendar_ids[i : i + _FREEBUSY_ITEM_LIMIT]
        result = freebusy_query(
            calendar_ids=chunk,
            time_min=time_min,
            time_max=time_max,
            access_token=access_token,
        )
        if result.ok:
            busy.update(result.busy)
    return busy


def suggest_alternative_slots(
    state: dict[str, Any],
    *,
    access_token: str,
    user_email: str,
    user_name: str,
    api_calendar_id: str,
    max_n: int = 3,
    rooms: list[dict[str, Any]] | None = None,
) -> list[SlotSuggestion]:
    """대안 시간 후보를 하루치 조회 1회분으로 판정.

    예전에는 후보 시각마다 `_conflicts_for_slot` + 회의실 freebusy 를 직렬로 쳐서
    후보 하나당 2~4회 왕복, 최악엔 수십 회가 순차로 쌓였다. 업무시간 전체를 한 번에
    받아 슬롯 판정은 메모리에서 하도록 바꿔 API 호출을 상수(최대 3회)로 고정한다.
    """
    bounds = _slot_bounds(state)
    if not bounds or not access_token:
        return []
    _, _, date, requested_time, _ = bounds
    work = dict(state)
    if not str(work.get("duration_mode") or "").strip():
        work["duration_mode"] = "1h"
    apply_duration_mode(work)
    duration = int(work.get("duration_minutes") or 60)

    candidates = [t for t in _candidate_start_times(date, requested_time, duration) if t != requested_time]
    if not candidates:
        return []
    try:
        day_start_iso = to_kst_iso(date, BUSINESS_HOUR_START)
        day_end_iso = to_kst_iso(date, BUSINESS_HOUR_END)
    except ValueError:
        return []

    room_list = rooms if rooms is not None else get_rooms()
    ordered = ordered_rooms_for_state(work, room_list)
    resource_ids = [
        str(r.get("calendar_resource_id") or "").strip()
        for r in ordered
        if str(r.get("calendar_resource_id") or "").strip()
    ]
    booker_cal = api_calendar_id or "primary"
    people_ids = [booker_cal]
    for email in _attendee_emails(state):
        if email.lower() != booker_cal.lower() and email not in people_ids:
            people_ids.append(email)
    group_enabled = bool(get_room_calendar_config().get("group_calendar_id"))

    def _fetch_people() -> dict[str, list[dict[str, str]]]:
        return _freebusy_chunked(
            people_ids, time_min=day_start_iso, time_max=day_end_iso, access_token=access_token
        )

    def _fetch_rooms() -> dict[str, list[dict[str, str]]]:
        if not resource_ids:
            return {}
        return _freebusy_chunked(
            resource_ids, time_min=day_start_iso, time_max=day_end_iso, access_token=access_token
        )

    def _fetch_group() -> list[dict[str, Any]]:
        if not group_enabled:
            return []
        return list_group_room_bookings(
            time_min=day_start_iso, time_max=day_end_iso, access_token=access_token
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_people = executor.submit(_fetch_people)
        f_rooms = executor.submit(_fetch_rooms)
        f_group = executor.submit(_fetch_group)
        try:
            people_busy = f_people.result()
        except Exception:
            return []
        try:
            room_busy = f_rooms.result()
        except Exception:
            room_busy = {}
        try:
            group_bookings = f_group.result()
        except Exception:
            group_bookings = []

    people_ranges = {cid: _busy_ranges(spans) for cid, spans in people_busy.items()}
    room_ranges = {rid: _busy_ranges(spans) for rid, spans in room_busy.items()}
    attendee_count = _attendee_count_for_state(state)

    suggestions: list[SlotSuggestion] = []
    for time_str in candidates:
        try:
            t0 = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        except ValueError:
            continue
        t1 = t0 + timedelta(minutes=duration)
        end_time = t1.strftime("%H:%M")
        if any(_ranges_overlap(people_ranges.get(cid) or [], t0, t1) for cid in people_ids):
            continue

        group_busy = busy_resource_ids_from_group_bookings(
            group_bookings,
            ordered,
            time_min_iso=t0.isoformat(),
            time_max_iso=t1.isoformat(),
        )
        annotated: list[dict[str, Any]] = []
        for room in ordered:
            row = dict(room)
            rid = str(room.get("calendar_resource_id") or "").strip()
            if not rid:
                row["availability"] = "unknown"
            elif _ranges_overlap(room_ranges.get(rid) or [], t0, t1) or rid in group_busy:
                row["availability"] = "busy"
            else:
                row["availability"] = "free"
            annotated.append(row)
        _sort_rooms_for_recommendation(annotated, attendee_count=attendee_count)
        free_rooms = [r for r in annotated if str(r.get("availability") or "") == "free"]
        if not free_rooms:
            continue
        top = free_rooms[0]
        suggestions.append(
            SlotSuggestion(
                meeting_date=date,
                meeting_time=time_str,
                meeting_end_time=end_time,
                free_room_count=len(free_rooms),
                top_room_name=str(top.get("display_name") or top.get("name") or ""),
            )
        )
        if len(suggestions) >= max_n:
            break
    return suggestions


def check_schedule_conflicts(
    state: dict[str, Any],
    *,
    access_token: str | None,
    user_email: str,
    user_name: str,
    api_calendar_id: str,
    snapshot: ComposeCalendarSnapshot | None = None,
    mode: str = "full",
    rooms: list[dict[str, Any]] | None = None,
) -> ConflictCheckResult:
    if not access_token or state.get("ignore_conflict"):
        return ConflictCheckResult()
    bounds = _slot_bounds(state)
    if not bounds:
        return ConflictCheckResult()
    start_iso, end_iso, _, requested_time, _ = bounds
    conflicts = _conflicts_for_slot(
        state,
        start_iso=start_iso,
        end_iso=end_iso,
        access_token=access_token,
        user_email=user_email,
        user_name=user_name,
        api_calendar_id=api_calendar_id,
        snapshot=snapshot,
        mode=mode,
    )
    result = ConflictCheckResult(
        has_conflict=bool(conflicts),
        conflicts=conflicts,
        requested_time=requested_time,
    )
    if conflicts and mode == "full":
        result.alternatives = suggest_alternative_slots(
            state,
            access_token=access_token,
            user_email=user_email,
            user_name=user_name,
            api_calendar_id=api_calendar_id,
            rooms=rooms,
        )
    return result
