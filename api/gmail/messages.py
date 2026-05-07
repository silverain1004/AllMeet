"""Gmail v1 — `users.messages.list` + `messages.get(format=metadata)`.

OAuth 미동의 사용자에 대해 ``"auth_required"`` 로 빠르게 빠짐.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass
class ListMessagesResult:
    ok: bool
    messages: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str = ""


def list_messages(
    *,
    user_email: str,
    time_min: str,
    time_max: str,
    max_results: int = 50,
) -> ListMessagesResult:
    """``user_email`` 의 메일 목록 (메타만).

    Args:
        time_min, time_max: ISO8601. Gmail q 는 ``after:YYYY/MM/DD before:...`` 형식으로 변환.
    """
    from api._auth.user_oauth import AuthRequiredError, get_user_credentials

    try:
        creds = get_user_credentials(user_email, [_SCOPE])
    except AuthRequiredError:
        return ListMessagesResult(ok=False, error_kind="auth_required")

    after = _to_gmail_date(time_min)
    before = _to_gmail_date(time_max)
    list_params = {
        "q": f"after:{after} before:{before}",
        "maxResults": str(max_results),
    }
    list_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{urllib.parse.urlencode(list_params)}"

    token = creds.token
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        req = urllib.request.Request(list_url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            list_payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ListMessagesResult(ok=False, error_kind="auth_required")
        logger.warning("gmail list HTTP %s", e.code)
        return ListMessagesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("gmail list 실패: %s", e)
        return ListMessagesResult(ok=False, error_kind="http_error")

    list_data = json.loads(list_payload)
    msg_ids = [m.get("id") for m in (list_data.get("messages") or []) if isinstance(m, dict) and m.get("id")]

    messages: list[dict[str, Any]] = []
    for mid in msg_ids[:max_results]:
        meta_url = (
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}"
            f"?format=metadata&metadataHeaders=Subject&metadataHeaders=From"
            f"&metadataHeaders=To&metadataHeaders=Date"
        )
        try:
            req2 = urllib.request.Request(meta_url, method="GET", headers=headers)
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                meta = json.loads(resp2.read().decode("utf-8"))
        except Exception as e:
            logger.warning("gmail get %s 실패: %s", mid, e)
            continue
        hdrs = {
            h.get("name", ""): h.get("value", "")
            for h in (meta.get("payload", {}).get("headers") or [])
            if isinstance(h, dict)
        }
        messages.append(
            {
                "id": mid,
                "subject": hdrs.get("Subject", ""),
                "from": hdrs.get("From", ""),
                "to": hdrs.get("To", ""),
                "date": hdrs.get("Date", ""),
                "internal_date": meta.get("internalDate", ""),
            }
        )
    return ListMessagesResult(ok=True, messages=messages)


def _to_gmail_date(iso: str) -> str:
    """ISO8601 → ``YYYY/MM/DD`` (Gmail q 검색 포맷)."""
    return (iso or "")[:10].replace("-", "/")
