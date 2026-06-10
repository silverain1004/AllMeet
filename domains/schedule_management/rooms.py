"""회의실 추천 점수·가용 필터."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domains.schedule_management.compose_availability import ComposeCalendarSnapshot

from domains.schedule_management.calendar_client import (
    KST,
    day_range_kst,
    freebusy_query,
    to_kst_iso,
)
from domains.schedule_management.room_calendar_store import get_room_calendar_config
from domains.schedule_management.rooms_group import (
    busy_resource_ids_from_group_bookings,
    format_group_booking_summary,
    list_group_room_bookings,
)
from domains.schedule_management.rooms_store import get_rooms

_AVAILABILITY_PREVIEW_MINUTES = 60


def _availability_duration_minutes(state: dict[str, Any], duration_minutes: int) -> int:
    """가용성 조회용 회의 길이(분). duration 미선택 시 1시간으로 미리보기."""
    mode = str(state.get("duration_mode") or "").strip()
    if mode == "1h":
        return 60
    if mode == "2h":
        return 120
    if mode == "custom":
        dm = int(state.get("duration_minutes") or duration_minutes or 0)
        return dm if dm > 0 else _AVAILABILITY_PREVIEW_MINUTES
    if duration_minutes > 0:
        return duration_minutes
    dm = int(state.get("duration_minutes") or 0)
    return dm if dm > 0 else _AVAILABILITY_PREVIEW_MINUTES


def _room_equipment_line(room: dict[str, Any]) -> str:
    equipment = [str(x).strip() for x in (room.get("equipment") or []) if str(x).strip()]
    return ", ".join(equipment) if equipment else "장비 정보 없음"


def _attendee_count_for_state(state: dict[str, Any]) -> int:
    attendees = state.get("attendees") or []
    explicit = state.get("attendee_count")
    if explicit is not None:
        try:
            return max(int(explicit), 1)
        except (TypeError, ValueError):
            return max(len(attendees), 1)
    return max(len(attendees), 1)


def _room_recommendation_sort_key(
    room: dict[str, Any],
    *,
    attendee_count: int,
    score: float = 0.0,
) -> tuple[int, int, int, float]:
    cap = int(room.get("capacity") or 0)
    avail = str(room.get("availability") or "")
    avail_rank = {"free": 0, "busy": 1}.get(avail, 0) if avail else 0
    if cap <= 0:
        fit_rank = 1
        excess = 9999
    elif cap < attendee_count:
        fit_rank = 2
        excess = 9999
    else:
        fit_rank = 0
        excess = cap - attendee_count
    return (avail_rank, fit_rank, excess, -score)


def _sort_rooms_for_recommendation(
    rooms: list[dict[str, Any]],
    *,
    attendee_count: int,
) -> None:
    rooms.sort(
        key=lambda r: _room_recommendation_sort_key(
            r,
            attendee_count=attendee_count,
            score=float(r.get("_recommend_score") or 0),
        )
    )


def score_rooms(
    rooms: list[dict[str, Any]],
    *,
    attendee_count: int,
    equipment_keywords: list[str],
    location_keyword: str = "",
    name_keyword: str = "",
) -> list[tuple[dict[str, Any], float]]:
    scored: list[tuple[dict[str, Any], float]] = []
    eq_lower = [k.lower() for k in equipment_keywords if k]
    loc_lower = location_keyword.lower()
    name_lower = name_keyword.lower()
    for room in rooms:
        score = float(room.get("default_priority") or 0)
        cap = int(room.get("capacity") or 0)
        if attendee_count > 0 and cap >= attendee_count:
            score += 30
            score -= (cap - attendee_count) * 0.1
        elif attendee_count > 0 and cap > 0:
            score -= 10
        equip = [str(e).lower() for e in (room.get("equipment") or [])]
        for kw in eq_lower:
            if any(kw in e for e in equip):
                score += 15
        rname = str(room.get("name") or "").lower()
        rloc = str(room.get("location") or "").lower()
        if loc_lower and loc_lower in rloc:
            score += 10
        if name_lower and name_lower in rname:
            score += 10
        scored.append((room, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def filter_available(
    rooms: list[dict[str, Any]],
    *,
    time_min_iso: str,
    time_max_iso: str,
) -> list[dict[str, Any]]:
    resource_ids = [
        str(r.get("calendar_resource_id") or "").strip()
        for r in rooms
        if str(r.get("calendar_resource_id") or "").strip()
    ]
    if not resource_ids:
        return list(rooms)

    result = freebusy_query(calendar_ids=resource_ids, time_min=time_min_iso, time_max=time_max_iso)
    if not result.ok:
        return list(rooms)

    def _overlaps(busy_list: list[dict[str, str]]) -> bool:
        try:
            t0 = datetime.fromisoformat(time_min_iso.replace("Z", "+00:00")).astimezone(KST)
            t1 = datetime.fromisoformat(time_max_iso.replace("Z", "+00:00")).astimezone(KST)
        except ValueError:
            return False
        for span in busy_list:
            s_raw = (span.get("start") or "").replace("Z", "+00:00")
            e_raw = (span.get("end") or "").replace("Z", "+00:00")
            try:
                s = datetime.fromisoformat(s_raw).astimezone(KST)
                e = datetime.fromisoformat(e_raw).astimezone(KST)
            except ValueError:
                continue
            if s < t1 and e > t0:
                return True
        return False

    out: list[dict[str, Any]] = []
    for room in rooms:
        rid = str(room.get("calendar_resource_id") or "").strip()
        if not rid:
            room_copy = dict(room)
            room_copy["availability"] = "unknown"
            out.append(room_copy)
            continue
        busy = result.busy.get(rid) or []
        room_copy = dict(room)
        if _overlaps(busy):
            room_copy["availability"] = "busy"
        else:
            room_copy["availability"] = "free"
            out.append(room_copy)
    # busy rooms at end for display but we filter to free+unknown for recommendations
    free = [r for r in out if r.get("availability") == "free"]
    unknown = [r for r in rooms if not str(r.get("calendar_resource_id") or "").strip()]
    for u in unknown:
        u = dict(u)
        u["availability"] = "unknown"
    return free + unknown


def _interval_overlaps(
    busy_list: list[dict[str, str]],
    *,
    time_min_iso: str,
    time_max_iso: str,
) -> bool:
    try:
        t0 = datetime.fromisoformat(time_min_iso).astimezone(KST)
        t1 = datetime.fromisoformat(time_max_iso).astimezone(KST)
    except ValueError:
        return False
    for span in busy_list:
        s_raw = (span.get("start") or "").replace("Z", "+00:00")
        e_raw = (span.get("end") or "").replace("Z", "+00:00")
        try:
            s = datetime.fromisoformat(s_raw).astimezone(KST)
            e = datetime.fromisoformat(e_raw).astimezone(KST)
        except ValueError:
            continue
        if s < t1 and e > t0:
            return True
    return False


def _busy_emails_from_freebusy(
    busy_map: dict[str, list[dict[str, str]]],
    *,
    attendee_emails: list[str],
    time_min_iso: str,
    time_max_iso: str,
) -> set[str]:
    """freebusy 결과에서 해당 구간에 겹치는 참석자 이메일."""
    busy_emails: set[str] = set()
    for email in attendee_emails:
        e = email.strip()
        if not e or "@" not in e:
            continue
        if _interval_overlaps(
            busy_map.get(e) or [],
            time_min_iso=time_min_iso,
            time_max_iso=time_max_iso,
        ):
            busy_emails.add(e)
    return busy_emails


def ordered_rooms_for_state(
    state: dict[str, Any],
    rooms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit = state.get("attendee_count")
    attendee_count = _attendee_count_for_state(state)
    equipment_keywords = state.get("equipment_keywords") or []
    location_keyword = str(state.get("location_keyword") or "")
    name_keyword = str(state.get("room_name_keyword") or "")

    scored = score_rooms(
        rooms,
        attendee_count=attendee_count,
        equipment_keywords=equipment_keywords,
        location_keyword=location_keyword,
        name_keyword=name_keyword,
    )
    ordered: list[dict[str, Any]] = []
    for room, score in scored:
        row = dict(room)
        row["_recommend_score"] = score
        ordered.append(row)
    if explicit is not None:
        ordered = [
            r
            for r in ordered
            if int(r.get("capacity") or 0) >= attendee_count or int(r.get("capacity") or 0) == 0
        ]
    _sort_rooms_for_recommendation(ordered, attendee_count=attendee_count)
    return ordered


def recommend_rooms(
    state: dict[str, Any],
    *,
    max_n: int = 3,
    duration_minutes: int = 60,
    access_token: str | None = None,
    snapshot: ComposeCalendarSnapshot | None = None,  # noqa: F821
    rooms: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    room_list = rooms if rooms is not None else get_rooms()
    ordered = ordered_rooms_for_state(state, room_list)
    attendee_count = _attendee_count_for_state(state)

    if snapshot:
        try:
            start_iso = snapshot.start_iso
            end_iso = snapshot.end_iso
            busy_map = snapshot.room_busy
            group_bookings = snapshot.group_bookings
            group_busy = busy_resource_ids_from_group_bookings(
                group_bookings,
                ordered,
                time_min_iso=start_iso,
                time_max_iso=end_iso,
            )
            t0 = datetime.fromisoformat(start_iso).astimezone(KST)
            t1 = datetime.fromisoformat(end_iso).astimezone(KST)

            def _busy_at(rid: str) -> bool:
                for span in busy_map.get(rid) or []:
                    s_raw = (span.get("start") or "").replace("Z", "+00:00")
                    e_raw = (span.get("end") or "").replace("Z", "+00:00")
                    try:
                        s = datetime.fromisoformat(s_raw).astimezone(KST)
                        e = datetime.fromisoformat(e_raw).astimezone(KST)
                    except ValueError:
                        continue
                    if s < t1 and e > t0:
                        return True
                return False

            annotated: list[dict[str, Any]] = []
            for room in ordered:
                row = dict(room)
                rid = str(room.get("calendar_resource_id") or "").strip()
                if not rid:
                    row["availability"] = "unknown"
                elif _busy_at(rid) or rid in group_busy:
                    row["availability"] = "busy"
                else:
                    row["availability"] = "free"
                row["show_availability"] = True
                annotated.append(row)
            _sort_rooms_for_recommendation(annotated, attendee_count=attendee_count)
            ordered = annotated
        except ValueError:
            pass
    else:
        date = str(state.get("meeting_date") or "").strip()
        time_str = str(state.get("meeting_time") or "").strip()
        duration_mode = str(state.get("duration_mode") or "").strip()
        eff_duration = _availability_duration_minutes(state, duration_minutes)
        can_check = bool(date and time_str)
        if duration_mode == "custom":
            can_check = can_check and bool(str(state.get("meeting_end_time") or "").strip())
        if can_check:
            try:
                start_iso = to_kst_iso(date, time_str)
                if duration_mode == "custom":
                    end_time = str(state.get("meeting_end_time") or "").strip()
                    end_iso = to_kst_iso(date, end_time)
                else:
                    end_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M") + timedelta(
                        minutes=eff_duration
                    )
                    end_iso = end_dt.replace(tzinfo=KST).isoformat()
                annotated: list[dict[str, Any]] = []
                resource_ids = [
                    str(r.get("calendar_resource_id") or "").strip()
                    for r in ordered
                    if str(r.get("calendar_resource_id") or "").strip()
                ]

                busy_map: dict[str, list[dict[str, str]]] = {}
                if resource_ids:
                    fb = freebusy_query(
                        calendar_ids=resource_ids,
                        time_min=start_iso,
                        time_max=end_iso,
                        access_token=access_token,
                    )
                    if fb.ok:
                        busy_map = fb.busy
                group_busy: set[str] = set()
                group_bookings: list[dict[str, Any]] = []
                if get_room_calendar_config().get("group_calendar_id"):
                    group_bookings = list_group_room_bookings(
                        time_min=start_iso,
                        time_max=end_iso,
                        access_token=access_token,
                    )
                    group_busy = busy_resource_ids_from_group_bookings(
                        group_bookings,
                        ordered,
                        time_min_iso=start_iso,
                        time_max_iso=end_iso,
                    )
                t0 = datetime.fromisoformat(start_iso).astimezone(KST)
                t1 = datetime.fromisoformat(end_iso).astimezone(KST)

                def _busy_at(rid: str) -> bool:
                    for span in busy_map.get(rid) or []:
                        s_raw = (span.get("start") or "").replace("Z", "+00:00")
                        e_raw = (span.get("end") or "").replace("Z", "+00:00")
                        try:
                            s = datetime.fromisoformat(s_raw).astimezone(KST)
                            e = datetime.fromisoformat(e_raw).astimezone(KST)
                        except ValueError:
                            continue
                        if s < t1 and e > t0:
                            return True
                    return False

                for room in ordered:
                    row = dict(room)
                    rid = str(room.get("calendar_resource_id") or "").strip()
                    if not rid:
                        row["availability"] = "unknown"
                    elif _busy_at(rid) or rid in group_busy:
                        row["availability"] = "busy"
                    else:
                        row["availability"] = "free"
                    row["show_availability"] = True
                    annotated.append(row)
                _sort_rooms_for_recommendation(annotated, attendee_count=attendee_count)
                ordered = annotated
            except ValueError:
                pass

    out: list[dict[str, Any]] = []
    for room in ordered[:max_n]:
        row = dict(room)
        row["display_line"] = f"수용 {room.get('capacity', 0)}명 | {_room_equipment_line(room)}"
        if row.get("show_availability"):
            avail = str(row.get("availability") or "")
            if avail == "busy":
                row["availability_label"] = "사용 중"
            elif avail == "free":
                row["availability_label"] = "사용 가능"
            else:
                row["availability_label"] = ""
                row["show_availability"] = False
        else:
            row["availability_label"] = ""
            row["show_availability"] = False
        out.append(row)
    return out


def get_group_booking_summary(
    state: dict[str, Any],
    *,
    access_token: str | None = None,
    snapshot: ComposeCalendarSnapshot | None = None,  # noqa: F821
    rooms: list[dict[str, Any]] | None = None,
) -> str:
    """선택 일시 기준 군산 집계 캘린더 예약 요약."""
    if not get_room_calendar_config().get("group_calendar_id"):
        return ""
    room_list = rooms if rooms is not None else get_rooms()
    if snapshot:
        bookings = snapshot.group_bookings
        start_iso = snapshot.start_iso
        end_iso = snapshot.end_iso
    else:
        date = str(state.get("meeting_date") or "").strip()
        time_str = str(state.get("meeting_time") or "").strip()
        if not date or not time_str:
            return ""
        try:
            duration = int(state.get("duration_minutes") or 60)
            start_iso = to_kst_iso(date, time_str)
            end_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M") + timedelta(
                minutes=duration
            )
            end_iso = end_dt.replace(tzinfo=KST).isoformat()
        except ValueError:
            return ""
        bookings = list_group_room_bookings(
            time_min=start_iso,
            time_max=end_iso,
            access_token=access_token,
        )
    if not bookings:
        return ""
    return format_group_booking_summary(
        bookings,
        room_list,
        time_min_iso=start_iso,
        time_max_iso=end_iso,
    )
