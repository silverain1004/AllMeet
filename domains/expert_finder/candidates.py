"""후보자 명단 빌더 (팀 무관 · 전사).

PLAN.md §5.1 `candidates.py`.

- ``list_consenting_users`` — `oauth_tokens.status == "linked"` 갈래
- ``annotate_with_consent`` — 스코어 결과에 OAuth 동의자 여부 라벨링
- 두 갈래 합집합은 호출 측에서 (set ∪ set) 으로 처리하므로 별도 함수 불필요
"""

from __future__ import annotations

import logging
from typing import Any

from firestore.writes import get_client

logger = logging.getLogger(__name__)

_OAUTH_COLLECTION = "oauth_tokens"


def list_consenting_users() -> list[str]:
    """`oauth_tokens` 컬렉션에서 status="linked" 인 사용자 email 리스트.

    문서 ID 자체가 user_email (``firestore/oauth_tokens.save_token`` 참조).
    """
    db = get_client()
    out: list[str] = []
    try:
        for snap in db.collection(_OAUTH_COLLECTION).stream():
            data = snap.to_dict() or {}
            if str(data.get("status") or "").strip().lower() != "linked":
                continue
            email = (snap.id or "").strip()
            if email:
                out.append(email)
    except Exception:
        logger.exception("oauth_tokens 스트리밍 실패")
        return []
    logger.info("expert_finder 동의자 풀: %d명", len(out))
    return out


def annotate_with_consent(
    scored: list[dict[str, Any]],
    consenting_emails: list[str],
) -> list[dict[str, Any]]:
    """``score_candidates`` 결과에 ``is_consenting`` 라벨을 in-place 로 추가."""
    consenting_set = {(e or "").strip().lower() for e in consenting_emails or [] if e}
    for entry in scored or []:
        email = (entry.get("email") or "").strip().lower()
        entry["is_consenting"] = email in consenting_set
    return scored
