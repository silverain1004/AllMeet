"""config/{TEAM_ID} 설정 필드를 config/team_list.teams[]로 이관."""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from google.cloud import firestore

from firestore.writes import get_client
from firestore.team_config import (
    GLOBAL_SETTING_FIELDS,
    TEAM_ROW_FIXED_FIELDS,
    TEAM_SETTING_FIELDS,
    normalize_team_id,
)

CONFIG_COLLECTION = "config"
TEAM_LIST_DOC = "team_list"
TARGET_TEAMS = ("PC2", "MES2")


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _read_team_list(db) -> list[dict[str, Any]]:
    snap = db.collection(CONFIG_COLLECTION).document(TEAM_LIST_DOC).get()
    if not snap.exists:
        return []
    data = snap.to_dict() or {}
    out: list[dict[str, Any]] = []
    for item in data.get("teams") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["id"] = normalize_team_id(row.get("id", ""))
        if row["id"]:
            out.append(row)
    return out


def _normalize_team_row(row: dict[str, Any], team_id: str) -> dict[str, Any]:
    normalized = {k: row.get(k) for k in TEAM_ROW_FIXED_FIELDS}
    normalized["id"] = team_id
    normalized["name"] = str(normalized.get("name") or team_id).strip() or team_id
    for key in ("calendar_id", "space_key", "confluence_space_key", "report_root_page_id", "template_page_url", "template_page_id"):
        normalized[key] = str(normalized.get(key) or "").strip()
    normalized["root_pages"] = normalized.get("root_pages") if isinstance(normalized.get("root_pages"), list) else []
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate team setting fields to config/team_list.teams[]")
    parser.add_argument("--apply", action="store_true", help="실제 반영 (기본은 dry-run)")
    args = parser.parse_args()

    db = get_client()
    teams = _read_team_list(db)
    teams_by_id = {t.get("id"): t for t in teams}
    changed_rows = 0
    stripped_docs = 0
    global_changed = False
    global_data: dict[str, Any] = {}

    # 기존 teams[]에 들어가 있던 공통값을 먼저 global 후보로 수집
    for row in teams:
        for key in GLOBAL_SETTING_FIELDS:
            src = row.get(key)
            if _is_blank(src):
                continue
            if key not in global_data or _is_blank(global_data.get(key)):
                global_data[key] = src
                global_changed = True
            elif global_data.get(key) != src:
                print(f"[WARN] global 충돌: teams[].{key} 값이 서로 다릅니다. 기존값 유지")

    for team_id in TARGET_TEAMS:
        team_id = normalize_team_id(team_id)
        doc_ref = db.collection(CONFIG_COLLECTION).document(team_id)
        snap = doc_ref.get()
        if not snap.exists:
            print(f"[SKIP] config/{team_id} 없음")
            continue
        data = snap.to_dict() or {}

        row = teams_by_id.get(team_id)
        if row is None:
            row = {"id": team_id, "name": str(data.get("team_name") or team_id)}
            teams.append(row)
            teams_by_id[team_id] = row

        updated = False
        for key in TEAM_SETTING_FIELDS:
            src = data.get(key)
            dst = row.get(key)
            if _is_blank(dst) and not _is_blank(src):
                row[key] = src
                updated = True
        for key in GLOBAL_SETTING_FIELDS:
            src = data.get(key)
            if _is_blank(src):
                continue
            if key not in global_data or _is_blank(global_data.get(key)):
                global_data[key] = src
                global_changed = True
            elif global_data.get(key) != src:
                print(f"[WARN] global 충돌: {team_id}.{key} 값이 기존 global과 다릅니다. 기존값 유지")
        normalized = _normalize_team_row(row, team_id)
        if normalized != row:
            row.clear()
            row.update(normalized)
            updated = True
        if updated:
            changed_rows += 1

        delete_payload = {
            key: firestore.DELETE_FIELD
            for key in (TEAM_SETTING_FIELDS | GLOBAL_SETTING_FIELDS)
            if key in data
        }
        if delete_payload:
            delete_payload["updated_at"] = datetime.utcnow()
            stripped_docs += 1
            if args.apply:
                doc_ref.update(delete_payload)

    if args.apply:
        payload: dict[str, Any] = {"teams": teams}
        if global_data:
            payload["global"] = global_data
        db.collection(CONFIG_COLLECTION).document(TEAM_LIST_DOC).set(payload, merge=True)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] team_list 변경 대상 행: {changed_rows}")
    print(f"[{mode}] team 문서 설정필드 제거 대상: {stripped_docs}")
    print(f"[{mode}] global 필드 갱신 여부: {global_changed}")
    if not args.apply:
        print("실제 반영: python -m tests.migrate_team_settings_to_team_list --apply")


if __name__ == "__main__":
    main()

