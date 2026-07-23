"""사용자 OAuth 토큰으로 Calendar API 호출."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from config.settings import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
from firestore.oauth_tokens import get_token

logger = logging.getLogger(__name__)

USER_CAL_PREFIX = "user:"

# Google access_token 은 1시간 유효한데 예전에는 카드 렌더마다 refresh POST 를 새로 쳤다.
# 만료 5분 전까지 재사용해 클릭당 왕복 2회를 없앤다.
_ACCESS_TOKEN_TTL_SEC = 3300
_access_token_cache: dict[str, tuple[float, str]] = {}
_access_token_lock = threading.Lock()


def clear_access_token_cache(user_email: str | None = None) -> None:
    """access_token 캐시 무효화. user_email 이 없으면 전체."""
    with _access_token_lock:
        if user_email:
            prefix = f"{user_email}\x00"
            for key in [k for k in _access_token_cache if k.startswith(prefix)]:
                _access_token_cache.pop(key, None)
        else:
            _access_token_cache.clear()


def encode_user_calendar_id(user_email: str, calendar_id: str) -> str:
    return f"{USER_CAL_PREFIX}{user_email}:{calendar_id}"


def decode_calendar_selection(selected: str) -> tuple[str, str | None]:
    """(api_calendar_id, user_email_for_token) — team/SA 캘린더는 user_email None."""
    raw = (selected or "").strip()
    if raw.startswith(USER_CAL_PREFIX):
        rest = raw[len(USER_CAL_PREFIX) :]
        if ":" in rest:
            email, cal_id = rest.split(":", 1)
            return cal_id.strip(), email.strip()
    return raw, None


def is_oauth_linked(user_email: str) -> bool:
    if not user_email:
        return False
    doc = get_token(user_email)
    if not doc:
        return False
    return str(doc.get("status") or "") == "linked" and bool(doc.get("refresh_token"))


def get_user_access_token(user_email: str) -> str | None:
    doc = get_token(user_email)
    if not doc:
        return None
    refresh = str(doc.get("refresh_token") or "").strip()
    if not refresh or str(doc.get("status") or "") != "linked":
        return None
    # 캐시 키에 refresh_token 을 포함 — 재동의로 새 refresh_token 이 발급되면 옛 access_token
    # 은 서버에서 폐기되므로, email 만으로 캐싱하면 죽은 토큰으로 401 이 난다.
    cache_key = f"{user_email}\x00{refresh}"
    now = time.monotonic()
    with _access_token_lock:
        cached = _access_token_cache.get(cache_key)
        if cached and (now - cached[0]) < _ACCESS_TOKEN_TTL_SEC:
            return cached[1]
    if not (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET):
        logger.warning("OAuth client not configured")
        return None
    body = urllib.parse.urlencode(
        {
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token")
        if not token:
            return None
        expires_in = int(data.get("expires_in") or 3600)
        ttl = max(60, min(_ACCESS_TOKEN_TTL_SEC, expires_in - 300))
        with _access_token_lock:
            # 캐시 만료 시각을 TTL 기준으로 맞추려 저장 시각을 뒤로 당긴다.
            _access_token_cache[cache_key] = (
                time.monotonic() - (_ACCESS_TOKEN_TTL_SEC - ttl),
                str(token),
            )
        return str(token)
    except Exception as e:
        logger.warning("refresh token failed for %s: %s", user_email, e)
        return None


def list_user_calendars(user_email: str) -> list[dict[str, str]]:
    """연결된 사용자의 캘린더 목록 — compose 드롭다운용."""
    token = get_user_access_token(user_email)
    if not token:
        return []
    url = "https://www.googleapis.com/calendar/v3/users/me/calendarList?minAccessRole=writer"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("calendarList failed: %s", e)
        return []
    out: list[dict[str, str]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        cal_id = str(item.get("id") or "").strip()
        if not cal_id:
            continue
        summary = str(item.get("summary") or cal_id).strip()
        primary = bool(item.get("primary"))
        label = f"{summary} (기본)" if primary else summary
        out.append(
            {
                "id": encode_user_calendar_id(user_email, cal_id),
                "label": label,
                "api_id": cal_id,
            }
        )
    return out
