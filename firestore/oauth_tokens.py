"""사용자 OAuth refresh_token 저장/조회 (Phase 2).

`oauth_tokens/{user_email}` 컬렉션 1곳에만 저장. 사용자 단위 자산이라 팀 문서에 묻지 않음
(한 사람이 여러 팀에 속해도 토큰은 하나).

스키마:
- refresh_token: str
- scopes: list[str]
- granted_at: timestamp (최초 동의)
- last_refreshed_at: timestamp
- status: "linked" | "expired" | "revoked"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .writes import get_client

_COLLECTION = "oauth_tokens"


def save_token(*, user_email: str, refresh_token: str, scopes: list[str]) -> None:
    """동의 callback 직후 신규 저장 (또는 재동의 시 덮어쓰기)."""
    db = get_client()
    now = datetime.utcnow()
    db.collection(_COLLECTION).document(user_email).set(
        {
            "refresh_token": refresh_token,
            "scopes": scopes,
            "granted_at": now,
            "last_refreshed_at": now,
            "status": "linked",
        }
    )


def get_token(user_email: str) -> dict[str, Any] | None:
    """저장된 토큰 dict 반환. 없으면 None."""
    if not user_email:
        return None
    db = get_client()
    snap = db.collection(_COLLECTION).document(user_email).get()
    if not snap.exists:
        return None
    return snap.to_dict() or None


def update_status(user_email: str, status: str) -> None:
    """status 만 갱신 — 만료/거부 감지 시 호출."""
    db = get_client()
    ref = db.collection(_COLLECTION).document(user_email)
    if not ref.get().exists:
        return
    ref.update({"status": status, "last_refreshed_at": datetime.utcnow()})


def touch_refreshed(user_email: str) -> None:
    """access_token 재발급 성공 시 last_refreshed_at 만 갱신."""
    db = get_client()
    ref = db.collection(_COLLECTION).document(user_email)
    if not ref.get().exists:
        return
    ref.update({"last_refreshed_at": datetime.utcnow()})


def revoke(user_email: str) -> None:
    """사용자가 명시적으로 봇 권한 철회 시."""
    update_status(user_email, "revoked")
