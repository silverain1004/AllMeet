"""Google Chat 이벤트 → Firestore 대화 맥락 로더."""

from __future__ import annotations

import logging
from typing import Any

from domains.daily_chat import conversation_store as conv
from domains.daily_chat.chat import _parse_google_chat_payload

logger = logging.getLogger(__name__)


def parse_chat_identity(
    chat_event: dict[str, Any] | None,
) -> tuple[str, str | None, str, str]:
    """(space_id, user_display_name, user_email, space_name) 반환."""
    payload = chat_event or {}
    space_id, user_display_name, user_ctx = _parse_google_chat_payload(payload)
    user = payload.get("user") or {}
    user_email = str(user.get("email") or user_ctx.get("email") or "").strip()
    space_name = str((payload.get("space") or {}).get("name") or "").strip()
    return space_id, user_display_name, user_email, space_name


def load_ctx_block(chat_event: dict[str, Any] | None) -> str:
    """MESSAGE 이벤트에서 최근 20턴 대화를 프롬프트용 문자열로 반환. 실패 시 빈 문자열."""
    if not chat_event or chat_event.get("type") != "MESSAGE":
        return ""
    space_id, user_display_name, _, _ = parse_chat_identity(chat_event)
    if not space_id or space_id == "unknown":
        return ""
    try:
        recent_msgs = conv.list_recent_messages(space_id, user_display_name)
        block = conv.format_recent_dialogue(recent_msgs)
        if block.strip():
            return f"\n[최근 대화]\n{block}\n"
    except Exception:
        logger.warning("load_ctx_block 실패", exc_info=True)
    return ""
