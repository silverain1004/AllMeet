"""에이전트 사용자 메모리 — Firestore ``agent_memory`` 컬렉션.

연속성("지난번에 만든 거 이어서")을 위해 사용자별로 최근 산출물(생성한 페이지/회의)을
기록하고, 다음 계획 수립 시 맥락으로 주입한다.

``domains/daily_chat/conversation_store.py`` 패턴(루트 문서 + 서브컬렉션 + 최근 N건)을
미러링하며, ``firestore/documents.py`` 공통 헬퍼를 재사용한다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from firestore.documents import (
    append_subcollection_docs,
    document_ref,
    ensure_document,
    list_subcollection_recent_chronological,
)

logger = logging.getLogger(__name__)

COLLECTION = "agent_memory"
ARTIFACTS = "artifacts"
ORDER_FIELD = "created_at"
ARTIFACT_FIELDS = ("kind", "ref_id", "title", "goal")
RECENT_LIMIT = 5


def memory_doc_id(user_email: str) -> str:
    """user_email 을 Firestore 문서 ID 로 정규화(비문자 → 밑줄, 최대 80자)."""
    s = (user_email or "").strip().lower()
    s = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")[:80]
    return s or "unknown"


def _extract_artifacts(goal: str, results: dict[Any, Any]) -> list[dict[str, Any]]:
    """실행 결과 dict 에서 생성 산출물(page/event)만 뽑아 메모리 문서로 변환."""
    now = datetime.utcnow()
    out: list[dict[str, Any]] = []
    for value in (results or {}).values():
        if not isinstance(value, dict) or not value.get("ok"):
            continue
        if value.get("page_id"):
            out.append({
                "kind": "page",
                "ref_id": str(value.get("page_id")),
                "title": str(value.get("title") or (value.get("preview") or {}).get("title") or ""),
                "goal": goal,
                "created_at": now,
            })
        if value.get("event_id"):
            out.append({
                "kind": "event",
                "ref_id": str(value.get("event_id")),
                "title": str(value.get("summary") or ""),
                "goal": goal,
                "created_at": now,
            })
    return out


def record_artifacts(user_email: str, goal: str, results: dict[Any, Any]) -> None:
    """완료된 계획의 산출물을 사용자 메모리에 적재. 실패해도 본 흐름을 막지 않는다."""
    if not (user_email or "").strip():
        return
    artifacts = _extract_artifacts(goal, results)
    if not artifacts:
        return
    try:
        ref = document_ref(COLLECTION, memory_doc_id(user_email))
        now = datetime.utcnow()
        ensure_document(
            ref,
            create_data={"user_email": user_email, "created_at": now, "updated_at": now},
            update_on_exists={"updated_at": now},
        )
        append_subcollection_docs(ref, ARTIFACTS, artifacts, parent_update={"updated_at": now})
    except Exception:
        logger.warning("agent memory 적재 실패 user=%s", user_email, exc_info=True)


def load_user_memory(user_email: str, limit: int = RECENT_LIMIT) -> list[dict[str, str]]:
    """사용자의 최근 산출물 N건을 시간순(오래된 것 → 최신)으로 반환. 실패 시 빈 리스트."""
    if not (user_email or "").strip():
        return []
    try:
        ref = document_ref(COLLECTION, memory_doc_id(user_email))
        return list_subcollection_recent_chronological(
            ref,
            ARTIFACTS,
            order_by_field=ORDER_FIELD,
            limit=limit,
            fields=ARTIFACT_FIELDS,
        )
    except Exception:
        logger.warning("agent memory 로드 실패 user=%s", user_email, exc_info=True)
        return []


def format_memory_block(items: list[dict[str, str]]) -> str:
    """load_user_memory 결과를 플래너 프롬프트용 텍스트로 변환. 비어 있으면 빈 문자열."""
    if not items:
        return ""
    lines: list[str] = []
    for it in items:
        kind = "페이지" if it.get("kind") == "page" else "회의"
        title = (it.get("title") or "").strip()
        goal = (it.get("goal") or "").strip()
        ref_id = (it.get("ref_id") or "").strip()
        label = title or goal or ref_id
        if label:
            lines.append(f"- [{kind}] {label} (id={ref_id})")
    return "\n".join(lines)
