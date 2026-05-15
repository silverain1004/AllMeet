"""Confluence Cloud accountId 캐시 (이메일 → accountId).

Atlassian Cloud 는 GDPR 이후 CQL에서 ``contributor.email`` 을 받지 않고
``contributor.accountid`` 만 받음. 매 호출마다 user search REST 를 때리지 않도록
``confluence_users/{user_email}`` 한 곳에 결과를 캐싱.

스키마:
- account_id: str
- display_name: str
- resolved_at: timestamp
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .writes import get_client

_COLLECTION = "confluence_users"


def get_cached(user_email: str) -> dict[str, Any] | None:
    """캐시된 ``{account_id, display_name, resolved_at}`` dict. 없으면 None."""
    if not user_email:
        return None
    db = get_client()
    snap = db.collection(_COLLECTION).document(user_email).get()
    if not snap.exists:
        return None
    return snap.to_dict() or None


def save(user_email: str, *, account_id: str, display_name: str = "") -> None:
    """resolve 성공 시 1회 저장 (덮어쓰기). 빈 account_id 는 저장하지 않음."""
    if not user_email or not account_id:
        return
    db = get_client()
    db.collection(_COLLECTION).document(user_email).set(
        {
            "account_id": account_id,
            "display_name": display_name or "",
            "resolved_at": datetime.utcnow(),
        }
    )
