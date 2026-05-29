"""회의실 추천 점수·가용 필터."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from domains.schedule_management.calendar_client import (
    KST,
    day_range_kst,
    freebusy_query,
    to_kst_iso,
)
from domains.schedule_management.rooms_store import get_rooms


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


def recommend_rooms(
    state: dict[str, Any],
    *,
    max_n: int = 3,
    duration_minutes: int = 60,
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    rooms = get_rooms()
    attendees = state.get("attendees") or []
    attendee_count = max(len(attendees), 1)
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
    ordered = [r for r, _ in scored]

    date = str(state.get("meeting_date") or "").strip()
    time_str = str(state.get("meeting_time") or "").strip()
    if date and time_str:
        try:
            start_iso = to_kst_iso(date, time_str)
            end_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M") + timedelta(
                minutes=duration_minutes
            )
            end_iso = end_dt.replace(tzinfo=KST).isoformat()
            annotated: list[dict[str, Any]] = []
            resource_ids = [
                str(r.get("calendar_resource_id") or "").strip()
                for r in ordered
                if str(r.get("calendar_resource_id") or "").strip()
            ]
            attendee_emails = [
                str(a.get("email") or "").strip()
                for a in (state.get("attendees") or [])
                if str(a.get("email") or "").strip()
            ]
            attendee_busy: set[str] = set()
            attendee_fb_ok = False
            if attendee_emails and access_token:
                fb_att = freebusy_query(
                    calendar_ids=attendee_emails,
                    time_min=start_iso,
                    time_max=end_iso,
                    access_token=access_token,
                )
                attendee_fb_ok = fb_att.ok
                if fb_att.ok:
                    attendee_busy = _busy_emails_from_freebusy(
                        fb_att.busy,
                        attendee_emails=attendee_emails,
                        time_min_iso=start_iso,
                        time_max_iso=end_iso,
                    )
            partial_attendee_check = bool(attendee_emails) and not attendee_fb_ok

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
                if attendee_busy:
                    row["availability"] = "attendee_conflict"
                elif not rid:
                    row["availability"] = "unknown"
                elif _busy_at(rid):
                    row["availability"] = "busy"
                else:
                    row["availability"] = "free"
                if partial_attendee_check:
                    row["attendee_check"] = "partial"
                annotated.append(row)
            annotated.sort(
                key=lambda r: {
                    "free": 0,
                    "unknown": 1,
                    "attendee_conflict": 2,
                    "busy": 3,
                }.get(str(r.get("availability")), 1)
            )
            ordered = annotated
        except ValueError:
            pass

    meeting_when = ""
    if date and time_str:
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            meeting_when = f"{d.year}년 {d.month:02d}월 {d.day:02d}일 {time_str}"
        except ValueError:
            meeting_when = f"{date} {time_str}"

    out: list[dict[str, Any]] = []
    for room in ordered[:max_n]:
        row = dict(room)
        row["display_when"] = meeting_when
        equip = ", ".join(room.get("equipment") or [])
        row["display_line"] = (
            f"{room.get('name','')} | {meeting_when or '일시 미정'} | "
            f"수용 {room.get('capacity',0)}명 | {equip or '장비 정보 없음'}"
        )
        avail = row.get("availability", "unknown")
        if avail == "free":
            row["availability_label"] = "예약 가능"
        elif avail == "busy":
            row["availability_label"] = "회의실 사용 중"
        elif avail == "attendee_conflict":
            row["availability_label"] = "참석자 일정 충돌"
        elif row.get("attendee_check") == "partial":
            row["availability_label"] = "일부 참석자 일정 미확인"
        else:
            row["availability_label"] = "가용성 미확인"
        out.append(row)
    return out
