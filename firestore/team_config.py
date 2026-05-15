"""팀 단위 주간업무보고 설정 저장/조회."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .writes import get_client

_CONFIG_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"
GLOBAL_SETTING_FIELDS = {
    "user_email",
    "api_token",
    "confluence_url",
    "page_restrict_to_email",
    "page_restrict_account_id",
}
TEAM_ROW_FIXED_FIELDS = {
    "id",
    "name",
    "calendar_id",
    "space_key",
    "confluence_space_key",
    "report_root_page_id",
    "root_pages",
    "shared_drive_ids",
    "template_page_url",
    "template_page_id",
}
TEAM_SETTING_FIELDS = {
    "calendar_id",
    "confluence_space_key",
    "report_root_page_id",
    "space_key",
    "root_pages",
    "shared_drive_ids",
    "template_page_url",
    "template_page_id",
}


def normalize_team_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    if normalized in {"PC2", "MES2"}:
        return normalized
    return normalized or text.upper()


def make_team_id(team_name: str) -> str:
    raw = (team_name or "").strip()
    compact = re.sub(r"\s+", "", raw).upper()
    if "PC2" in compact:
        return "PC2"
    if "MES2" in compact:
        return "MES2"
    base = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]+", "_", raw)
    base = re.sub(r"_+", "_", base).strip("_")
    return normalize_team_id(base or "TEAM")


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
    return [{"name": name, "nickname": [], "email": ""} for name in parts]


def parse_shared_drive_ids(raw_value: str) -> list[str]:
    """줄바꿈/쉼표 구분 입력값 → Shared Drive ID 리스트.

    URL 이 들어와도 ``/folders/<id>`` 패턴에서 ID 만 뽑아내고, 그렇지 않으면
    공백 제거 후 그대로 반환. 빈 값은 무시.
    """
    parts = [p.strip() for p in re.split(r"[,\n]", raw_value or "") if p.strip()]
    if not parts:
        return []
    out: list[str] = []
    for part in parts:
        m = re.search(r"/folders/([^/?#\s]+)", part)
        if m:
            out.append(m.group(1))
        else:
            out.append(part)
    return out


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


def _empty_team_row(team_id: str, team_name: str) -> dict[str, Any]:
    return {
        "id": team_id,
        "name": (team_name or team_id).strip() or team_id,
        "calendar_id": "",
        "space_key": "",
        "confluence_space_key": "",
        "report_root_page_id": "",
        "root_pages": [],
        "shared_drive_ids": [],
        "template_page_url": "",
        "template_page_id": "",
    }


def _normalize_team_row(row: dict[str, Any], *, team_id: str, team_name: str) -> dict[str, Any]:
    base = _empty_team_row(team_id, team_name)
    for key in TEAM_ROW_FIXED_FIELDS:
        if key in row:
            base[key] = row.get(key)
    base["id"] = team_id
    base["name"] = (str(base.get("name") or "").strip() or team_name or team_id).strip()
    base["root_pages"] = base.get("root_pages") if isinstance(base.get("root_pages"), list) else []
    base["shared_drive_ids"] = (
        [str(x).strip() for x in base.get("shared_drive_ids") if str(x).strip()]
        if isinstance(base.get("shared_drive_ids"), list)
        else []
    )
    for key in ("calendar_id", "space_key", "confluence_space_key", "report_root_page_id", "template_page_url", "template_page_id"):
        base[key] = str(base.get(key) or "").strip()
    return base


def _read_team_list_doc() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if not snap.exists:
        return {}, []
    data = snap.to_dict() or {}
    teams = [t for t in (data.get("teams") or []) if isinstance(t, dict)]
    return data, teams


def get_global_settings() -> dict[str, Any]:
    data, _ = _read_team_list_doc()
    global_data = data.get("global")
    if not isinstance(global_data, dict):
        return {}
    return {k: global_data.get(k) for k in GLOBAL_SETTING_FIELDS if k in global_data}


def upsert_global_settings(updates: dict[str, Any]) -> None:
    clean = {k: updates.get(k) for k in GLOBAL_SETTING_FIELDS if k in (updates or {})}
    if not clean:
        return
    db = get_client()
    db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC).set({"global": clean}, merge=True)


def _upsert_team_list(team_id: str, team_name: str) -> None:
    team_id = normalize_team_id(team_id)
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        teams = list(data.get("teams") or [])
    else:
        teams = []

    updated = False
    exists = False
    for item in teams:
        if not isinstance(item, dict):
            continue
        if normalize_team_id(item.get("id", "")) != team_id:
            continue
        exists = True
        normalized = _normalize_team_row(item, team_id=team_id, team_name=team_name)
        if normalized != item:
            item.clear()
            item.update(normalized)
            updated = True
        break
    if not exists:
        teams.append(_empty_team_row(team_id, team_name))
        updated = True
    if updated:
        ref.set({"teams": teams}, merge=True)


def _get_team_list_entry(team_id: str) -> dict[str, Any] | None:
    team_id = normalize_team_id(team_id)
    if not team_id:
        return None
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    for item in data.get("teams") or []:
        if not isinstance(item, dict):
            continue
        if normalize_team_id(item.get("id", "")) == team_id:
            return item
    return None


def get_team_settings(team_id: str) -> dict[str, Any]:
    entry = _get_team_list_entry(team_id)
    if not entry:
        return {}
    out: dict[str, Any] = {}
    for key in TEAM_SETTING_FIELDS:
        if key in entry:
            out[key] = entry.get(key)
    return out


def upsert_team_settings(team_id: str, team_name: str, updates: dict[str, Any]) -> None:
    team_id = normalize_team_id(team_id)
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        teams = list(data.get("teams") or [])
    else:
        teams = []

    row_updates = {k: v for k, v in (updates or {}).items() if k in TEAM_SETTING_FIELDS}
    global_updates = {k: v for k, v in (updates or {}).items() if k in GLOBAL_SETTING_FIELDS}
    found = False
    for item in teams:
        if not isinstance(item, dict):
            continue
        if normalize_team_id(item.get("id", "")) != team_id:
            continue
        found = True
        merged_row = dict(item)
        merged_row.update(row_updates)
        normalized = _normalize_team_row(merged_row, team_id=team_id, team_name=team_name)
        item.clear()
        item.update(normalized)
        break
    if not found:
        row: dict[str, Any] = _empty_team_row(team_id, team_name)
        row.update(row_updates)
        row = _normalize_team_row(row, team_id=team_id, team_name=team_name)
        teams.append(row)
    ref.set({"teams": teams}, merge=True)
    if global_updates:
        upsert_global_settings(global_updates)


def get_team_list() -> list[dict[str, str]]:
    """team_list 문서를 우선 사용하고, 없으면 config 문서들에서 폴백 생성."""
    db = get_client()
    existing_doc_ids = {
        normalize_team_id(doc.id)
        for doc in db.collection(_CONFIG_COLLECTION).stream()
        if doc.id != _TEAM_LIST_DOC
    }
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    teams: list[dict[str, str]] = []
    if snap.exists:
        data = snap.to_dict() or {}
        for item in data.get("teams") or []:
            if not isinstance(item, dict):
                continue
            team_id = normalize_team_id(str(item.get("id") or ""))
            team_name = str(item.get("name") or "").strip()
            if team_id and team_name and team_id in existing_doc_ids:
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
            teams.append({"id": normalize_team_id(doc.id), "name": team_name})
    teams.sort(key=lambda x: x["name"])
    return teams


def rename_team_in_list(team_id: str, new_team_name: str) -> None:
    """team_list에서 팀 이름을 갱신합니다."""
    team_id = normalize_team_id(team_id)
    new_name = (new_team_name or "").strip()
    if not new_name:
        return
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if not snap.exists:
        return
    data = snap.to_dict() or {}
    teams = list(data.get("teams") or [])
    changed = False
    for item in teams:
        if not isinstance(item, dict):
            continue
        if normalize_team_id(item.get("id", "")) != team_id:
            continue
        item["name"] = new_name
        changed = True
        break
    if changed:
        ref.set({"teams": teams}, merge=True)


def remove_team_from_list(team_id: str) -> None:
    """team_list에서 팀 항목을 제거합니다."""
    team_id = normalize_team_id(team_id)
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(_TEAM_LIST_DOC)
    snap = ref.get()
    if not snap.exists:
        return
    data = snap.to_dict() or {}
    teams = [
        item
        for item in (data.get("teams") or [])
        if isinstance(item, dict) and normalize_team_id(item.get("id", "")) != team_id
    ]
    ref.set({"teams": teams}, merge=True)


def get_team_config(team_id: str) -> dict[str, Any] | None:
    team_id = normalize_team_id(team_id)
    db = get_client()
    snap = db.collection(_CONFIG_COLLECTION).document(team_id).get()
    if not snap.exists:
        return None
    out = snap.to_dict() or {}
    entry = _get_team_list_entry(team_id) or {}
    for key in TEAM_SETTING_FIELDS:
        if key in entry:
            out[key] = entry.get(key)
    global_settings = get_global_settings()
    for key in GLOBAL_SETTING_FIELDS:
        if key in global_settings:
            out[key] = global_settings.get(key)
    if entry.get("name") and not out.get("team_name"):
        out["team_name"] = str(entry.get("name") or "").strip()
    return out


def upsert_team_config(
    *,
    team_id: str,
    team_name: str,
    space_id: str,
    user_context: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    team_id = normalize_team_id(team_id)
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(team_id)
    snap = ref.get()
    now = datetime.utcnow()

    raw_updates = updates or {}
    member_updates = {k: v for k, v in raw_updates.items() if k not in TEAM_SETTING_FIELDS}
    settings_updates = {k: v for k, v in raw_updates.items() if k in TEAM_SETTING_FIELDS}

    payload: dict[str, Any] = {
        "team_name": (team_name or "").strip(),
        "space_id": (space_id or "").strip(),
        "user_context": _normalized_user_context(user_context),
        "updated_at": now,
    }
    payload.update(member_updates)

    if snap.exists:
        ref.set(payload, merge=True)
    else:
        payload["created_at"] = now
        ref.set(payload, merge=True)

    _upsert_team_list(team_id, team_name)
    if settings_updates:
        upsert_team_settings(team_id, team_name, settings_updates)
    return ref.get().to_dict() or {}


def update_team_name(
    *,
    team_id: str,
    new_team_name: str,
    space_id: str,
    user_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """팀 이름을 변경하고 team_list도 동기화합니다."""
    team_id = normalize_team_id(team_id)
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
    team_id = normalize_team_id(team_id)
    db = get_client()
    ref = db.collection(_CONFIG_COLLECTION).document(team_id)
    snap = ref.get()
    if not snap.exists:
        return False
    ref.delete()
    remove_team_from_list(team_id)
    return True


def update_team_calendar_id(
    *,
    team_id: str,
    calendar_id: str,
    space_id: str,
    user_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """팀의 Google Calendar ID를 갱신합니다."""
    team_id = normalize_team_id(team_id)
    existing = get_team_config(team_id)
    if not existing:
        raise ValueError("존재하지 않는 팀입니다.")
    team_name = str(existing.get("team_name") or team_id)
    return upsert_team_config(
        team_id=team_id,
        team_name=team_name,
        space_id=space_id,
        user_context=user_context,
        updates={"calendar_id": (calendar_id or "").strip()},
    )


def update_team_shared_drive_ids(
    *,
    team_id: str,
    shared_drive_ids: list[str],
    space_id: str,
    user_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """팀의 Shared Drive ID 리스트를 갱신합니다 (전체 덮어쓰기)."""
    team_id = normalize_team_id(team_id)
    existing = get_team_config(team_id)
    if not existing:
        raise ValueError("존재하지 않는 팀입니다.")
    team_name = str(existing.get("team_name") or team_id)
    cleaned = [str(x).strip() for x in (shared_drive_ids or []) if str(x).strip()]
    return upsert_team_config(
        team_id=team_id,
        team_name=team_name,
        space_id=space_id,
        user_context=user_context,
        updates={"shared_drive_ids": cleaned},
    )


# ---------------------------------------------------------------------------
# weekly_report 도메인 진입용 — 이메일 → 팀 매칭 / 글로벌 토큰
# ---------------------------------------------------------------------------


def get_team_from_index(team_id: str) -> dict[str, Any] | None:
    """`config/team_list.teams[]` 의 row 반환. ``_get_team_list_entry`` 의 public alias."""
    return _get_team_list_entry(team_id)


def find_team_by_email(email: str) -> str | None:
    """이메일이 속한 팀 ID 1개 (여러 팀에 있으면 첫 번째 발견)."""
    if not email:
        return None
    needle = email.strip().lower()
    db = get_client()
    docs = db.collection(_CONFIG_COLLECTION).stream()
    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        members = data.get("team_members") or []
        for m in members:
            if isinstance(m, dict) and str(m.get("email") or "").strip().lower() == needle:
                return doc.id
    return None


def find_all_teams_by_email(email: str) -> list[str]:
    """이메일이 속한 모든 팀 ID 리스트 (OAuth 동의/철회 sync 용)."""
    if not email:
        return []
    needle = email.strip().lower()
    db = get_client()
    docs = db.collection(_CONFIG_COLLECTION).stream()
    result: list[str] = []
    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        members = data.get("team_members") or []
        for m in members:
            if isinstance(m, dict) and str(m.get("email") or "").strip().lower() == needle:
                result.append(doc.id)
                break
    return result


def get_token_for(user_email: str = "") -> dict[str, str]:
    """사용자 이메일이 사용할 Confluence 인증 정보.

    현재 모델: 글로벌 1개 토큰 (`config/team_list.global`). 미래에 팀별로 나뉘면
    이 함수만 교체 (호출 인터페이스 유지).
    """
    g = get_global_settings()
    return {
        "url": str(g.get("confluence_url") or "").strip().rstrip("/"),
        "user_email": str(g.get("user_email") or "").strip(),
        "api_token": str(g.get("api_token") or "").strip(),
    }
