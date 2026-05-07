from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from firestore.writes import get_client

_COLLECTION = "config"
_TEAM_LIST_DOC = "team_list"


def _normalize_name(name: str) -> str:
    text = (name or "").strip()
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.replace(" ", "")
    text = text.replace("\u200b", "")
    return text


PC2_EMAILS = {
    "김경원": "kkw@vntgcorp.com",
    "박광준": "gwangjun.park@vntgcorp.com",
    "정동원": "dongwon@vntgcorp.com",
    "김성훈": "shkim0609@vntgcorp.com",
    "이수종": "isz56715@vntgcorp.com",
    "서영은": "kkami4182@vntgcorp.com",
    "최은비": "silverain@vntgcorp.com",
    "김태표": "e50271@vntgcorp.com",
    "홍현기": "hyeongi.hong@vntgcorp.com",
    "채희민": "hmchae@vntgcorp.com",
}

MES2_EMAILS = {
    "이민규": "lmk1984@vntgcorp.com",
    "정민수": "jeongminsu97@vntgcorp.com",
    "이승균": "seungkyun.lee@vntgcorp.com",
    "박영민": "benny_park@vntgcorp.com",
    "박용구": "dball@vntgcorp.com",
    "주웅택": "wtju@vntgcorp.com",
    "송원용": "wonyong.song@vntgcorp.com",
    "엄익준": "uhmikjun@vntgcorp.com",
    "이재섭": "zipcwal79@vntgcorp.com",
    "이정인": "lee0930@vntgcorp.com",
    "최지원": "laian1422@vntgcorp.com",
    "고진영": "kai.jin0806@vntgcorp.com",
}

TEAM_EMAILS = {
    "PC2팀": PC2_EMAILS,
    "MES2팀": MES2_EMAILS,
}


def _normalize_members(raw_members: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        return []
    out: list[dict[str, Any]] = []
    for member in raw_members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        raw_nick = member.get("nickname")
        if isinstance(raw_nick, list):
            nick = [str(n).strip() for n in raw_nick if str(n).strip()]
        elif raw_nick is None:
            nick = []
        else:
            nick = [str(raw_nick).strip()] if str(raw_nick).strip() else []
        email = str(member.get("email") or "").strip()
        out.append({"name": name, "nickname": nick, "email": email})
    return out


def _target_mapping(team_name: str) -> dict[str, str] | None:
    normalized = _normalize_name(team_name)
    if "pc2" in normalized.lower() or normalized == _normalize_name("PC2팀"):
        return PC2_EMAILS
    if "mes2" in normalized.lower() or normalized == _normalize_name("MES2팀"):
        return MES2_EMAILS
    return TEAM_EMAILS.get(team_name)


def main() -> None:
    db = get_client()
    docs = db.collection(_COLLECTION).stream()

    updated_docs = 0
    updated_members = 0
    added_members = 0

    for doc in docs:
        if doc.id == _TEAM_LIST_DOC:
            continue
        data = doc.to_dict() or {}
        team_name = str(data.get("team_name") or doc.id).strip()
        mapping = _target_mapping(team_name)
        if not mapping:
            continue

        normalized_mapping = {_normalize_name(k): v for k, v in mapping.items()}
        members = _normalize_members(data.get("team_members"))

        changed = False
        existing_keys = {_normalize_name(str(m.get("name") or "")) for m in members}

        for member in members:
            key = _normalize_name(str(member.get("name") or ""))
            email = normalized_mapping.get(key, "")
            if email and not str(member.get("email") or "").strip():
                member["email"] = email
                updated_members += 1
                changed = True

        for key, email in normalized_mapping.items():
            if key in existing_keys:
                continue
            members.append({"name": key, "nickname": [], "email": email})
            added_members += 1
            changed = True

        if changed:
            db.collection(_COLLECTION).document(doc.id).set(
                {
                    "team_members": members,
                    "updated_at": datetime.utcnow(),
                },
                merge=True,
            )
            updated_docs += 1

    print(f"완료: 업데이트 문서 {updated_docs}개, 이메일 채움 {updated_members}명, 신규 추가 {added_members}명")


if __name__ == "__main__":
    main()
