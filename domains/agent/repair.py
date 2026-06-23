"""Phase C 자가복구 — error_kind 분류 + LLM repair 서브루프 + 재승인 경계.

복구 가능한 오류는 스스로 고쳐 계속 진행하고, 사람만 해결할 수 있는 오류(권한 등)는
멈춰서 사용자에게 넘긴다.
"""

from __future__ import annotations

import logging
from typing import Any

from domains.agent._llm import PLANNER_MODEL, generate_json
from domains.agent.prompts import build_repair_prompt
from domains.agent.tools import get_tool, tools_catalog

logger = logging.getLogger(__name__)

# 일시 오류 — 그대로 재시도(backoff).
RETRY_KINDS = {"http_error", "calendar_http_error"}

# 인자/대상 문제 — LLM 으로 교정 시도.
LLM_FIX_KINDS = {
    "not_found",
    "calendar_not_found",
    "empty_query",
    "missing_args",
    "missing_source",
    "ref_unresolved",
    "event_field_missing",
    "calendar_id_missing",
    "account_id_unresolved",
}

# skip 금지 — 건너뛰면 목표 달성이 불가능한 오류.
SKIP_FORBIDDEN_KINDS = frozenset(
    {"ref_unresolved", "missing_source", "missing_args", "empty_query"}
)

# 사람만 해결 가능 — 자동 복구 불가, 사용자에게 넘긴다.
USER_REQUIRED_KINDS = {"auth_required", "auth_error", "calendar_auth_error"}

# 분류 결과
CAT_RETRY = "retry"
CAT_LLM_FIX = "llm_fix"
CAT_USER_REQUIRED = "user_required"


def classify_error(error_kind: str) -> str:
    """error_kind → 복구 카테고리. 미지의 종류는 LLM 교정 시도로 보낸다."""
    kind = (error_kind or "").strip()
    if kind in USER_REQUIRED_KINDS:
        return CAT_USER_REQUIRED
    if kind in RETRY_KINDS:
        return CAT_RETRY
    if kind in LLM_FIX_KINDS:
        return CAT_LLM_FIX
    return CAT_LLM_FIX


def skip_allowed(
    error_kind: str,
    *,
    results: dict[str, Any],
    failed_step: dict[str, Any],
) -> bool:
    """이 오류에서 skip 을 허용할지. 검색 0건·$ref 실패·필수 source 누락은 skip 불가."""
    kind = (error_kind or "").strip()
    if kind in SKIP_FORBIDDEN_KINDS:
        return False
    tool_name = str(failed_step.get("tool") or "")
    if tool_name in {"search_confluence", "search_emails", "get_confluence_page_body", "get_email_body"}:
        return False
    # upstream 검색이 found=false 였으면 skip 금지
    for val in (results or {}).values():
        if isinstance(val, dict) and val.get("found") is False:
            return False
    return True


def llm_repair(
    *,
    goal: str,
    approved_steps: list[dict[str, Any]],
    results: dict[str, Any],
    failed_step: dict[str, Any],
    error_kind: str,
    detail: str,
) -> dict[str, Any]:
    """실패 단계 복구안 1개 생성. {action, tool, new_args, reason}. 실패 시 abort."""
    prompt = build_repair_prompt(
        goal=goal,
        approved_steps=approved_steps,
        results=results,
        failed_step=failed_step,
        error_kind=error_kind,
        detail=detail,
        catalog=tools_catalog(),
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    action = str(data.get("action") or "").strip().lower()
    if action not in ("retry", "fix_args", "switch_tool", "skip", "abort"):
        return {"action": "abort", "reason": "복구안을 생성하지 못함"}
    return {
        "action": action,
        "tool": str(data.get("tool") or "").strip(),
        "new_args": data.get("new_args") if isinstance(data.get("new_args"), dict) else {},
        "reason": str(data.get("reason") or ""),
    }


def is_unapproved_side_effect(
    tool_name: str,
    *,
    approved_steps: list[dict[str, Any]],
) -> bool:
    """``tool_name`` 이 '승인된 계획에 없던 side_effect 도구'인지 판정.

    True 면 자동 실행 금지 → 재승인 게이트로 보내야 한다. repair/ReAct 공용.
    """
    tool = get_tool(str(tool_name or ""))
    if tool is None or not tool.side_effect:
        return False
    approved_tools = {str(s.get("tool") or "") for s in approved_steps}
    return tool.name not in approved_tools


def introduces_new_side_effect(
    fix: dict[str, Any],
    *,
    approved_steps: list[dict[str, Any]],
) -> bool:
    """복구안이 '승인된 계획에 없던 새 side_effect 도구'를 도입하는지 판정.

    True 면 자동 실행 금지 → 재승인 게이트로 보내야 한다.
    """
    if fix.get("action") != "switch_tool":
        return False
    return is_unapproved_side_effect(str(fix.get("tool") or ""), approved_steps=approved_steps)


def apply_fix(step: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    """복구안을 단계에 반영한 새 step 반환 (retry/fix_args/switch_tool)."""
    new_step = dict(step)
    action = fix.get("action")
    if action == "fix_args" and fix.get("new_args"):
        merged = dict(step.get("args") or {})
        merged.update(fix["new_args"])
        new_step["args"] = merged
    elif action == "switch_tool" and fix.get("tool"):
        new_step["tool"] = fix["tool"]
        if fix.get("new_args"):
            new_step["args"] = fix["new_args"]
        tool = get_tool(fix["tool"])
        new_step["side_effect"] = bool(tool.side_effect) if tool else step.get("side_effect")
    return new_step
