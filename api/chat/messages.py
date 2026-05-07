"""Google Chat REST — `spaces.messages.create` 푸시.

`hello_http` 가 즉시 "분석 중" 응답 후 백그라운드에서 결과를 만들어 이 함수로 push.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from api._auth.service_account import get_access_token

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/chat.bot"


def post_message_to_space(*, space_name: str, payload: dict[str, Any]) -> bool:
    """Chat REST 메시지 신규 push.

    Args:
        space_name: ``"spaces/XXX"`` 또는 ``"XXX"`` (자동 prefix).
        payload: Google Chat ``Message`` 스키마 (``text``, ``cardsV2``, ``accessoryWidgets``, ``actionResponse``).

    Returns:
        성공 여부. 실패 시 False (호출 측에서 fallback).
    """
    name = (space_name or "").strip()
    if not name:
        logger.warning("post_message_to_space: empty space_name")
        return False
    if not name.startswith("spaces/"):
        name = f"spaces/{name}"
    url = f"https://chat.googleapis.com/v1/{name}/messages"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        token = get_access_token([_SCOPE])
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.warning("Chat REST push HTTP %s: %s", e.code, snippet)
        return False
    except Exception as e:
        logger.warning("Chat REST push 실패: %s", e)
        return False
