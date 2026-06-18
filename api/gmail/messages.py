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
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code in (401, 403):
            logger.warning("gmail list HTTP %s body=%s", e.code, body)
            return ListMessagesResult(ok=False, error_kind="auth_required")
        logger.warning("gmail list HTTP %s body=%s", e.code, body)
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


def list_messages_by_query(
    *,
    user_email: str,
    query: str,
    time_min: str | None = None,
    time_max: str | None = None,
    max_results: int = 30,
) -> ListMessagesResult:
    """키워드 검색 — Gmail ``q`` 본문/제목 검색. 응답은 메타만 (``format=metadata``).

    Args:
        user_email: OAuth 동의자 이메일.
        query: 검색 키워드 (Gmail q 에 그대로 들어감 — 본문까지 검색됨).
        time_min, time_max: ISO8601. None 이면 시간 제약 없음.
        max_results: 응답 메시지 최대 수.

    Returns:
        ``ListMessagesResult`` — 응답 dict 항목 (``list_messages`` 와 동일):
        ``id, subject, from, to, date, internal_date``. 본문은 fetch 안 함.
    """
    if not (query or "").strip():
        return ListMessagesResult(ok=False, error_kind="empty_query")

    from api._auth.user_oauth import AuthRequiredError, get_user_credentials

    try:
        creds = get_user_credentials(user_email, [_SCOPE])
    except AuthRequiredError:
        return ListMessagesResult(ok=False, error_kind="auth_required")

    q_parts = [query.strip()]
    if time_min:
        q_parts.append(f"after:{_to_gmail_date(time_min)}")
    if time_max:
        q_parts.append(f"before:{_to_gmail_date(time_max)}")
    q = " ".join(q_parts)

    list_params = {"q": q, "maxResults": str(max_results)}
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
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code in (401, 403):
            logger.warning("gmail list_messages_by_query HTTP %s body=%s", e.code, body)
            return ListMessagesResult(ok=False, error_kind="auth_required")
        logger.warning("gmail list_messages_by_query HTTP %s body=%s", e.code, body)
        return ListMessagesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("gmail list_messages_by_query 실패: %s", e)
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
    logger.info(
        "gmail list_messages_by_query user=%s q=%s count=%d",
        user_email,
        q,
        len(messages),
    )
    return ListMessagesResult(ok=True, messages=messages)


def get_message_body(*, message_id: str, user_email: str, max_chars: int = 1000) -> str:
    """Gmail 메시지 본문 (text/plain) 앞 max_chars 자 반환. 실패 시 빈 문자열."""
    from api._auth.user_oauth import AuthRequiredError, get_user_credentials

    try:
        creds = get_user_credentials(user_email, [_SCOPE])
    except AuthRequiredError:
        return ""

    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=full"
    headers = {"Authorization": f"Bearer {creds.token}", "Accept": "application/json"}
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("gmail get_message_body %s 실패: %s", message_id, e)
        return ""

    return _extract_text_body(data.get("payload") or {}, max_chars)


def _extract_text_body(payload: dict[str, Any], max_chars: int) -> str:
    """payload 에서 text/plain 파트를 재귀 탐색해 base64url 디코드 후 반환."""
    import base64

    mime = payload.get("mimeType", "")
    body_data = (payload.get("body") or {}).get("data", "")
    if mime == "text/plain" and body_data:
        try:
            text = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
            return text[:max_chars]
        except Exception:
            return ""

    for part in payload.get("parts") or []:
        result = _extract_text_body(part, max_chars)
        if result:
            return result
    return ""


def _to_gmail_date(iso: str) -> str:
    """ISO8601 → ``YYYY/MM/DD`` (Gmail q 검색 포맷)."""
    return (iso or "")[:10].replace("-", "/")
