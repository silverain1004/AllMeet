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

import threading
import time
from datetime import datetime
from typing import Any

from .writes import get_client

_COLLECTION = "oauth_tokens"

# compose 카드 1회 렌더에서 get_token 이 3회 이상 불린다(is_oauth_linked / 토큰 발급 /
# 캘린더 목록). 문서 내용은 연결·해제 때만 바뀌므로 짧은 TTL 캐시로 왕복을 없앤다.
_DOC_TTL_SEC = 60
_doc_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_doc_lock = threading.Lock()


def clear_token_cache(user_email: str | None = None) -> None:
    """토큰 문서 캐시 무효화. user_email 이 없으면 전체."""
    with _doc_lock:
        if user_email:
            _doc_cache.pop(user_email, None)
        else:
            _doc_cache.clear()


def save_token(*, user_email: str, refresh_token: str, scopes: list[str]) -> None:
    """동의 callback 직후 신규 저장 (또는 재동의 시 덮어쓰기)."""
    clear_token_cache(user_email)
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
    """저장된 토큰 dict 반환. 없으면 None. (60초 TTL 캐시)"""
    if not user_email:
        return None
    now = time.monotonic()
    with _doc_lock:
        cached = _doc_cache.get(user_email)
        if cached and (now - cached[0]) < _DOC_TTL_SEC:
            return dict(cached[1]) if cached[1] is not None else None
    db = get_client()
    snap = db.collection(_COLLECTION).document(user_email).get()
    doc = (snap.to_dict() or None) if snap.exists else None
    with _doc_lock:
        _doc_cache[user_email] = (now, dict(doc) if doc is not None else None)
    return dict(doc) if doc is not None else None


def update_status(user_email: str, status: str) -> None:
    """status 만 갱신 — 만료/거부 감지 시 호출."""
    clear_token_cache(user_email)
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
