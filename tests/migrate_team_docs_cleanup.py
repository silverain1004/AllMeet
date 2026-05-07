from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from firestore.writes import get_client

_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"
_TARGETS = {
    "PC2": "PC2팀",
    "MES2": "MES2팀",
}


def _normalize_name(text: str) -> str:
    v = str(text or "").strip().upper()
    v = re.sub(r"\s+", "", v)
    return v


def _target_id_by_team_name(team_name: str) -> str | None:
    normalized = _normalize_name(team_name)
    if "PC2" in normalized:
        return "PC2"
    if "MES2" in normalized:
        return "MES2"
    return None


def _normalize_members(raw_members: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw_members:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        raw_nick = row.get("nickname")
        if isinstance(raw_nick, list):
            nick = [str(n).strip() for n in raw_nick if str(n).strip()]
        elif raw_nick is None:
            nick = []
        else:
            nick = [str(raw_nick).strip()] if str(raw_nick).strip() else []
        email = str(row.get("email") or "").strip()
        out.append({"name": name, "nickname": nick, "email": email})
    return out


def _merge_members(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in left + right:
        key = _normalize_name(str(item.get("name") or ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = {"name": item.get("name", ""), "nickname": list(item.get("nickname") or []), "email": str(item.get("email") or "")}
            continue
        current = merged[key]
        nick_union = set(current.get("nickname") or [])
        nick_union.update(item.get("nickname") or [])
        current["nickname"] = sorted([n for n in nick_union if str(n).strip()])
        if not str(current.get("email") or "").strip():
            current["email"] = str(item.get("email") or "").strip()
    return list(merged.values())


def _select_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, dict) and not value:
            continue
        return value
    return None


def _merge_doc(current: dict[str, Any], incoming: dict[str, Any], team_id: str) -> dict[str, Any]:
    out = dict(current)
    out["team_name"] = _TARGETS[team_id]
    out["team_members"] = _merge_members(_normalize_members(current.get("team_members")), _normalize_members(incoming.get("team_members")))
    out["calendar_id"] = _select_value(current.get("calendar_id"), incoming.get("calendar_id"), "")
    out["confluence_space_key"] = _select_value(current.get("confluence_space_key"), incoming.get("confluence_space_key"), "")
    out["root_pages"] = _select_value(current.get("root_pages"), incoming.get("root_pages"), [])
    out["template_page_url"] = _select_value(current.get("template_page_url"), incoming.get("template_page_url"), "")
    out["template_page_id"] = _select_value(current.get("template_page_id"), incoming.get("template_page_id"), "")
    out["setup_completed"] = bool(_select_value(current.get("setup_completed"), incoming.get("setup_completed"), False))
    out["updated_at"] = datetime.utcnow()
    if not out.get("created_at"):
        out["created_at"] = datetime.utcnow()
    return out


def main() -> None:
    db = get_client()
    docs = list(db.collection(_COLLECTION).stream())

    targets: dict[str, dict[str, Any]] = {
        "PC2": {},
        "MES2": {},
    }
    source_doc_ids: list[str] = []

    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        team_name = str(data.get("team_name") or doc.id)
        target_id = _target_id_by_team_name(team_name)
        if not target_id:
            source_doc_ids.append(doc.id)
            continue
        targets[target_id] = _merge_doc(targets[target_id], data, target_id)
        source_doc_ids.append(doc.id)

    moved = 0
    deleted = 0
    for team_id, payload in targets.items():
        if not payload:
            payload = {
                "team_name": _TARGETS[team_id],
                "team_members": [],
                "updated_at": datetime.utcnow(),
                "created_at": datetime.utcnow(),
            }
        db.collection(_COLLECTION).document(team_id).set(payload, merge=True)
        moved += 1

    keep_ids = {"PC2", "MES2", _TEAM_LIST_DOC}
    for doc in docs:
        if doc.id in keep_ids:
            continue
        db.collection(_COLLECTION).document(doc.id).delete()
        deleted += 1

    db.collection(_COLLECTION).document(_TEAM_LIST_DOC).set(
        {
            "teams": [
                {"id": "PC2", "name": "PC2팀"},
                {"id": "MES2", "name": "MES2팀"},
            ],
            "updated_at": datetime.utcnow(),
        },
        merge=True,
    )

    print(f"완료: 타깃 반영 {moved}건, 삭제 {deleted}건, team_list 재생성 완료")


if __name__ == "__main__":
    main()
