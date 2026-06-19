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
    return create_message_in_space(space_name=space_name, payload=payload) is not None


def create_message_in_space(*, space_name: str, payload: dict[str, Any]) -> str | None:
    """Chat REST 메시지 신규 push 후 생성된 메시지 리소스 이름을 반환.

    ``post_message_to_space`` 와 달리 진행상황 라이브 갱신처럼 이후 ``patch`` 가 필요한 경우 사용.

    Returns:
        ``"spaces/XXX/messages/YYY"`` (성공) 또는 None (실패).
    """
    name = (space_name or "").strip()
    if not name:
        logger.warning("create_message_in_space: empty space_name")
        return None
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
            if not 200 <= resp.status < 300:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            return str(data.get("name") or "") or None
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.warning("Chat REST push HTTP %s: %s", e.code, snippet)
        return None
    except Exception as e:
        logger.warning("Chat REST push 실패: %s", e)
        return None


def update_message_in_space(
    *, message_name: str, payload: dict[str, Any], update_mask: str = "text,cardsV2"
) -> bool:
    """기존 Chat 메시지를 patch — 진행상황 라이브 갱신용.

    Args:
        message_name: ``create_message_in_space`` 가 돌려준 ``"spaces/XXX/messages/YYY"``.
        payload: 갱신할 필드 (``text`` 또는 ``cardsV2``).
        update_mask: 갱신 대상 필드 마스크. 기본 ``"text,cardsV2"`` — 진행 텍스트↔결과 카드 전환 모두 커버.

    Returns:
        성공 여부. 실패해도 호출 측 흐름은 계속(베스트-에포트).
    """
    name = (message_name or "").strip()
    if not name:
        return False
    query = urllib.parse.urlencode({"updateMask": update_mask})
    url = f"https://chat.googleapis.com/v1/{name}?{query}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        token = get_access_token([_SCOPE])
        req = urllib.request.Request(url, data=body, method="PATCH")
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
        logger.warning("Chat REST patch HTTP %s: %s", e.code, snippet)
        return False
    except Exception as e:
        logger.warning("Chat REST patch 실패: %s", e)
        return False
