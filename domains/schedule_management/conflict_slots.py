"""일정 충돌 감지 및 대안 시간 슬롯 제안."""

from __future__ import annotations

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
from domains.schedule_management.rooms import _busy_emails_from_freebusy, recommend_rooms


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

    for email in busy_emails:
        label = _attendee_label(email, state)
        ev_result = list_events(
            calendar_id=email,
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
        if ev_result.ok and ev_result.events:
            found = _conflicts_from_events(
                ev_result.events,
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


def _slot_has_free_room(
    state: dict[str, Any],
    *,
    date: str,
    time_str: str,
    end_time: str,
    access_token: str,
) -> tuple[int, str]:
    probe = dict(state)
    probe["meeting_date"] = date
    probe["meeting_time"] = time_str
    probe["meeting_end_time"] = end_time
    if not str(probe.get("duration_mode") or "").strip():
        probe["duration_mode"] = "1h"
    apply_duration_mode(probe)
    rooms = recommend_rooms(
        probe,
        max_n=10,
        duration_minutes=int(probe.get("duration_minutes") or 60),
        access_token=access_token,
    )
    free_rooms = [r for r in rooms if str(r.get("availability") or "") == "free"]
    if not free_rooms:
        return 0, ""
    top = free_rooms[0]
    name = str(top.get("display_name") or top.get("name") or "")
    return len(free_rooms), name


def suggest_alternative_slots(
    state: dict[str, Any],
    *,
    access_token: str,
    user_email: str,
    user_name: str,
    api_calendar_id: str,
    max_n: int = 3,
) -> list[SlotSuggestion]:
    bounds = _slot_bounds(state)
    if not bounds or not access_token:
        return []
    _, _, date, requested_time, _ = bounds
    work = dict(state)
    if not str(work.get("duration_mode") or "").strip():
        work["duration_mode"] = "1h"
    apply_duration_mode(work)
    duration = int(work.get("duration_minutes") or 60)
    suggestions: list[SlotSuggestion] = []
    for time_str in _candidate_start_times(date, requested_time, duration):
        if time_str == requested_time:
            continue
        try:
            start_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            end_time = (start_dt + timedelta(minutes=duration)).strftime("%H:%M")
            start_iso = to_kst_iso(date, time_str)
            end_iso = to_kst_iso(date, end_time)
        except ValueError:
            continue
        if _conflicts_for_slot(
            state,
            start_iso=start_iso,
            end_iso=end_iso,
            access_token=access_token,
            user_email=user_email,
            user_name=user_name,
            api_calendar_id=api_calendar_id,
        ):
            continue
        free_count, top_name = _slot_has_free_room(
            state,
            date=date,
            time_str=time_str,
            end_time=end_time,
            access_token=access_token,
        )
        if free_count <= 0:
            continue
        suggestions.append(
            SlotSuggestion(
                meeting_date=date,
                meeting_time=time_str,
                meeting_end_time=end_time,
                free_room_count=free_count,
                top_room_name=top_name,
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
        )
    return result
