"""Workspace 회의실(리소스) 캘린더 동기화."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from domains.schedule_management.calendar_client import (
    KST,
    _get_service_account_token,
    list_calendar_list,
    list_events,
)
from domains.schedule_management.gunsan_rooms import (
    EXPECTED_GUNSAN_ROOM_COUNT,
    catalog_entry_to_room,
    parse_capacity_from_name,
)
from domains.schedule_management.room_calendar_store import get_room_calendar_config
from domains.schedule_management.rooms_store import upsert_rooms

logger = logging.getLogger(__name__)

_EXPECTED_ROOM_COUNT = EXPECTED_GUNSAN_ROOM_COUNT


def _room_from_calendar_item(item: dict[str, Any]) -> dict[str, Any] | None:
    cal_id = str(item.get("id") or "").strip()
    if not cal_id:
        return None
    is_resource = (
        "@resource.calendar.google.com" in cal_id
        or str(item.get("resourceId") or "").strip()
        or item.get("conferenceProperties")
    )
    if not is_resource:
        return None
    name = str(item.get("summary") or cal_id).strip()
    location = str(item.get("description") or "").strip()
    return _room_dict(cal_id, name=name, location=location)


def _room_dict(
    cal_id: str,
    *,
    name: str = "",
    location: str = "",
) -> dict[str, Any]:
    display_name = name or cal_id.split("@")[0]
    return {
        "id": cal_id.replace("@", "_").replace(".", "_")[:64],
        "name": display_name,
        "capacity": parse_capacity_from_name(display_name),
        "equipment": ["회의실"],
        "calendar_resource_id": cal_id,
        "location": location,
        "default_priority": 10,
    }


def _filter_by_name(rooms: list[dict[str, Any]], name_filter: str) -> list[dict[str, Any]]:
    needle = (name_filter or "").strip().lower()
    if not needle:
        return rooms
    return [
        r
        for r in rooms
        if needle in str(r.get("name") or "").lower()
        or needle in str(r.get("location") or "").lower()
        or needle in str(r.get("calendar_resource_id") or "").lower()
    ]


def _rooms_from_manual_ids(
    resource_ids: list[str],
    catalog: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    by_id = {
        str(e.get("calendar_resource_id") or "").strip(): e
        for e in (catalog or [])
        if str(e.get("calendar_resource_id") or "").strip()
    }
    rooms: list[dict[str, Any]] = []
    for rid in resource_ids:
        rid = rid.strip()
        if not rid:
            continue
        entry = by_id.get(rid)
        if entry:
            rooms.append(catalog_entry_to_room(entry))
        else:
            rooms.append(_room_dict(rid))
    return rooms


def _rooms_from_calendar_list(
    *,
    subject_email: str | None,
    name_filter: str,
) -> tuple[list[dict[str, Any]], str]:
    result = list_calendar_list(subject_email=subject_email)
    if not result.ok:
        return [], f"calendarList 실패: {result.detail or result.error_kind}"

    rooms: list[dict[str, Any]] = []
    for item in result.calendar_list_items:
        row = _room_from_calendar_item(item)
        if row:
            rooms.append(row)
    rooms = _filter_by_name(rooms, name_filter)
    return rooms, ""


def _rooms_from_group_calendar(
    group_calendar_id: str,
    *,
    subject_email: str | None,
) -> tuple[list[dict[str, Any]], str]:
    end_dt = datetime.now(KST)
    start_dt = end_dt - timedelta(days=90)
    token: str | None = None
    try:
        token = _get_service_account_token(write=False, subject_email=subject_email)
    except RuntimeError:
        token = None
    result = list_events(
        calendar_id=group_calendar_id,
        time_min=start_dt.isoformat(),
        time_max=end_dt.isoformat(),
        max_results=250,
        access_token=token,
    )
    if not result.ok:
        return [], f"집계 캘린더 조회 실패: {result.detail or result.error_kind}"

    seen: set[str] = set()
    rooms: list[dict[str, Any]] = []
    for event in result.events:
        for att in event.get("attendees") or []:
            if not isinstance(att, dict) or not att.get("resource"):
                continue
            email = str(att.get("email") or "").strip()
            if not email or email in seen:
                continue
            seen.add(email)
            name = str(att.get("displayName") or "").strip()
            rooms.append(_room_dict(email, name=name))
    rooms = _filter_by_name(rooms, get_room_calendar_config().get("sync_name_filter", ""))
    return rooms, ""


def _count_message(count: int) -> str:
    if count == _EXPECTED_ROOM_COUNT:
        return f"{count}개 회의실을 동기화했습니다."
    if count > 0:
        return f"{count}개 회의실을 동기화했습니다. (예상 {_EXPECTED_ROOM_COUNT}개)"
    return ""


def sync_resource_rooms_from_calendar_list() -> tuple[int, str]:
    """군산 회의실 리소스를 Firestore config/rooms에 동기화.

    1) 수동 resource_ids
    2) calendarList (+ 선택적 DWD)
    3) 집계 캘린더 이벤트 역추출
    """
    config = get_room_calendar_config()
    subject = str(config.get("impersonate_email") or "").strip() or None
    name_filter = str(config.get("sync_name_filter") or "군산").strip()

    manual_ids = config.get("room_resource_ids") or []
    catalog = config.get("room_catalog") or []
    if manual_ids:
        rooms = _rooms_from_manual_ids(manual_ids, catalog=catalog)
        if rooms:
            upsert_rooms(rooms)
            return len(rooms), f"수동 등록 {_count_message(len(rooms))}"
        return 0, "수동 등록된 리소스 ID가 비어 있습니다."

    rooms, list_err = _rooms_from_calendar_list(subject_email=subject, name_filter=name_filter)
    if rooms:
        upsert_rooms(rooms)
        return len(rooms), f"calendarList {_count_message(len(rooms))}"

    group_id = str(config.get("group_calendar_id") or "").strip()
    if group_id:
        rooms, group_err = _rooms_from_group_calendar(group_id, subject_email=subject)
        if rooms:
            upsert_rooms(rooms)
            return len(rooms), f"집계 캘린더 역추출 {_count_message(len(rooms))}"
        list_err = list_err or group_err

    hint = (
        "동기화할 리소스 캘린더가 없습니다. "
        f"서비스 계정에 {_EXPECTED_ROOM_COUNT}개 회의실 리소스·군산 집계 캘린더를 공유하거나, "
        "설정에서 리소스 ID를 수동 등록해 주세요. "
        f"(docs/ROOM_CALENDAR_SETUP.md 참고)"
    )
    if list_err:
        hint = f"{list_err} {hint}"
    logger.warning("rooms sync found 0 resources: %s", hint)
    return 0, hint
