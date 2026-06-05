"""군산 회의실 집계 캘린더·리소스 설정 (config/room_calendar)."""

from __future__ import annotations

import os
from typing import Any

from domains.schedule_management.gunsan_rooms import GUNSAN_ROOM_CATALOG
from firestore.writes import get_client

_CONFIG_COLLECTION = "config"
_ROOM_CALENDAR_DOC = "room_calendar"

_GUNSAN_GROUP_CALENDAR_ID = (
    "c_b9eaaa762147e2838192050f2ae6ff03e9e0f38e242cc4394e963ee81212e454"
    "@group.calendar.google.com"
)

_DEFAULT: dict[str, Any] = {
    "group_calendar_id": _GUNSAN_GROUP_CALENDAR_ID,
    "group_calendar_name": "군산 회의실 예약",
    "sync_name_filter": "군산",
    "room_resource_ids": [r["calendar_resource_id"] for r in GUNSAN_ROOM_CATALOG],
    "room_catalog": GUNSAN_ROOM_CATALOG,
    "impersonate_email": "",
}


def _parse_resource_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [x.strip() for x in raw.replace(",", "\n").splitlines() if x.strip()]
    return []


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    group_id = str(data.get("group_calendar_id") or "").strip()
    if not group_id:
        group_id = os.environ.get("GUNSAN_ROOM_GROUP_CALENDAR_ID", "").strip()
    impersonate = str(data.get("impersonate_email") or "").strip()
    if not impersonate:
        impersonate = os.environ.get("GOOGLE_CALENDAR_IMPERSONATE_EMAIL", "").strip()
    resource_ids = _parse_resource_ids(data.get("room_resource_ids"))
    if not resource_ids:
        resource_ids = [r["calendar_resource_id"] for r in GUNSAN_ROOM_CATALOG]
    catalog = data.get("room_catalog")
    if not isinstance(catalog, list) or not catalog:
        catalog = GUNSAN_ROOM_CATALOG
    return {
        "group_calendar_id": group_id,
        "group_calendar_name": str(data.get("group_calendar_name") or "군산 회의실 예약").strip(),
        "sync_name_filter": str(data.get("sync_name_filter") or "군산").strip(),
        "room_resource_ids": resource_ids,
        "room_catalog": catalog,
        "impersonate_email": impersonate,
    }


def get_room_calendar_config() -> dict[str, Any]:
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(_ROOM_CALENDAR_DOC).get()
    if not snap.exists:
        return _normalize(_DEFAULT)
    return _normalize(snap.to_dict() or {})


def update_room_calendar_config(**fields: Any) -> dict[str, Any]:
    current = get_room_calendar_config()
    for key, val in fields.items():
        if key not in _DEFAULT:
            continue
        if key == "room_resource_ids":
            current[key] = _parse_resource_ids(val)
        else:
            current[key] = val
    normalized = _normalize(current)
    db = get_client()
    db.collection(_CONFIG_COLLECTION).document(_ROOM_CALENDAR_DOC).set(normalized, merge=True)
    return normalized
