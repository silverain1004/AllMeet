"""회의실 메타 Firestore 저장 (config/rooms)."""

from __future__ import annotations

from typing import Any

from firestore.writes import get_client

_CONFIG_COLLECTION = "config"
_ROOMS_DOC = "rooms"

_DUMMY_ROOMS: list[dict[str, Any]] = [
    {
        "id": "room_a",
        "name": "대회의실 A",
        "capacity": 20,
        "equipment": ["프로젝터", "화이트보드", "화상회의"],
        "calendar_resource_id": "",
        "location": "본관 3층",
        "default_priority": 10,
    },
    {
        "id": "room_b",
        "name": "소회의실 B",
        "capacity": 6,
        "equipment": ["모니터", "화상회의"],
        "calendar_resource_id": "",
        "location": "본관 2층",
        "default_priority": 20,
    },
    {
        "id": "room_c",
        "name": "프로젝트룸 C",
        "capacity": 10,
        "equipment": ["프로젝터", "TV"],
        "calendar_resource_id": "",
        "location": "별관 1층",
        "default_priority": 15,
    },
]


def _normalize_room(row: dict[str, Any]) -> dict[str, Any]:
    equipment = row.get("equipment") or []
    if not isinstance(equipment, list):
        equipment = [str(equipment)]
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "capacity": int(row.get("capacity") or 0),
        "equipment": [str(x).strip() for x in equipment if str(x).strip()],
        "calendar_resource_id": str(row.get("calendar_resource_id") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "default_priority": int(row.get("default_priority") or 0),
    }


def get_rooms() -> list[dict[str, Any]]:
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).get()
    if not snap.exists:
        return [dict(r) for r in _DUMMY_ROOMS]
    data = snap.to_dict() or {}
    rooms = data.get("rooms") or []
    out = [_normalize_room(r) for r in rooms if isinstance(r, dict) and r.get("id")]
    return out or [dict(r) for r in _DUMMY_ROOMS]


def upsert_rooms(rooms: list[dict[str, Any]]) -> None:
    normalized = [_normalize_room(r) for r in rooms if isinstance(r, dict)]
    db = get_client()
    db.collection(_CONFIG_COLLECTION).document(_ROOMS_DOC).set({"rooms": normalized}, merge=True)
