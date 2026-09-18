"""회의실 메타 Firestore 저장 (config/rooms)."""

from __future__ import annotations

import time
from typing import Any

from domains.schedule_management.gunsan_rooms import (
    GUNSAN_ROOM_CATALOG,
    catalog_entry_to_room,
    gunsan_rooms_from_catalog,
)
from domains.schedule_management.seoul_rooms import (
    SEOUL_ROOM_CATALOG,
    seoul_rooms_from_catalog,
)
from firestore.writes import get_client

_CONFIG_COLLECTION = "config"
_ROOMS_DOC = "rooms"

_DUMMY_ROOMS: list[dict[str, Any]] = seoul_rooms_from_catalog() + gunsan_rooms_from_catalog()
_ROOMS_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_ROOMS_TTL_SEC = 120


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
        "office": str(row.get("office") or "gunsan").strip(),
        "default_priority": int(row.get("default_priority") or 0),
    }


def _catalog_by_resource_id() -> dict[str, dict[str, Any]]:
    entries = list(GUNSAN_ROOM_CATALOG) + list(SEOUL_ROOM_CATALOG)
    return {
        str(e.get("calendar_resource_id") or "").strip(): catalog_entry_to_room(e)
        for e in entries
        if str(e.get("calendar_resource_id") or "").strip()
    }


def _enrich_with_catalog(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Firestore에 저장된 회의실 이름/수용인원/사무실 구분이 낡았어도, 하드코딩된
    카탈로그(calendar_resource_id 매칭)로 항상 최신 표시명·인원·office를 덮어써
    운영 문서를 수동 정정하지 않아도 되게 한다."""
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
            if catalog.get("capacity"):
                row["capacity"] = catalog["capacity"]
            if catalog.get("office"):
                row["office"] = catalog["office"]
            if catalog.get("location") and not row.get("location"):
                row["location"] = catalog["location"]
        out.append(row)
    return out


def clear_rooms_cache() -> None:
    global _ROOMS_CACHE
    _ROOMS_CACHE = None


def get_rooms() -> list[dict[str, Any]]:
    global _ROOMS_CACHE
    now = time.monotonic()
    if _ROOMS_CACHE and (now - _ROOMS_CACHE[0]) < _ROOMS_TTL_SEC:
        return [dict(r) for r in _ROOMS_CACHE[1]]
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).get()
    if not snap.exists:
        result = _enrich_with_catalog([dict(r) for r in _DUMMY_ROOMS])
    else:
        data = snap.to_dict() or {}
        rooms = data.get("rooms") or []
        out = [_normalize_room(r) for r in rooms if isinstance(r, dict) and r.get("id")]
        base = out or [dict(r) for r in _DUMMY_ROOMS]
        result = _enrich_with_catalog(base)
    _ROOMS_CACHE = (now, [dict(r) for r in result])
    return [dict(r) for r in result]


def upsert_rooms(rooms: list[dict[str, Any]]) -> None:
    clear_rooms_cache()
    normalized = [_normalize_room(r) for r in rooms if isinstance(r, dict)]
    db = get_client()
    db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).set({"rooms": normalized}, merge=True)
