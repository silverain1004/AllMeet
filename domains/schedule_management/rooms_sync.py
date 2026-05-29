"""Workspace 회의실(리소스) 캘린더 동기화."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from domains.schedule_management.calendar_client import _get_service_account_token
from domains.schedule_management.rooms_store import upsert_rooms

logger = logging.getLogger(__name__)


def sync_resource_rooms_from_calendar_list() -> tuple[int, str]:
    """calendarList에서 conference/resource 성격 캘린더를 회의실로 등록.

    Domain-Wide Delegation 없이 SA/ADC가 접근 가능한 리소스 캘린더만 수집합니다.
    """
    try:
        token = _get_service_account_token(write=False)
    except RuntimeError as e:
        return 0, str(e)

    url = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=250"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("rooms sync calendarList failed: %s", e)
        return 0, f"calendarList 실패: {e}"

    rooms: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        cal_id = str(item.get("id") or "").strip()
        if not cal_id:
            continue
        # 리소스 캘린더: resource, conferenceRoom, 또는 @resource.calendar.google.com
        is_resource = (
            "@resource.calendar.google.com" in cal_id
            or str(item.get("resourceId") or "").strip()
            or item.get("conferenceProperties")
        )
        if not is_resource:
            continue
        name = str(item.get("summary") or cal_id).strip()
        rooms.append(
            {
                "id": cal_id.replace("@", "_").replace(".", "_")[:64],
                "name": name,
                "capacity": 10,
                "equipment": ["회의실"],
                "calendar_resource_id": cal_id,
                "location": str(item.get("description") or "").strip(),
                "default_priority": 10,
            }
        )

    if not rooms:
        return 0, "동기화할 리소스 캘린더가 없습니다. (권한 또는 리소스 미등록)"

    upsert_rooms(rooms)
    return len(rooms), f"{len(rooms)}개 회의실을 동기화했습니다."
