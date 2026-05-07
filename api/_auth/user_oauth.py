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

_lock = threading.Lock()
_creds_cache: dict[str, Credentials] = {}


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

    with _lock:
        cached = _creds_cache.get(user_email)
    if cached is not None and cached.valid:
        return cached

    record = get_token(user_email)
    if not record:
        raise AuthRequiredError(user_email, reason="no_token")
    if record.get("status") != "linked":
        raise AuthRequiredError(user_email, reason=f"status_{record.get('status')}")

    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise AuthRequiredError(user_email, reason="no_refresh_token")

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
            _creds_cache.pop(user_email, None)
        raise AuthRequiredError(user_email, reason="refresh_failed") from e

    touch_refreshed(user_email)
    with _lock:
        _creds_cache[user_email] = creds
    return creds


# 함수 — bearer token 직접 호출용.
def get_user_access_token(user_email: str, scopes: list[str]) -> str:
    creds = get_user_credentials(user_email, scopes)
    token = getattr(creds, "token", None)
    if not token:
        raise AuthRequiredError(user_email, reason="empty_token")
    return token
