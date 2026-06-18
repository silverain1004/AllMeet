"""사용자 OAuth refresh_token → access_token 재발급 (Phase 2).

스케줄러·즉석 호출 모두 이 모듈로 credentials 받음. Gmail / 개인 Calendar 호출 진입점.

미동의·만료·폐기 시 ``AuthRequiredError`` 를 raise — 호출 측에서 부분 결손으로 처리.
"""

from __future__ import annotations

import logging
import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from config.settings import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
from firestore.oauth_tokens import get_token, touch_refreshed, update_status

logger = logging.getLogger(__name__)

# 캐시 키 — (user_email, sorted scopes tuple). scope 별로 access_token 이 달라지므로
# email 만으로 캐싱하면 다른 scope 호출이 폴루션 되어 403 발생.
_lock = threading.Lock()
_creds_cache: dict[tuple[str, tuple[str, ...]], Credentials] = {}


def _cache_key(user_email: str, scopes: list[str]) -> tuple[str, tuple[str, ...]]:
    return (user_email, tuple(sorted(scopes)))


def invalidate_cache(user_email: str) -> None:
    """해당 사용자의 모든 scope 캐시 제거. 재동의/권한철회 직후 호출 — 옛 access_token 즉시 폐기.

    이 인스턴스 한정(인메모리). 타 인스턴스는 ``get_user_credentials`` 의 refresh_token 대조로 무효화됨.
    """
    if not user_email:
        return
    with _lock:
        for k in list(_creds_cache.keys()):
            if k[0] == user_email:
                _creds_cache.pop(k, None)


class AuthRequiredError(Exception):
    """OAuth 미동의/만료/폐기 — 사용자에게 재동의 안내 필요."""

    def __init__(self, user_email: str, reason: str = "no_token"):
        super().__init__(f"oauth_required: {user_email} ({reason})")
        self.user_email = user_email
        self.reason = reason


# 함수 — refresh_token 으로 사용자 credentials 발급.
def get_user_credentials(user_email: str, scopes: list[str]) -> Credentials:
    """저장된 refresh_token 으로 access_token 재발급.

    Raises:
        AuthRequiredError: 토큰 없거나 status 가 linked 가 아니거나 refresh 실패.
    """
    if not user_email:
        raise AuthRequiredError("", reason="empty_email")

    # 캐시 히트라도 Firestore 의 refresh_token 과 대조 후 사용. 재동의(prompt=consent)로
    # 새 refresh_token 이 저장되면 옛 access_token 은 서버에서 폐기되므로, 만료 전이라
    # `cached.valid` 만 믿고 쓰면 죽은 토큰으로 401 이 난다. record 를 먼저 읽어 토큰
    # 버전이 바뀌었는지 본다 (멀티 인스턴스 포함 — 다른 인스턴스가 재동의를 처리해도
    # Firestore 가 단일 진실원이라 다음 호출에서 자동 무효화됨).
    record = get_token(user_email)
    if not record:
        raise AuthRequiredError(user_email, reason="no_token")
    if record.get("status") != "linked":
        raise AuthRequiredError(user_email, reason=f"status_{record.get('status')}")

    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise AuthRequiredError(user_email, reason="no_refresh_token")

    key = _cache_key(user_email, scopes)
    with _lock:
        cached = _creds_cache.get(key)
    if cached is not None and cached.valid and cached.refresh_token == refresh_token:
        return cached

    if not (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET):
        raise AuthRequiredError(user_email, reason="oauth_client_not_configured")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=OAUTH_CLIENT_ID,
        client_secret=OAUTH_CLIENT_SECRET,
        scopes=scopes,
    )
    try:
        creds.refresh(Request())
    except Exception as e:
        logger.warning("refresh_token 재발급 실패 (%s): %s", user_email, e)
        update_status(user_email, "revoked")
        with _lock:
            for k in list(_creds_cache.keys()):
                if k[0] == user_email:
                    _creds_cache.pop(k, None)
        raise AuthRequiredError(user_email, reason="refresh_failed") from e

    touch_refreshed(user_email)
    with _lock:
        _creds_cache[key] = creds
    return creds


# 함수 — bearer token 직접 호출용.
def get_user_access_token(user_email: str, scopes: list[str]) -> str:
    creds = get_user_credentials(user_email, scopes)
    token = getattr(creds, "token", None)
    if not token:
        raise AuthRequiredError(user_email, reason="empty_token")
    return token
