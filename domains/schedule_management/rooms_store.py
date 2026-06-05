"""회의실 메타 Firestore 저장 (config/rooms)."""

from __future__ import annotations

from typing import Any

from domains.schedule_management.gunsan_rooms import (
    GUNSAN_ROOM_CATALOG,
    catalog_entry_to_room,
    gunsan_rooms_from_catalog,
)
from firestore.writes import get_client

_CONFIG_COLLECTION = "config"
_ROOMS_DOC = "rooms"

_DUMMY_ROOMS: list[dict[str, Any]] = gunsan_rooms_from_catalog()


def _normalize_room(row: dict[str, Any]) -> dict[str, Any]:
    equipment = row.get("equipment") or []
    if not isinstance(equipment, list):
        equipment = [str(equipment)]
    display_name = str(row.get("display_name") or row.get("name") or "").strip()
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "display_name": display_name,
        "capacity": int(row.get("capacity") or 0),
        "equipment": [str(x).strip() for x in equipment if str(x).strip()],
        "calendar_resource_id": str(row.get("calendar_resource_id") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "default_priority": int(row.get("default_priority") or 0),
    }


def _catalog_by_resource_id() -> dict[str, dict[str, Any]]:
    return {
        str(e.get("calendar_resource_id") or "").strip(): catalog_entry_to_room(e)
        for e in GUNSAN_ROOM_CATALOG
        if str(e.get("calendar_resource_id") or "").strip()
    }


def _enrich_with_catalog(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = _catalog_by_resource_id()
    out: list[dict[str, Any]] = []
    for room in rooms:
        row = dict(room)
        cal_id = str(row.get("calendar_resource_id") or "").strip()
        catalog = by_id.get(cal_id)
        if catalog:
            if catalog.get("display_name"):
                row["display_name"] = catalog["display_name"]
            equipment = row.get("equipment") or []
            if not equipment or equipment == ["회의실"]:
                row["equipment"] = catalog.get("equipment") or equipment
        out.append(row)
    return out


def get_rooms() -> list[dict[str, Any]]:
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).get()
    if not snap.exists:
        return _enrich_with_catalog([dict(r) for r in _DUMMY_ROOMS])
    data = snap.to_dict() or {}
    rooms = data.get("rooms") or []
    out = [_normalize_room(r) for r in rooms if isinstance(r, dict) and r.get("id")]
    base = out or [dict(r) for r in _DUMMY_ROOMS]
    return _enrich_with_catalog(base)


def upsert_rooms(rooms: list[dict[str, Any]]) -> None:
    normalized = [_normalize_room(r) for r in rooms if isinstance(r, dict)]
    db = get_client()
    db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).set({"rooms": normalized}, merge=True)
