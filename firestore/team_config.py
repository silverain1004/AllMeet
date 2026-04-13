"""팀 단위 주간업무보고 설정 저장/조회."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .writes import get_client

_CONFIG_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"


def make_team_id(team_name: str) -> str:
    base = (team_name or "").strip().lower()
    base = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base or "weekly_report"


def parse_template_page_id(template_page_url: str) -> str | None:
    text = (template_page_url or "").strip()
    if not text:
        return None
    m = re.search(r"/pages/(\d+)", text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return None


def parse_confluence_space_key(text_value: str) -> str | None:
    """
    Confluence URL 또는 직접 입력값에서 space key를 추출합니다.

    - URL 예: /wiki/spaces/PLATFORM/pages/123...
    - 직접 입력 예: PLATFORM
    """
    text = (text_value or "").strip()
    if not text:
        return None

    m = re.search(r"/spaces/([^/\s]+)/", text, flags=re.IGNORECASE)
    if m:
        key = m.group(1).strip()
        return key.upper() if key else None

    if re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        return text.upper()
    return None


def parse_team_members(raw_value: str) -> list[dict[str, str]]:
    parts = [p.strip() for p in re.split(r"[,\n]", raw_value or "") if p.strip()]
    return [{"name": name, "nickname": ""} for name in parts]


def parse_root_page_ids(raw_value: str) -> list[dict[str, Any]]:
    """
    루트 페이지 ID를 줄바꿈/쉼표 기반으로 파싱합니다.

    - 사용자는 한 줄에 하나씩 ID를 추가/삭제할 수 있습니다.
    - URL이 들어오면 /pages/{id} 패턴에서 ID를 추출합니다.
    """
    parts = [p.strip() for p in re.split(r"[,\n]", raw_value or "") if p.strip()]
    if not parts:
        return []

    out: list[dict[str, Any]] = []
    for idx, part in enumerate(parts, start=1):
        page_id = parse_template_page_id(part)
        if page_id is None:
            raise ValueError(f"{idx}번째 값에서 유효한 페이지 ID를 찾지 못했습니다: {part}")
        out.append({"level": idx, "page_id": page_id})
    return out


def _normalized_user_context(user_context: dict[str, Any] | None) -> dict[str, str]:
    src = user_context or {}
    return {
        "name": str(src.get("name") or "").strip(),
        "email": str(src.get("email") or "").strip(),
        "department": str(src.get("department") or "미지정").strip() or "미지정",
    }


def _upsert_team_list(team_id: str, team_name: str) -> None:
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        teams = list(data.get("teams") or [])
    else:
        teams = []

    exists = any((t.get("id") == team_id) for t in teams if isinstance(t, dict))
    if not exists:
        teams.append({"id": team_id, "name": team_name})
        ref.set({"teams": teams}, merge=True)


def get_team_config(team_id: str) -> dict[str, Any] | None:
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(team_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def upsert_team_config(
    *,
    team_id: str,
    team_name: str,
    space_id: str,
    user_context: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(team_id)
    snap = ref.get()
    now = datetime.utcnow()

    payload: dict[str, Any] = {
        "team_name": (team_name or "").strip(),
        "space_id": (space_id or "").strip(),
        "user_context": _normalized_user_context(user_context),
        "updated_at": now,
    }
    payload.update(updates)

    if snap.exists:
        ref.set(payload, merge=True)
    else:
        payload["created_at"] = now
        ref.set(payload, merge=True)

    _upsert_team_list(team_id, team_name)
    return ref.get().to_dict() or {}
