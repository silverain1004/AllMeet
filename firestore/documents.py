"""
Firestore 공통: 문서 보장·갱신·삭제, 서브컬렉션 추가·최근 N건 조회.

도메인(컬렉션명·문서 ID 규칙·필드 스키마)은 호출 쪽에서 정한다.
"""

from __future__ import annotations

from typing import Any, Sequence

from google.cloud import firestore

from .writes import get_client


def document_ref(collection_id: str, document_id: str) -> firestore.DocumentReference:
    """루트 컬렉션 문서 참조."""
    return get_client().collection(collection_id).document(document_id)


def ensure_document(
    ref: firestore.DocumentReference,
    *,
    create_data: dict[str, Any],
    update_on_exists: dict[str, Any],
) -> None:
    """문서 없으면 create_data 로 생성, 있으면 update_on_exists 만 병합 갱신."""
    snap = ref.get()
    if snap.exists:
        ref.update(update_on_exists)
    else:
        ref.set(create_data)


def patch_document(ref: firestore.DocumentReference, data: dict[str, Any]) -> None:
    """문서 필드 부분 갱신. 문서가 없으면 Firestore가 오류를 낸다."""
    ref.update(data)


def delete_document(ref: firestore.DocumentReference) -> None:
    """문서 삭제. 하위 서브컬렉션은 자동 삭제되지 않는다."""
    ref.delete()


def list_subcollection_recent_chronological(
    parent: firestore.DocumentReference,
    subcollection_id: str,
    *,
    order_by_field: str,
    limit: int,
    fields: Sequence[str],
) -> list[dict[str, str]]:
    """
    서브컬렉션에서 최근 limit건을 시간순(오래된 것 → 최신)으로 반환.
    부모 문서가 없으면 빈 리스트.
    각 항목은 fields 에 나열한 키만 담으며, 없으면 빈 문자열.
    """
    if not parent.get().exists:
        return []
    sub = parent.collection(subcollection_id)
    q = sub.order_by(order_by_field, direction=firestore.Query.DESCENDING).limit(limit)
    docs = list(q.stream())
    out: list[dict[str, str]] = []
    for d in reversed(docs):
        data = d.to_dict() or {}
        row: dict[str, str] = {}
        for k in fields:
            v = data.get(k)
            row[k] = "" if v is None else str(v)
        out.append(row)
    return out


def append_subcollection_docs(
    parent: firestore.DocumentReference,
    subcollection_id: str,
    docs: list[dict[str, Any]],
    *,
    parent_update: dict[str, Any] | None = None,
) -> None:
    """서브컬렉션에 문서를 순서대로 추가하고, 필요 시 부모 문서를 갱신한다."""
    sub = parent.collection(subcollection_id)
    for item in docs:
        sub.add(item)
    if parent_update:
        parent.update(parent_update)
