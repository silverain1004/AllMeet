"""Phase B 영속화 — 계획을 Firestore ``agent_plans`` 컬렉션에 저장.

승인 클릭(CARD_CLICKED)은 계획 생성(MESSAGE)과 별개의 HTTP 요청이므로, 계획을
영속화하고 카드 버튼에는 plan_id 만 실어야 한다. firestore/documents.py 공통 CRUD 재사용.

상태(status) 전이:
  pending_approval → approved → running → done | failed
  pending_approval → rejected
  (자가복구 중 새 side_effect 발견 시) running → pending_reapproval → approved
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_COLLECTION = "agent_plans"

STATUS_PENDING = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
STATUS_PENDING_REAPPROVAL = "pending_reapproval"
# 승인 카드를 방치한 채 TTL 을 넘긴 계획. 사용자가 카드에 응답하지 않는 경우에 대한
# 생명주기 전이가 없어서 pending 계획이 무기한 살아남았고, 한 달 뒤 새 요청을
# 하이재킹하는 사고가 났다 — find_active_plan 이 만료 발견 시 이 상태로 정리한다.
STATUS_EXPIRED = "expired"

# 활성 계획 유효기간 — 승인 대기 계획이 새 발화의 수정(revision) 대상이 될 수 있는 시한.
# 명료화 15분 만료와 같은 취지: 하루가 지나도록 승인하지 않은 계획은 버려진 것으로 본다.
ACTIVE_PLAN_TTL_HOURS = 24


def plan_expired(created_at_iso: str, *, hours: int = ACTIVE_PLAN_TTL_HOURS) -> bool:
    """계획이 TTL 을 넘겼는지. 파싱 불가 시 만료로 단정하지 않음(보수적)."""
    from datetime import datetime, timezone

    if not (created_at_iso or "").strip():
        return False
    try:
        created = datetime.fromisoformat(created_at_iso)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() > hours * 3600
    except (ValueError, TypeError):
        return False
# 플래너가 되물어 답변을 기다리는 상태 — 후속 답변을 원 요청과 병합·재계획하기 위한 최소 상태.
STATUS_CLARIFYING = "clarifying"
STATUS_CLARIFY_CLOSED = "clarify_closed"


def _ref(plan_id: str):
    from firestore.documents import document_ref

    return document_ref(_COLLECTION, plan_id)


def save_plan(
    plan: dict[str, Any],
    *,
    user_email: str,
    space_name: str,
) -> str:
    """새 계획을 pending_approval 로 저장하고 plan_id 반환."""
    import json
    from datetime import datetime, timezone

    from firestore.documents import ensure_document

    plan_id = uuid.uuid4().hex
    create_data = {
        "plan_id": plan_id,
        "goal": str(plan.get("goal") or ""),
        "plan_rationale": str(plan.get("plan_rationale") or ""),
        "critique_note": str(plan.get("critique_note") or ""),
        "steps_json": json.dumps(plan.get("steps") or [], ensure_ascii=False),
        "status": STATUS_PENDING,
        "user_email": user_email,
        "space_name": space_name,
        "results_json": "{}",
        "trace_json": "[]",
        "planner_model": str(plan.get("planner_model") or ""),
        "error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        ensure_document(_ref(plan_id), create_data=create_data, update_on_exists=create_data)
    except Exception:
        logger.exception("save_plan 실패")
        raise
    return plan_id


def load_plan(plan_id: str) -> dict[str, Any] | None:
    """저장된 계획을 dict 로 로드. steps/results 는 파싱해서 돌려준다. 없으면 None."""
    import json

    if not (plan_id or "").strip():
        return None
    try:
        snap = _ref(plan_id).get()
    except Exception:
        logger.exception("load_plan 실패 plan_id=%s", plan_id)
        return None
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    try:
        data["steps"] = json.loads(data.get("steps_json") or "[]")
    except (ValueError, TypeError):
        data["steps"] = []
    try:
        data["results"] = json.loads(data.get("results_json") or "{}")
    except (ValueError, TypeError):
        data["results"] = {}
    try:
        data["trace"] = json.loads(data.get("trace_json") or "[]")
    except (ValueError, TypeError):
        data["trace"] = []
    return data


def set_status(plan_id: str, status: str, *, error: str = "") -> None:
    from firestore.documents import patch_document

    patch: dict[str, Any] = {"status": status}
    if error:
        patch["error"] = error
    try:
        patch_document(_ref(plan_id), patch)
    except Exception:
        logger.exception("set_status 실패 plan_id=%s status=%s", plan_id, status)


def save_results(plan_id: str, results: dict[str, Any]) -> None:
    """단계별 실행 결과(JSON)를 갱신 저장 — 재개·디버깅용."""
    import json

    from firestore.documents import patch_document

    try:
        patch_document(_ref(plan_id), {"results_json": json.dumps(results, ensure_ascii=False)})
    except Exception:
        logger.exception("save_results 실패 plan_id=%s", plan_id)


def save_trace(plan_id: str, trace: list[dict[str, Any]]) -> None:
    """step별 실행 trace(JSON)를 갱신 저장 — 관측/실패지점 분석용(Phase 0).

    어디서 깨지는지(라우팅·계획·실행) 데이터로 확정하기 위한 핵심 기록.
    """
    import json

    from firestore.documents import patch_document

    try:
        patch_document(
            _ref(plan_id), {"trace_json": json.dumps(trace or [], ensure_ascii=False, default=str)}
        )
    except Exception:
        logger.exception("save_trace 실패 plan_id=%s", plan_id)


def update_steps(plan_id: str, steps: list[dict[str, Any]]) -> None:
    """자가복구로 계획이 바뀐 경우 steps 갱신."""
    import json

    from firestore.documents import patch_document

    try:
        patch_document(_ref(plan_id), {"steps_json": json.dumps(steps, ensure_ascii=False)})
    except Exception:
        logger.exception("update_steps 실패 plan_id=%s", plan_id)


def update_plan(plan_id: str, plan: dict[str, Any]) -> None:
    """승인 대기 plan 의 goal/steps/메타 갱신. status 는 pending_approval 유지."""
    import json

    from firestore.documents import patch_document

    patch: dict[str, Any] = {
        "goal": str(plan.get("goal") or ""),
        "plan_rationale": str(plan.get("plan_rationale") or ""),
        "critique_note": str(plan.get("critique_note") or ""),
        "steps_json": json.dumps(plan.get("steps") or [], ensure_ascii=False),
        "status": STATUS_PENDING,
    }
    try:
        patch_document(_ref(plan_id), patch)
    except Exception:
        logger.exception("update_plan 실패 plan_id=%s", plan_id)
        raise


def find_active_plan(
    user_email: str,
    space_name: str,
    *,
    statuses: tuple[str, ...] = (STATUS_PENDING, STATUS_PENDING_REAPPROVAL),
) -> dict[str, Any] | None:
    """user+space 기준 가장 최근 활성(pending) plan. 없으면 None.

    TTL(24h)을 넘긴 pending 계획은 활성으로 치지 않고 expired 로 정리한다(lazy cleanup) —
    방치된 계획이 좀비로 축적되어 이후 발화를 하이재킹하는 것을 원천 차단.
    """
    if not (user_email or "").strip() or not (space_name or "").strip():
        return None
    allowed = set(statuses)
    try:
        from firestore.documents import get_client

        col = get_client().collection(_COLLECTION)
        docs = col.where("user_email", "==", user_email).where("space_name", "==", space_name).stream()
        candidates: list[dict[str, Any]] = []
        for doc in docs:
            data = doc.to_dict() or {}
            if data.get("status") not in allowed:
                continue
            if plan_expired(str(data.get("created_at") or "")):
                set_status(doc.id, STATUS_EXPIRED)  # 실패해도 내부 로깅만 — 조회는 계속
                logger.info("find_active_plan: 만료 계획 정리 plan_id=%s created_at=%s", doc.id, data.get("created_at"))
                continue
            loaded = load_plan(doc.id)
            if loaded:
                loaded["plan_id"] = doc.id
                candidates.append(loaded)
        if not candidates:
            return None
        candidates.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
        return candidates[0]
    except Exception:
        logger.exception("find_active_plan 실패 user=%s space=%s", user_email, space_name)
        return None


def save_clarification(
    user_message: str,
    ask: str,
    *,
    user_email: str,
    space_name: str,
    attempts: int = 1,
) -> str:
    """플래너가 되물을 때 '명료화 대기' 레코드를 저장하고 plan_id 반환.

    후속 답변을 원 요청(goal)과 병합·재계획하기 위한 최소 상태. agent_plans 컬렉션을 공유하되
    status=clarifying 으로 승인 대기 plan 과 분리된다.
    """
    from datetime import datetime, timezone

    from firestore.documents import ensure_document

    plan_id = uuid.uuid4().hex
    create_data = {
        "plan_id": plan_id,
        "goal": str(user_message or ""),
        "ask": str(ask or ""),
        "clarify_attempts": int(attempts),
        "steps_json": "[]",
        "results_json": "{}",
        "trace_json": "[]",
        "status": STATUS_CLARIFYING,
        "user_email": user_email,
        "space_name": space_name,
        "error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        ensure_document(_ref(plan_id), create_data=create_data, update_on_exists=create_data)
    except Exception:
        logger.exception("save_clarification 실패")
        raise
    return plan_id


def find_pending_clarification(user_email: str, space_name: str) -> dict[str, Any] | None:
    """user+space 의 가장 최근 '명료화 대기' 레코드. 없으면 None."""
    return find_active_plan(user_email, space_name, statuses=(STATUS_CLARIFYING,))


def clear_clarification(plan_id: str) -> None:
    """명료화 레코드를 닫는다(소비·만료·하이재킹 시) — 이후 find 에서 제외."""
    if not (plan_id or "").strip():
        return
    set_status(plan_id, STATUS_CLARIFY_CLOSED)
