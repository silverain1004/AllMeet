from __future__ import annotations

import argparse
import re
from datetime import datetime
from typing import Any

from firestore.writes import get_client

_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_nicknames(raw_value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n]", raw_value or "") if p.strip()]


def _nicknames_to_text(member: dict[str, Any]) -> str:
    raw = member.get("nickname")
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif raw is None:
        items = []
    else:
        items = _normalize_nicknames(str(raw))
    return ", ".join(items)


def _normalize_members(raw_members: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_members:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        raw_nickname = raw.get("nickname")
        if isinstance(raw_nickname, list):
            nickname = [str(x).strip() for x in raw_nickname if str(x).strip()]
        elif raw_nickname is None:
            nickname = []
        else:
            nickname = _normalize_nicknames(str(raw_nickname))
        out.append({"name": name, "nickname": nickname})
    return out


def _team_doc_ref(team_id: str):
    return get_client().collection(_COLLECTION).document(team_id)


def _team_list_ref():
    return get_client().collection(_COLLECTION).document(_TEAM_LIST_DOC)


def _load_team_list() -> list[dict[str, str]]:
    ref = _team_list_ref()
    snap = ref.get()
    teams: list[dict[str, str]] = []
    if snap.exists:
        data = snap.to_dict() or {}
        for item in data.get("teams") or []:
            if isinstance(item, dict):
                team_id = str(item.get("id") or "").strip()
                name = str(item.get("name") or "").strip()
                if team_id and name:
                    teams.append({"id": team_id, "name": name})
    if teams:
        return teams

    # team_list가 없을 때를 위한 폴백.
    docs = get_client().collection(_COLLECTION).stream()
    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        name = str(data.get("team_name") or doc.id).strip()
        teams.append({"id": doc.id, "name": name})
    teams.sort(key=lambda x: x["name"])
    return teams


def _save_team_list(teams: list[dict[str, str]]) -> None:
    _team_list_ref().set({"teams": teams, "updated_at": _now()}, merge=True)


def _get_team_doc(team_id: str) -> dict[str, Any]:
    snap = _team_doc_ref(team_id).get()
    if not snap.exists:
        return {}
    return snap.to_dict() or {}


def _upsert_team_doc(team_id: str, data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["updated_at"] = _now()
    _team_doc_ref(team_id).set(payload, merge=True)


def _print_teams(teams: list[dict[str, str]]) -> None:
    if not teams:
        print("등록된 팀이 없습니다.")
        return
    print("팀 목록:")
    for i, team in enumerate(teams, start=1):
        print(f"  {i}. {team['name']}")


def _pick_team(teams: list[dict[str, str]]) -> dict[str, str] | None:
    if not teams:
        print("등록된 팀이 없습니다.")
        return None
    _print_teams(teams)
    raw = input("팀을 선택하세요 (번호): ").strip()
    if not raw.isdigit():
        print("번호를 입력해 주세요.")
        return None
    idx = int(raw)
    if idx < 1 or idx > len(teams):
        print("올바른 번호가 아닙니다.")
        return None
    return teams[idx - 1]


def _print_members(team_name: str, members: list[dict[str, Any]]) -> None:
    print(f"\n[{team_name}] 팀원 ({len(members)}명)")
    print("-" * 50)
    if not members:
        print("  (등록된 팀원이 없습니다)")
        return
    for i, member in enumerate(members, start=1):
        nicknames = _nicknames_to_text(member)
        if nicknames:
            print(f"  {i}. {member['name']} (닉네임: {nicknames})")
        else:
            print(f"  {i}. {member['name']}")


def _action_list(teams: list[dict[str, str]]) -> None:
    team = _pick_team(teams)
    if not team:
        return
    doc = _get_team_doc(team["id"])
    members = _normalize_members(doc.get("team_members"))
    _print_members(team["name"], members)
    print("")


def _action_register(teams: list[dict[str, str]]) -> list[dict[str, str]]:
    print("\n--- 등록 ---")
    print("  1. 팀원 등록  2. 팀 등록  (그 외: 이전)")
    choice = input("선택: ").strip()
    if choice == "1":
        team = _pick_team(teams)
        if not team:
            return teams
        name = input("이름: ").strip()
        if not name:
            print("이름은 필수입니다.")
            return teams
        nicknames = _normalize_nicknames(input("닉네임 (쉼표 구분, 선택): "))
        doc = _get_team_doc(team["id"])
        members = _normalize_members(doc.get("team_members"))
        members.append({"name": name, "nickname": nicknames})
        _upsert_team_doc(team["id"], {"team_name": team["name"], "team_members": members})
        print("✅ 등록되었습니다.")
        return teams

    if choice == "2":
        print("\n=== 팀 추가 ===")
        team_id = input("팀 ID (영문/숫자, 예: mes2): ").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]+", team_id):
            print("팀 ID는 영문 소문자/숫자/_/- 만 가능합니다.")
            return teams
        if any(t["id"] == team_id for t in teams):
            print("이미 존재하는 팀 ID입니다.")
            return teams
        team_name = input("팀 이름: ").strip()
        if not team_name:
            print("팀 이름은 필수입니다.")
            return teams
        teams.append({"id": team_id, "name": team_name})
        _save_team_list(teams)
        _upsert_team_doc(team_id, {"team_name": team_name, "team_members": [], "setup_completed": False})
        print("✅ 팀이 등록되었습니다.")
        return teams

    return teams


def _action_edit(teams: list[dict[str, str]]) -> None:
    team = _pick_team(teams)
    if not team:
        return
    doc = _get_team_doc(team["id"])
    members = _normalize_members(doc.get("team_members"))
    _print_members(team["name"], members)
    if not members:
        print("")
        return
    raw = input("\n수정할 팀원 번호를 입력하세요: ").strip()
    if not raw.isdigit():
        print("번호를 입력해 주세요.")
        return
    idx = int(raw) - 1
    if idx < 0 or idx >= len(members):
        print("올바른 번호가 아닙니다.")
        return
    target = members[idx]
    new_name = input(f"이름 (현재: {target['name']}, 변경 없으면 엔터): ").strip()
    current_nicks = _nicknames_to_text(target)
    new_nick_raw = input(f"닉네임 (현재: {current_nicks or '-'}, 변경 없으면 엔터, 여러 개는 쉼표 구분): ").strip()
    if new_name:
        target["name"] = new_name
    if new_nick_raw:
        target["nickname"] = _normalize_nicknames(new_nick_raw)
    _upsert_team_doc(team["id"], {"team_members": members})
    print("✅ 수정되었습니다.")


def _action_reorder(teams: list[dict[str, str]]) -> None:
    team = _pick_team(teams)
    if not team:
        return
    doc = _get_team_doc(team["id"])
    members = _normalize_members(doc.get("team_members"))
    _print_members(team["name"], members)
    if len(members) < 2:
        print("\n순서를 바꿀 팀원이 충분하지 않습니다.")
        return
    print("\n새 순서를 현재 번호로 입력하세요 (예: 3 1 2 4 -> 3번을 1번 자리로).")
    raw = input("순서 (공백 또는 쉼표 구분): ").strip()
    tokens = [p for p in re.split(r"[\s,]+", raw) if p]
    if len(tokens) != len(members) or not all(t.isdigit() for t in tokens):
        print("팀원 수와 동일한 번호를 입력해 주세요.")
        return
    order = [int(t) for t in tokens]
    if sorted(order) != list(range(1, len(members) + 1)):
        print("번호는 1부터 팀원 수까지 중복 없이 입력해야 합니다.")
        return
    reordered = [members[i - 1] for i in order]
    _upsert_team_doc(team["id"], {"team_members": reordered})
    print("✅ 순서가 변경되었습니다.")


def _action_delete(teams: list[dict[str, str]]) -> None:
    team = _pick_team(teams)
    if not team:
        return
    doc = _get_team_doc(team["id"])
    members = _normalize_members(doc.get("team_members"))
    _print_members(team["name"], members)
    if not members:
        print("")
        return
    raw = input("\n삭제할 팀원 번호를 입력하세요: ").strip()
    if not raw.isdigit():
        print("번호를 입력해 주세요.")
        return
    idx = int(raw) - 1
    if idx < 0 or idx >= len(members):
        print("올바른 번호가 아닙니다.")
        return
    removed = members.pop(idx)
    _upsert_team_doc(team["id"], {"team_members": members})
    print(f"✅ 삭제되었습니다: {removed['name']}")


def _action_rename_team(teams: list[dict[str, str]]) -> list[dict[str, str]]:
    team = _pick_team(teams)
    if not team:
        return teams
    print(f"현재 팀 이름: {team['name']} (config/{team['id']})")
    new_name = input("새 팀 이름을 입력하세요 (엔터 시 취소): ").strip()
    if not new_name:
        print("취소되었습니다.")
        return teams
    team["name"] = new_name
    _save_team_list(teams)
    _upsert_team_doc(team["id"], {"team_name": new_name})
    print("✅ 팀 이름이 변경되었습니다.")
    return teams


def _show_menu() -> None:
    print("=" * 40)
    print("  팀원 관리")
    print("  1. 조회  2. 등록  3. 수정  4. 순서 변경  5. 삭제  6. 팀 이름 변경  (그 외: 종료)")
    print("=" * 40)


def _run_interactive() -> None:
    teams = _load_team_list()
    while True:
        _show_menu()
        choice = input("선택: ").strip()
        if choice == "1":
            _action_list(teams)
        elif choice == "2":
            teams = _action_register(teams)
        elif choice == "3":
            _action_edit(teams)
        elif choice == "4":
            _action_reorder(teams)
        elif choice == "5":
            _action_delete(teams)
        elif choice == "6":
            teams = _action_rename_team(teams)
        else:
            print("종료합니다.")
            break


def _run_list_mode() -> None:
    teams = _load_team_list()
    if not teams:
        print("등록된 팀이 없습니다.")
        return
    first = teams[0]
    doc = _get_team_doc(first["id"])
    members = _normalize_members(doc.get("team_members"))
    _print_members(first["name"], members)


def main() -> None:
    parser = argparse.ArgumentParser(description="팀원 관리 CLI")
    parser.add_argument("--list", action="store_true", help="첫 번째 팀의 팀원 목록만 출력")
    args = parser.parse_args()
    if args.list:
        _run_list_mode()
    else:
        _run_interactive()


if __name__ == "__main__":
    main()
