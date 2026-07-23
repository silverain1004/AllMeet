"""Phase A — 계획 수립.

사용자 요청을 받아 '실행 없는' 구조화 계획(JSON)을 만든다. 실행은 승인 이후
orchestrator 가 담당한다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from domains.agent import registry  # noqa: F401 - import 시 도구 등록
from domains.agent._llm import PLANNER_MODEL, generate_json
from domains.agent.prompts import (
    build_critique_prompt,
    build_next_step_prompt,
    build_planner_prompt,
    build_revise_prompt,
)
from domains.agent.tools import get_tool, select_catalog, tools_catalog

logger = logging.getLogger(__name__)


def create_plan(
    user_message: str,
    *,
    user_name: str = "",
    user_email: str = "",
    ctx_block: str = "",
    memory_block: str = "",
    recipes_block: str = "",
) -> dict[str, Any]:
    """사용자 요청 → 계획 dict.

    Returns:
        {
          "goal": str,
          "steps": [{"n", "tool", "args", "why", "side_effect"}],
          "needs_confirmation": bool,
          "ask_user": str,     # 정보 부족 시 사용자에게 물을 문장 (있으면 steps 비어 있음)
        }
        LLM 실패 시 {"goal": "", "steps": [], "ask_user": "..."} 로 안전 반환.
    """
    catalog = select_catalog(user_message)
    prompt = build_planner_prompt(
        user_message=user_message,
        catalog=catalog,
        today=datetime.now().strftime("%Y-%m-%d (%A)"),
        user_name=user_name or "사용자",
        user_email=user_email,
        ctx_block=ctx_block,
        memory_block=memory_block,
        recipes_block=recipes_block,
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    if not data:
        return {
            "goal": "",
            "plan_rationale": "",
            "critique_note": "",
            "steps": [],
            "needs_confirmation": True,
            "ask_user": "요청을 계획으로 분해하지 못했어요. 조금 더 구체적으로 말씀해 주시겠어요?",
            "planner_model": PLANNER_MODEL,
        }
    plan = _normalize_plan(data)
    if plan.get("steps"):
        plan = critique_plan(plan, user_message)
    _sanitize_email_args(plan.get("steps") or [], user_email)
    plan["planner_model"] = PLANNER_MODEL
    return plan


def revise_plan(
    existing_plan: dict[str, Any],
    user_message: str,
    *,
    user_name: str = "",
    user_email: str = "",
    ctx_block: str = "",
    memory_block: str = "",
) -> dict[str, Any]:
    """승인 대기 중 plan 을 사용자 수정 요청에 맞게 갱신."""
    catalog = select_catalog(user_message)
    prompt = build_revise_prompt(
        user_message=user_message,
        goal=str(existing_plan.get("goal") or ""),
        steps=existing_plan.get("steps") or [],
        plan_rationale=str(existing_plan.get("plan_rationale") or ""),
        catalog=catalog,
        today=datetime.now().strftime("%Y-%m-%d (%A)"),
        user_name=user_name or "사용자",
        user_email=user_email,
        ctx_block=ctx_block,
        memory_block=memory_block,
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    if not data:
        return {
            **existing_plan,
            "ask_user": "수정 내용을 반영하지 못했어요. 조금 더 구체적으로 말씀해 주시겠어요?",
            "steps": existing_plan.get("steps") or [],
        }
    plan = _normalize_plan(data)
    if plan.get("steps"):
        plan = critique_plan(plan, user_message)
    _sanitize_email_args(plan.get("steps") or [], user_email)
    plan["planner_model"] = PLANNER_MODEL
    return plan


def critique_plan(plan: dict[str, Any], user_message: str) -> dict[str, Any]:
    """Phase 1B — 실행 전 계획 자가검수. 보정안이 오면 steps 를 교체하고 critique_note 를 남긴다.

    LLM 실패 시 원본 계획을 그대로 반환(안전).
    """
    prompt = build_critique_prompt(
        goal=plan.get("goal", ""),
        steps=plan.get("steps", []),
        user_message=user_message,
        catalog=tools_catalog(),
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    if not data:
        return plan

    note = str(data.get("note") or "")
    revised = data.get("revised_steps")
    if isinstance(revised, list) and revised:
        renormalized = _normalize_plan({**plan, "steps": revised})
        if renormalized.get("steps"):
            plan = {**plan, "steps": renormalized["steps"]}
    if note:
        plan = {**plan, "critique_note": note}
    return plan


def decide_next_step(
    *,
    goal: str,
    approved_steps: list[dict[str, Any]],
    results: dict[Any, Any],
) -> dict[str, Any]:
    """Phase 3(ReAct) — 결과를 관찰해 다음 행동 결정. {action, step}.

    실패/미지 응답 시 action="continue" (안전: 정적 계획 그대로 진행).
    """
    prompt = build_next_step_prompt(
        goal=goal,
        approved_steps=approved_steps,
        results={str(k): v for k, v in (results or {}).items()},
        catalog=tools_catalog(),
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    action = str(data.get("action") or "continue").strip().lower()
    if action not in ("continue", "insert_step", "done"):
        return {"action": "continue", "step": None}
    step = data.get("step")
    if action == "insert_step" and isinstance(step, dict) and step.get("tool"):
        tool = get_tool(str(step.get("tool")))
        if tool is None:
            return {"action": "continue", "step": None}
        return {
            "action": "insert_step",
            "step": {
                "tool": str(step.get("tool")),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "why": str(step.get("why") or ""),
                "side_effect": bool(tool.side_effect),
            },
        }
    return {"action": action, "step": None}


# LLM 이 실제 이메일 대신 자리표시자를 넣는 사고 방지 (예: "placeholder@user.email" 로
# list_confluence_pages_by_author 가 account_id_unresolved 로 죽던 사례).
_EMAIL_ARG_KEYS = ("modified_by_email", "user_email")
_PLACEHOLDER_EMAIL_RE = re.compile(
    r"placeholder|example\.|sample|user\.email|@company|@domain|@email\b|^me@|^test@|^user@|^your",
    re.IGNORECASE,
)


def _sanitize_email_args(steps: list[dict[str, Any]], user_email: str) -> None:
    """step args 의 이메일 인자가 자리표시자·비정상 값이면 요청자 이메일로 교체 (in-place)."""
    real = (user_email or "").strip()
    if not real:
        return
    for s in steps:
        args = s.get("args")
        if not isinstance(args, dict):
            continue
        for key in _EMAIL_ARG_KEYS:
            if key not in args:
                continue
            val = str(args.get(key) or "").strip()
            if not val or "@" not in val or _PLACEHOLDER_EMAIL_RE.search(val):
                logger.info("planner: 이메일 인자 보정 tool=%s %s=%r → 요청자", s.get("tool"), key, val)
                args[key] = real


def _normalize_plan(data: dict[str, Any]) -> dict[str, Any]:
    """LLM 출력 정규화 + 알 수 없는 도구 제거 + side_effect 재계산(레지스트리 기준)."""
    raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps: list[dict[str, Any]] = []
    for idx, s in enumerate(raw_steps, start=1):
        if not isinstance(s, dict):
            continue
        tool_name = str(s.get("tool") or "").strip()
        tool = get_tool(tool_name)
        if tool is None:
            logger.warning("planner: 알 수 없는 도구 무시 tool=%s", tool_name)
            continue
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        steps.append(
            {
                "n": int(s.get("n") or idx),
                "tool": tool_name,
                "args": args,
                "why": str(s.get("why") or ""),
                # side_effect 는 레지스트리가 진실. LLM 값 신뢰 안 함.
                "side_effect": bool(tool.side_effect),
            }
        )
    return {
        "goal": str(data.get("goal") or ""),
        "plan_rationale": str(data.get("plan_rationale") or ""),
        "critique_note": str(data.get("critique_note") or ""),
        "steps": steps,
        "needs_confirmation": bool(data.get("needs_confirmation", True)),
        "ask_user": str(data.get("ask_user") or ""),
    }
