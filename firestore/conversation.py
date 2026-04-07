"""
Firestore `conversations` — 방(space)+사람마다 문서 하나.

공개 함수 (나머지 _doc_id 는 내부용):
  ensure_conversation — 대화 루트 문서 없으면 만들고, 있으면 updated_at 갱신
  list_recent_messages — 서브컬렉션 messages 최근 N건(프롬프트용, 시간순)
  record_messages   — 이번 턴 user/assistant 를 messages 에 쌓고, 최근 목록 반환

구글 챗 JSON에서 space/user 뽑는 건 Firestore랑 무관해서 domains/daily_chat/chat.py 에 둠.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from google.cloud import firestore

from .writes import get_client

COLLECTION_ID = "conversations"
_MESSAGES = "messages"
_RECENT_LIMIT = 20


def _messages_recent_chronological(conv_ref: Any, limit: int) -> list[dict[str, str]]:
    """messages 서브컬렉션에서 최근 limit건을 시간순으로 반환."""
    sub = conv_ref.collection(_MESSAGES)
    q = sub.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
    docs = list(q.stream())
    out: list[dict[str, str]] = []
    for d in reversed(docs):
        data = d.to_dict() or {}
        out.append({"role": data.get("role", "user"), "content": data.get("content", "")})
    return out


# 1. conversations/{문서id} 에 쓸 id 문자열만 계산 (문서 생성 아님).
def _doc_id(space_id: str, user_name: Optional[str]) -> str:
    """conversations 컬렉션 문서 ID 문자열만 계산(문서 생성 아님)."""
    if not user_name or not isinstance(user_name, str):
        return space_id
    s = user_name.strip()
    s = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")[:80]
    return f"{space_id}_{s}" if s else space_id


# 2. 루트 문서 없으면 만들고, 있으면 updated_at 만 갱신한다.
def ensure_conversation(
    space_id: str,
    user_name: Optional[str],
    user_context: Optional[dict[str, str]],
) -> dict[str, Any]:
    """대화 문서 없으면 생성·있으면 갱신. 맥락은 서브컬렉션 messages 로만 유지한다."""
    db = get_client()
    doc_id = _doc_id(space_id, user_name)
    ref = db.collection(COLLECTION_ID).document(doc_id)
    now = datetime.utcnow()

    snap = ref.get()
    if snap.exists:
        ref.update({"updated_at": now})
    else:
        data: dict[str, Any] = {
            "space_id": space_id,
            "created_at": now,
            "updated_at": now,
        }
        if user_context:
            data["user_context"] = user_context
        ref.set(data)

    return {}


# 3. 서브컬렉션 messages 에서 최근 N건을 시간순으로 반환(답 생성 직전에 프롬프트에 넣기 위함).
def list_recent_messages(
    space_id: str,
    user_name: Optional[str],
    limit: int = _RECENT_LIMIT,
) -> list[dict[str, str]]:
    """messages 서브컬렉션 최근 N건을 시간순으로 반환. 문서 없으면 빈 리스트."""
    db = get_client()
    doc_id = _doc_id(space_id, user_name)
    conv_ref = db.collection(COLLECTION_ID).document(doc_id)
    if not conv_ref.get().exists:
        return []
    return _messages_recent_chronological(conv_ref, limit)


# 4. 이번 턴 user/assistant 를 messages 에 쌓고, 요약 LLM 에 넘길 최근 대화 목록을 반환한다.
def record_messages(
    space_id: str,
    user_name: Optional[str],
    user_message: str,
    assistant_message: str,
) -> list[dict[str, str]]:
    """이번 턴 메시지 2건 기록 후 요약용 최근 대화 목록(role/content) 반환."""
    db = get_client()
    doc_id = _doc_id(space_id, user_name)
    conv_ref = db.collection(COLLECTION_ID).document(doc_id)
    now = datetime.utcnow()

    if not conv_ref.get().exists:
        ensure_conversation(space_id, user_name, None)

    sub = conv_ref.collection(_MESSAGES)
    for role, content in (("user", user_message), ("assistant", assistant_message)):
        sub.add({"role": role, "content": content, "created_at": now})
    conv_ref.update({"updated_at": now})
    return _messages_recent_chronological(conv_ref, _RECENT_LIMIT)
