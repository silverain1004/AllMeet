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


def get_team_list() -> list[dict[str, str]]:
    """team_list 문서를 우선 사용하고, 없으면 config 문서들에서 폴백 생성."""
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    teams: list[dict[str, str]] = []
    if snap.exists:
        data = snap.to_dict() or {}
        for item in data.get("teams") or []:
            if not isinstance(item, dict):
                continue
            team_id = str(item.get("id") or "").strip()
            team_name = str(item.get("name") or "").strip()
            if team_id and team_name:
                teams.append({"id": team_id, "name": team_name})
    if teams:
        return teams

    docs = db.collection(_CONFIG_COLLECTION).stream()
    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        team_name = str(data.get("team_name") or doc.id).strip()
        if team_name:
            teams.append({"id": doc.id, "name": team_name})
    teams.sort(key=lambda x: x["name"])
    return teams


def rename_team_in_list(team_id: str, new_team_name: str) -> None:
    """team_list에서 팀 이름을 갱신합니다."""
    new_name = (new_team_name or "").strip()
    if not new_name:
        return
    teams = get_team_list()
    changed = False
    for team in teams:
        if team.get("id") == team_id:
            team["name"] = new_name
            changed = True
            break
    if changed:
        get_client().collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC).set({"teams": teams}, merge=True)


def remove_team_from_list(team_id: str) -> None:
    """team_list에서 팀 항목을 제거합니다."""
    teams = [t for t in get_team_list() if t.get("id") != team_id]
    get_client().collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC).set({"teams": teams}, merge=True)


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


def update_team_name(
    *,
    team_id: str,
    new_team_name: str,
    space_id: str,
    user_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """팀 이름을 변경하고 team_list도 동기화합니다."""
    existing = get_team_config(team_id)
    if not existing:
        raise ValueError("존재하지 않는 팀입니다.")
    out = upsert_team_config(
        team_id=team_id,
        team_name=new_team_name,
        space_id=space_id,
        user_context=user_context,
        updates={},
    )
    rename_team_in_list(team_id, new_team_name)
    return out


def delete_team(team_id: str) -> bool:
    """팀 문서를 삭제하고 team_list에서 제거합니다."""
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(team_id)
    snap = ref.get()
    if not snap.exists:
        return False
    ref.delete()
    remove_team_from_list(team_id)
    return True
