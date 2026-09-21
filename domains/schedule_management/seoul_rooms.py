"""서울(세아타워) 회의실 5개 기본 정의."""

from __future__ import annotations

from typing import Any

from domains.schedule_management.gunsan_rooms import catalog_entry_to_room

SEOUL_ROOM_CATALOG: list[dict[str, Any]] = [
    {
        "name": "VNTG 서울 Bali (6)",
        "display_name": "Bali",
        "calendar_resource_id": "c_18886qq804vhijdjllp1d2p3kg67m@resource.calendar.google.com",
        "location": "세아타워(VNTG)-6층",
        "equipment": ["회의실"],
        "office": "seoul",
        "aliases": ["발리"],
    },
    {
        "name": "VNTG 서울 Seoul (20)",
        "display_name": "Seoul",
        "calendar_resource_id": "c_188c8176isdamimmg7jp8702pnvq0@resource.calendar.google.com",
        "location": "세아타워(VNTG)-6층",
        "equipment": ["회의실"],
        "office": "seoul",
        # 지역어 '서울'과 충돌하므로 '서울' 단독은 별칭으로 두지 않는다.
        "aliases": ["서울룸"],
    },
    {
        "name": "VNTG 서울 Hawaii (6)",
        "display_name": "Hawaii",
        "calendar_resource_id": "c_1880hukf9ehjsjt0h1k41om7g3l9e@resource.calendar.google.com",
        "location": "세아타워(VNTG)-7층",
        "equipment": ["회의실"],
        "office": "seoul",
        "aliases": ["하와이"],
    },
    {
        "name": "VNTG 서울 London (8)",
        "display_name": "London",
        "calendar_resource_id": "c_1880l7h5vtug2icvn9jp00co70ije@resource.calendar.google.com",
        "location": "세아타워(VNTG)-7층",
        "equipment": ["회의실"],
        "office": "seoul",
        "aliases": ["런던"],
    },
    {
        "name": "VNTG 서울 Paris (6)",
        "display_name": "Paris",
        "calendar_resource_id": "c_188eqv9fb9m2ejtoibil0j9umc2nc@resource.calendar.google.com",
        "location": "세아타워(VNTG)-7층",
        "equipment": ["회의실"],
        "office": "seoul",
        "aliases": ["파리"],
    },
]

EXPECTED_SEOUL_ROOM_COUNT = 5


def seoul_rooms_from_catalog(catalog: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = catalog if catalog is not None else SEOUL_ROOM_CATALOG
    return [catalog_entry_to_room(e) for e in rows if str(e.get("calendar_resource_id") or "").strip()]
