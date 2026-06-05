"""군산 회의실 3개 기본 정의."""

from __future__ import annotations

import re
from typing import Any

_CAPACITY_RE = re.compile(r"\((\d+)\)\s*$")

GUNSAN_ROOM_CATALOG: list[dict[str, Any]] = [
    {
        "name": "VNTG 군산 V-Room (18)",
        "display_name": "V-Room",
        "calendar_resource_id": "c_1885rgn3rk4pci2hkkv4lo7s65qcc@resource.calendar.google.com",
        "location": "브이엔티지(군산)-3층",
        "equipment": ["빔프로젝터", "화이트보드", "노트북", "카메라", "마이크"],
    },
    {
        "name": "VNTG 군산 N-Room (12)",
        "display_name": "N-Room",
        "calendar_resource_id": "c_1880jg03klhiii2eltar07fc223gu@resource.calendar.google.com",
        "location": "브이엔티지(군산)-3층",
        "equipment": ["모니터", "화이트보드", "노트북"],
    },
    {
        "name": "VNTG 군산 T-Room (4)",
        "display_name": "T-Room",
        "calendar_resource_id": "c_1881b3e97f71kit8l3u5h06auf63g@resource.calendar.google.com",
        "location": "브이엔티지(군산)-3층",
        "equipment": ["모니터", "화이트보드"],
    },
]

EXPECTED_GUNSAN_ROOM_COUNT = 3


def parse_capacity_from_name(name: str) -> int:
    """캘린더 이름 끝 괄호 숫자를 수용 인원으로 파싱. 예: 'V-Room (18)' → 18."""
    m = _CAPACITY_RE.search((name or "").strip())
    if m:
        return int(m.group(1))
    return 10


def catalog_entry_to_room(entry: dict[str, Any]) -> dict[str, Any]:
    cal_id = str(entry.get("calendar_resource_id") or "").strip()
    name = str(entry.get("name") or cal_id).strip()
    display_name = str(entry.get("display_name") or name).strip()
    location = str(entry.get("location") or "").strip()
    equipment = entry.get("equipment") or ["회의실"]
    if not isinstance(equipment, list):
        equipment = [str(equipment)]
    return {
        "id": cal_id.replace("@", "_").replace(".", "_")[:64],
        "name": name,
        "display_name": display_name,
        "capacity": parse_capacity_from_name(name),
        "equipment": [str(x).strip() for x in equipment if str(x).strip()],
        "calendar_resource_id": cal_id,
        "location": location,
        "default_priority": 10,
    }


def gunsan_rooms_from_catalog(catalog: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = catalog if catalog is not None else GUNSAN_ROOM_CATALOG
    return [catalog_entry_to_room(e) for e in rows if str(e.get("calendar_resource_id") or "").strip()]
