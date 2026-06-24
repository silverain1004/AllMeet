"""에이전트 진입점(Phase A) + 승인 액션 핸들러(Phase B).

- handle_agent_request: MESSAGE 로 들어온 복잡 요청 → 계획 수립 → Firestore 저장 → 승인 카드.
- handle_agent_action: CARD_CLICKED ag_* → 승인/수정/취소. 승인 시에만 background 실행 시작.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from domains.agent import cards, store
from domains.agent.orchestrator import (
    OUTCOME_DONE,
    OUTCOME_NEEDS_REAPPROVAL,
    OUTCOME_NEEDS_USER,
    execute_plan,
)
from domains.agent.planner import create_plan, revise_plan

logger = logging.getLogger(__name__)

# 명료화 라운드 상한 — 무한 되묻기 루프 방지(초기 ask 포함 카운트).
_MAX_CLARIFY_ATTEMPTS = 3


def _user_email(chat_event: dict[str, Any] | None) -> str:
    return str(((chat_event or {}).get("user") or {}).get("email") or "").strip()


def _user_name(chat_event: dict[str, Any] | None) -> str:
    user = (chat_event or {}).get("user") or {}
    return str(user.get("displayName") or user.get("name") or "").strip()


def _space_name(chat_event: dict[str, Any] | None) -> str:
    return str(((chat_event or {}).get("space") or {}).get("name") or "").strip()


def _make_push(space_name: str):
    """진행상황을 space 로 push 하는 함수. space 없으면 no-op."""
    if not space_name:
        return lambda _msg: None

    def _push(text: str) -> None:
        try:
            from api.chat.messages import post_message_to_space

            post_message_to_space(space_name=space_name, payload={"text": text})
        except Exception:
            logger.warning("agent 진행상황 push 실패", exc_info=True)

    return _push


def handle_agent_request(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
    ctx_block: str = "",
) -> dict[str, Any]:
    """Phase A — 계획을 세워 승인 카드로 반환한다. 실행은 승인 후."""
    user_email = _user_email(chat_event)
    user_name = _user_name(chat_event)
    space_name = _space_name(chat_event)

    if space_name:
        from api.chat.loading import loading_text, start_background

        start_background(
            _run_create_plan_background,
            user_message,
            chat_event,
            ctx_block,
        )
        return loading_text("계획을 세우는 중이에요…")

    return _build_plan_response(
        user_message,
        user_email=user_email,
        user_name=user_name,
        space_name=space_name,
        ctx_block=ctx_block,
    )


def _build_plan_response(
    user_message: str,
    *,
    user_email: str,
    user_name: str,
    space_name: str,
    ctx_block: str,
) -> dict[str, Any]:
    from domains.agent import memory

    memory_block = memory.format_memory_block(memory.load_user_memory(user_email))
    plan = create_plan(
        user_message,
        user_name=user_name,
        user_email=user_email,
        ctx_block=ctx_block,
        memory_block=memory_block,
    )

    if not plan.get("steps"):
        ask = plan.get("ask_user") or "요청을 조금 더 구체적으로 알려주시겠어요?"
        # 되물음을 영속화 — 후속 답변을 원 요청과 병합·재계획하기 위한 '명료화 대기' 상태.
        if user_email and space_name:
            try:
                store.save_clarification(
                    user_message, ask, user_email=user_email, space_name=space_name
                )
            except Exception:
                logger.exception("clarification 저장 실패")
        return cards.build_ask_user_card(ask)

    if not space_name:
        return {"text": _plan_preview_text(plan)}

    try:
        plan_id = store.save_plan(plan, user_email=user_email, space_name=space_name)
    except Exception:
        logger.exception("계획 저장 실패")
        return {"text": "계획을 저장하지 못했어요. 잠시 후 다시 시도해 주세요."}

    return cards.build_plan_approval_card(
        plan_id=plan_id,
        goal=plan.get("goal", ""),
        steps=plan.get("steps", []),
        rationale=plan.get("plan_rationale", ""),
        critique_note=plan.get("critique_note", ""),
    )


def _run_create_plan_background(
    user_message: str,
    chat_event: dict[str, Any] | None,
    ctx_block: str,
) -> None:
    space_name = _space_name(chat_event)
    if not space_name:
        return
    try:
        out = _build_plan_response(
            user_message,
            user_email=_user_email(chat_event),
            user_name=_user_name(chat_event),
            space_name=space_name,
            ctx_block=ctx_block,
        )
        from api.chat.messages import post_message_to_space

        post_message_to_space(space_name=space_name, payload=out)
    except Exception:
        logger.exception("agent plan background 실패")
        try:
            from api.chat.messages import post_message_to_space

            post_message_to_space(
                space_name=space_name,
                payload={"text": "계획을 세우지 못했어요. 잠시 후 다시 시도해 주세요."},
            )
        except Exception:
            logger.warning("agent plan 실패 알림 push 실패", exc_info=True)


def handle_agent_clarification(
    answer: str,
    *,
    chat_event: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
    ctx_block: str = "",
) -> dict[str, Any]:
    """'명료화 대기' 중 들어온 후속 답변을 원 요청과 병합해 재계획한다."""
    if not existing:
        return handle_agent_request(answer, chat_event=chat_event, ctx_block=ctx_block)

    space_name = _space_name(chat_event)
    if space_name:
        from api.chat.loading import loading_text, start_background

        start_background(_run_clarify_background, answer, chat_event, existing, ctx_block)
        return loading_text("이어서 계획을 세우는 중이에요…")

    return _build_clarify_response(
        answer,
        existing=existing,
        user_email=_user_email(chat_event),
        user_name=_user_name(chat_event),
        space_name=space_name,
        ctx_block=ctx_block,
    )


def _merge_clarification(original: str, answer: str) -> str:
    original = (original or "").strip()
    answer = (answer or "").strip()
    if not original:
        return answer
    return f"{original}\n(추가 정보: {answer})"


def _build_clarify_response(
    answer: str,
    *,
    existing: dict[str, Any],
    user_email: str,
    user_name: str,
    space_name: str,
    ctx_block: str,
) -> dict[str, Any]:
    from domains.agent import memory

    clar_id = str(existing.get("plan_id") or "").strip()
    attempts = int(existing.get("clarify_attempts") or 1)
    merged = _merge_clarification(str(existing.get("goal") or ""), answer)

    memory_block = memory.format_memory_block(memory.load_user_memory(user_email))
    plan = create_plan(
        merged,
        user_name=user_name,
        user_email=user_email,
        ctx_block=ctx_block,
        memory_block=memory_block,
    )

    # 명료화 레코드는 어느 경로로 끝나든 닫는다(steps 성공·재명료화·포기) — 누수 방지.
    if clar_id:
        store.clear_clarification(clar_id)

    if plan.get("steps"):
        if not space_name:
            return {"text": _plan_preview_text(plan)}
        try:
            plan_id = store.save_plan(plan, user_email=user_email, space_name=space_name)
        except Exception:
            logger.exception("계획 저장 실패")
            return {"text": "계획을 저장하지 못했어요. 잠시 후 다시 시도해 주세요."}
        return cards.build_plan_approval_card(
            plan_id=plan_id,
            goal=plan.get("goal", ""),
            steps=plan.get("steps", []),
            rationale=plan.get("plan_rationale", ""),
            critique_note=plan.get("critique_note", ""),
        )

    # 여전히 정보 부족 — 상한 내면 새 명료화 레코드로 한 번 더, 넘으면 정중히 종료.
    ask = plan.get("ask_user") or "조금 더 구체적으로 알려주시겠어요?"
    if attempts >= _MAX_CLARIFY_ATTEMPTS:
        return cards.build_ask_user_card(
            f"{ask}\n\n계속 어려우면 기능 이름(예: '주간보고초안')이나 문서명을 직접 알려주세요."
        )
    if user_email and space_name:
        try:
            store.save_clarification(
                merged, ask, user_email=user_email, space_name=space_name, attempts=attempts + 1
            )
        except Exception:
            logger.exception("clarification 갱신 실패")
    return cards.build_ask_user_card(ask)


def _run_clarify_background(
    answer: str,
    chat_event: dict[str, Any] | None,
    existing: dict[str, Any],
    ctx_block: str,
) -> None:
    space_name = _space_name(chat_event)
    if not space_name:
        return
    try:
        out = _build_clarify_response(
            answer,
            existing=existing,
            user_email=_user_email(chat_event),
            user_name=_user_name(chat_event),
            space_name=space_name,
            ctx_block=ctx_block,
        )
        from api.chat.messages import post_message_to_space

        post_message_to_space(space_name=space_name, payload=out)
    except Exception:
        logger.exception("agent clarify background 실패")
        try:
            from api.chat.messages import post_message_to_space

            post_message_to_space(
                space_name=space_name,
                payload={"text": "계획을 세우지 못했어요. 잠시 후 다시 시도해 주세요."},
            )
        except Exception:
            logger.warning("clarify 실패 알림 push 실패", exc_info=True)


def handle_agent_revision(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
    existing_plan: dict[str, Any] | None = None,
    ctx_block: str = "",
) -> dict[str, Any]:
    """승인 대기 plan 을 사용자 자연어 수정 요청으로 갱신 → 같은 plan_id 승인 카드."""
    if not existing_plan:
        return handle_agent_request(user_message, chat_event=chat_event, ctx_block=ctx_block)

    plan_id = str(existing_plan.get("plan_id") or "").strip()
    if not plan_id:
        return handle_agent_request(user_message, chat_event=chat_event, ctx_block=ctx_block)

    user_email = _user_email(chat_event)
    user_name = _user_name(chat_event)
    space_name = _space_name(chat_event)

    if space_name:
        from api.chat.loading import loading_text, start_background

        start_background(
            _run_revision_background,
            user_message,
            chat_event,
            existing_plan,
            ctx_block,
        )
        return loading_text("계획을 수정하는 중이에요…")

    return _build_revision_response(
        user_message,
        existing_plan=existing_plan,
        plan_id=plan_id,
        user_email=user_email,
        user_name=user_name,
        space_name=space_name,
        ctx_block=ctx_block,
    )


def _build_revision_response(
    user_message: str,
    *,
    existing_plan: dict[str, Any],
    plan_id: str,
    user_email: str,
    user_name: str,
    space_name: str,
    ctx_block: str,
) -> dict[str, Any]:
    from domains.agent import memory

    memory_block = memory.format_memory_block(memory.load_user_memory(user_email))
    revised = revise_plan(
        existing_plan,
        user_message,
        user_name=user_name,
        user_email=user_email,
        ctx_block=ctx_block,
        memory_block=memory_block,
    )

    if not revised.get("steps"):
        ask = revised.get("ask_user") or "수정 내용을 조금 더 구체적으로 알려주시겠어요?"
        return cards.build_ask_user_card(ask)

    if not space_name:
        return {"text": _plan_preview_text(revised)}

    try:
        store.update_plan(plan_id, revised)
    except Exception:
        logger.exception("계획 수정 저장 실패 plan_id=%s", plan_id)
        return {"text": "계획 수정을 저장하지 못했어요. 잠시 후 다시 시도해 주세요."}

    out = cards.build_plan_approval_card(
        plan_id=plan_id,
        goal=revised.get("goal", ""),
        steps=revised.get("steps", []),
        rationale=revised.get("plan_rationale", ""),
        critique_note=revised.get("critique_note", ""),
    )
    out["text"] = "계획을 수정했어요. 검토 후 승인해 주세요."
    return out


def _run_revision_background(
    user_message: str,
    chat_event: dict[str, Any] | None,
    existing_plan: dict[str, Any],
    ctx_block: str,
) -> None:
    space_name = _space_name(chat_event)
    plan_id = str(existing_plan.get("plan_id") or "").strip()
    if not space_name or not plan_id:
        return
    try:
        out = _build_revision_response(
            user_message,
            existing_plan=existing_plan,
            plan_id=plan_id,
            user_email=_user_email(chat_event),
            user_name=_user_name(chat_event),
            space_name=space_name,
            ctx_block=ctx_block,
        )
        from api.chat.messages import post_message_to_space

        post_message_to_space(space_name=space_name, payload=out)
    except Exception:
        logger.exception("agent revision background 실패 plan_id=%s", plan_id)


def _plan_preview_text(plan: dict[str, Any]) -> str:
    lines = [f"계획: {plan.get('goal', '')}"]
    for s in plan.get("steps", []):
        lines.append(f"  {s.get('n')}. {s.get('why') or s.get('tool')}")
    return "\n".join(lines)


def handle_agent_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    chat_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """CARD_CLICKED ag_* 처리."""
    fn = (invoked_function or "").strip()

    # 하이브리드 랜딩의 'AI에게 맡기기' — 원본 발화로 계획 수립 흐름에 진입.
    if fn == "ag_delegate":
        msg = (parameters or {}).get("user_message", "")
        if not msg.strip():
            return {"text": "맡길 요청 내용을 찾지 못했어요. 다시 말씀해 주세요."}
        from domains.routing.context import load_ctx_block

        return handle_agent_request(msg, chat_event=chat_event, ctx_block=load_ctx_block(chat_event or {}))

    plan_id = (parameters or {}).get("plan_id", "").strip()
    if not plan_id:
        return {"text": "계획 정보를 찾을 수 없습니다."}

    plan = store.load_plan(plan_id)
    if plan is None:
        return {"text": "계획을 찾을 수 없거나 만료되었습니다."}

    if fn == "ag_reject":
        store.set_status(plan_id, store.STATUS_REJECTED)
        return {"text": "계획을 취소했어요. 다시 요청해 주시면 새로 세워드릴게요."}

    if fn == "ag_edit":
        return {
            "text": (
                "수정 내용을 채팅으로 보내주세요. 예: '2주로 바꿔줘'. "
                "같은 계획에 반영해 다시 보여드릴게요."
            )
        }

    if fn == "ag_approve":
        # 재승인(pending_reapproval) 또는 최초 승인(pending_approval) 모두 approved 로.
        store.set_status(plan_id, store.STATUS_APPROVED)
        space_name = plan.get("space_name") or _space_name(chat_event)

        if not space_name:
            # 동기 실행 (챗 외) — 드물지만 안전하게 처리.
            outcome = execute_plan(plan_id)
            return {"text": outcome.get("message", "완료")}

        threading.Thread(
            target=_run_plan_background,
            args=(plan_id, space_name),
            daemon=False,
        ).start()
        return {"text": "✅ 승인됐어요. 지금부터 실행할게요. 진행 상황을 알려드릴게요."}

    return {"text": "알 수 없는 동작입니다."}


def _run_plan_background(plan_id: str, space_name: str) -> None:
    """background 실행 + 최종 결과 push."""
    push = _make_push(space_name)
    try:
        outcome = execute_plan(plan_id, push=push)
    except Exception:
        logger.exception("agent 실행 실패 plan_id=%s", plan_id)
        push("실행 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.")
        return

    kind = outcome.get("outcome")
    if kind == OUTCOME_NEEDS_REAPPROVAL:
        from api.chat.messages import post_message_to_space

        post_message_to_space(
            space_name=space_name,
            payload=cards.build_reapproval_card(
                plan_id=plan_id,
                message=outcome.get("message", ""),
                steps=outcome.get("steps", []),
            ),
        )
        return

    if kind in (OUTCOME_DONE, OUTCOME_NEEDS_USER):
        push(outcome.get("message", "완료"))
    else:  # failed
        push(outcome.get("message", "작업을 끝내지 못했어요."))
