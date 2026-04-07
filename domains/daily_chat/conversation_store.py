"""
구글 챗 일상 대화용 Firestore `conversations` 컬렉션.

- 루트 문서: 방(space)+사용자별 1개 (`conversation_doc_id` 규칙).
- 서브컬렉션 `messages`: 턴별 role/content + created_at.

`chat.py` 등에서 import 해 맥락 로드·저장에 사용한다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from firestore.documents import (
    append_subcollection_docs,
    document_ref,
    ensure_document,
    list_subcollection_recent_chronological,
)

COLLECTION = "conversations"
MESSAGES = "messages"
RECENT_LIMIT = 20
ORDER_FIELD = "created_at"
MESSAGE_FIELDS = ("role", "content")


def conversation_doc_id(space_id: str, user_name: str | None) -> str:
    """Firestore 루트 문서 ID 문자열만 계산한다(문서 생성/조회 아님).

    * `user_name` 없음 → ``space_id`` 그대로.
    * 있으면 ``{space_id}_{표시명 슬러그}`` (비문자·연속 밑줄 정리, 최대 80자).
    """
    if not user_name or not isinstance(user_name, str):
        return space_id
    s = user_name.strip()
    s = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")[:80]
    return f"{space_id}_{s}" if s else space_id


def ensure_conversation(
    space_id: str,
    user_name: str | None,
    user_context: dict[str, str] | None,
) -> dict[str, Any]:
    """대화 루트 문서가 없으면 생성하고, 있으면 ``updated_at`` 만 갱신한다.

    최초 생성 시 ``user_context`` 가 있으면 루트 필드 ``user_context`` 에 저장한다.
    반환 ``{}`` 는 호출부에서 “Firestore 맥락 로드 경로로 진입했음” 표시용.
    """
    ref = document_ref(COLLECTION, conversation_doc_id(space_id, user_name))
    now = datetime.utcnow()
    create_data: dict[str, Any] = {
        "space_id": space_id,
        "created_at": now,
        "updated_at": now,
    }
    if user_context:
        create_data["user_context"] = user_context
    ensure_document(ref, create_data=create_data, update_on_exists={"updated_at": now})
    return {}


def list_recent_messages(
    space_id: str,
    user_name: str | None,
    limit: int = RECENT_LIMIT,
) -> list[dict[str, str]]:
    """``messages`` 서브컬렉션에서 최근 ``limit`` 건을 **시간순**(오래된 것 → 최신)으로 반환.

    루트 문서가 없으면 빈 리스트. 각 항목은 ``MESSAGE_FIELDS`` 키만 담는다.
    """
    ref = document_ref(COLLECTION, conversation_doc_id(space_id, user_name))
    return list_subcollection_recent_chronological(
        ref,
        MESSAGES,
        order_by_field=ORDER_FIELD,
        limit=limit,
        fields=MESSAGE_FIELDS,
    )


def record_messages(
    space_id: str,
    user_name: str | None,
    user_message: str,
    assistant_message: str,
) -> list[dict[str, str]]:
    """이번 턴 user/assistant 메시지 2건을 ``messages`` 에 추가하고, 루트 ``updated_at`` 을 갱신한다.

    루트가 없으면 ``ensure_conversation(..., None)`` 로 먼저 만든다.
    반환값은 ``list_recent_messages`` 와 동일 형식의 최근 ``RECENT_LIMIT`` 건(프롬프트 재사용).
    """
    ref = document_ref(COLLECTION, conversation_doc_id(space_id, user_name))
    now = datetime.utcnow()
    if not ref.get().exists:
        ensure_conversation(space_id, user_name, None)
    append_subcollection_docs(
        ref,
        MESSAGES,
        [
            {"role": "user", "content": user_message, "created_at": now},
            {"role": "assistant", "content": assistant_message, "created_at": now},
        ],
        parent_update={"updated_at": now},
    )
    return list_subcollection_recent_chronological(
        ref,
        MESSAGES,
        order_by_field=ORDER_FIELD,
        limit=RECENT_LIMIT,
        fields=MESSAGE_FIELDS,
    )


def format_recent_dialogue(messages: list[dict[str, str]]) -> str:
    """``list_recent_messages`` 결과를 프롬프트용 한 줄 한 턴 텍스트로 붙인다.

    ``role`` 이 ``user`` 이면 발화자 레이블을 “사용자”, 아니면 “All-Meet”.
    ``content`` 가 비어 있으면 해당 줄은 생략.
    """
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages:
        role = "사용자" if (m.get("role") == "user") else "All-Meet"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)
