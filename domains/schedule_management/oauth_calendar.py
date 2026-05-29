"""사용자 OAuth 토큰으로 Calendar API 호출."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from config.settings import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
from firestore.oauth_tokens import get_token

logger = logging.getLogger(__name__)

USER_CAL_PREFIX = "user:"


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
        return str(token) if token else None
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
