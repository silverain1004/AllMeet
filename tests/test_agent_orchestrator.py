"""멀티스텝 에이전트 — 실행 루프 / 자가복구 / 계획 정규화 / 도구 레지스트리 테스트.

LLM·Firestore·외부 API 호출 없이 동작하도록, 가짜 도구를 레지스트리에 등록하고
repair.llm_repair / store 를 monkeypatch 한다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from domains.agent import orchestrator, repair
from domains.agent.tools import Tool, get_tool, register


# ---------------------------------------------------------------------------
# 가짜 도구 등록 (모듈 import 시 1회)
# ---------------------------------------------------------------------------

_calls: dict[str, int] = {}


def _reset_calls() -> None:
    _calls.clear()


def _run_ok(**kwargs: Any) -> dict[str, Any]:
    _calls["t_ok"] = _calls.get("t_ok", 0) + 1
    return {"ok": True, "value": kwargs.get("echo", "done")}


def _run_auth_fail(**_: Any) -> dict[str, Any]:
    return {"ok": False, "error_kind": "auth_required"}


def _run_needs_fix(**kwargs: Any) -> dict[str, Any]:
    # args 에 fixed=True 가 들어와야 성공. 아니면 not_found 로 실패.
    _calls["t_fix"] = _calls.get("t_fix", 0) + 1
    if kwargs.get("fixed"):
        return {"ok": True}
    return {"ok": False, "error_kind": "not_found"}


def _run_always_fail(**_: Any) -> dict[str, Any]:
    _calls["t_fail"] = _calls.get("t_fail", 0) + 1
    return {"ok": False, "error_kind": "not_found"}


register(Tool(name="t_ok", description="ok", run=_run_ok))
register(Tool(name="t_auth_fail", description="auth", run=_run_auth_fail))
register(Tool(name="t_needs_fix", description="fix", run=_run_needs_fix))
register(Tool(name="t_fail", description="fail", run=_run_always_fail))


def _run_search_empty(**_: Any) -> dict[str, Any]:
    return {"ok": True, "pages": [], "count": 0, "found": False}


def _run_need_page_id(**kwargs: Any) -> dict[str, Any]:
    if not str(kwargs.get("page_id") or "").strip():
        return {"ok": False, "error_kind": "missing_args", "detail": "page_id 필요"}
    return {"ok": True}


register(Tool(name="t_search_empty", description="empty search", run=_run_search_empty))
register(Tool(name="t_need_page_id", description="needs page", run=_run_need_page_id))


@pytest.fixture(autouse=True)
def _clear():
    _reset_calls()
    yield


# ---------------------------------------------------------------------------
# resolve_args (late-binding)
# ---------------------------------------------------------------------------


def test_resolve_args_resolves_ref_from_prior_results():
    results = {1: {"busy": {"cal": [{"start": "x"}]}}}
    args = {"calendar": {"$ref": "1.busy"}, "literal": 5}
    out = orchestrator.resolve_args(args, results)
    assert out["calendar"] == {"cal": [{"start": "x"}]}
    assert out["literal"] == 5


def test_resolve_args_ref_missing_returns_none():
    assert orchestrator.resolve_args({"$ref": "9.nope"}, {}) is None


# ---------------------------------------------------------------------------
# error 분류
# ---------------------------------------------------------------------------


def test_classify_error_categories():
    assert repair.classify_error("auth_required") == repair.CAT_USER_REQUIRED
    assert repair.classify_error("calendar_auth_error") == repair.CAT_USER_REQUIRED
    assert repair.classify_error("http_error") == repair.CAT_RETRY
    assert repair.classify_error("not_found") == repair.CAT_LLM_FIX
    assert repair.classify_error("weird_unknown") == repair.CAT_LLM_FIX


# ---------------------------------------------------------------------------
# run_loop 시나리오
# ---------------------------------------------------------------------------


def test_run_loop_success_single_step():
    steps = [{"n": 1, "tool": "t_ok", "args": {"echo": "hi"}, "side_effect": False}]
    out = orchestrator.run_loop(goal="g", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    assert out["results"][1]["value"] == "hi"


def test_run_loop_user_required_stops_immediately():
    steps = [{"n": 1, "tool": "t_auth_fail", "args": {}, "side_effect": False}]
    out = orchestrator.run_loop(goal="g", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_USER
    assert "연결" in out["message"]


def test_run_loop_self_heals_with_fix_args():
    steps = [{"n": 1, "tool": "t_needs_fix", "args": {}, "side_effect": False}]
    fix = {"action": "fix_args", "tool": "", "new_args": {"fixed": True}, "reason": "교정"}
    with patch.object(repair, "llm_repair", return_value=fix):
        out = orchestrator.run_loop(goal="g", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    assert _calls["t_fix"] == 2  # 실패 1 + 보정 후 성공 1


def test_run_loop_retry_budget_exhausted_asks_user():
    steps = [{"n": 1, "tool": "t_fail", "args": {}, "side_effect": False}]
    fix = {"action": "fix_args", "tool": "", "new_args": {}, "reason": "헛수고"}
    with patch.object(repair, "llm_repair", return_value=fix):
        out = orchestrator.run_loop(goal="g", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_USER


def test_run_loop_new_side_effect_triggers_reapproval():
    # 읽기 단계가 실패하고, 복구안이 '승인 계획에 없던 side_effect 도구(create_meeting)'로 전환 시도.
    steps = [{"n": 1, "tool": "t_fail", "args": {}, "side_effect": False}]
    fix = {"action": "switch_tool", "tool": "create_meeting", "new_args": {}, "reason": "다른 방법"}
    with patch.object(repair, "llm_repair", return_value=fix):
        out = orchestrator.run_loop(goal="g", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_REAPPROVAL
    assert out["steps"][0]["tool"] == "create_meeting"


def test_run_loop_skips_already_completed_steps_on_resume():
    steps = [
        {"n": 1, "tool": "t_ok", "args": {}, "side_effect": False},
        {"n": 2, "tool": "t_ok", "args": {}, "side_effect": False},
    ]
    out = orchestrator.run_loop(goal="g", steps=steps, results={1: {"ok": True, "value": "pre"}})
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    assert out["results"][1]["value"] == "pre"  # 1번은 재실행 안 됨
    assert _calls.get("t_ok") == 1  # 2번만 실행


# ---------------------------------------------------------------------------
# execute_plan — 승인 게이트 강제
# ---------------------------------------------------------------------------


def test_execute_plan_rejects_unapproved_status():
    from domains.agent import store

    plan = {"status": store.STATUS_PENDING, "goal": "g", "steps": [], "results": {}}
    with patch.object(store, "load_plan", return_value=plan):
        out = orchestrator.execute_plan("pid")
    assert out["outcome"] == orchestrator.OUTCOME_FAILED
    assert "승인" in out["message"]


def test_run_loop_empty_search_stops_with_needs_user():
    steps = [
        {
            "n": 1,
            "tool": "t_search_empty",
            "args": {"query": "작년 챗봇 보고서"},
            "side_effect": False,
        }
    ]
    out = orchestrator.run_loop(goal="로드맵", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_USER
    assert "검색 결과" in out["message"]


def test_run_loop_ref_unresolved_before_tool_run():
    steps = [
        {
            "n": 2,
            "tool": "t_need_page_id",
            "args": {"page_id": {"$ref": "1.pages.0.id"}},
            "side_effect": False,
        }
    ]
    pre = {1: {"ok": True, "pages": [], "found": False, "count": 0}}
    fix = {"action": "skip", "tool": "", "new_args": {}, "reason": "skip"}
    with patch.object(repair, "llm_repair", return_value=fix):
        out = orchestrator.run_loop(goal="g", steps=steps, results=pre)
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_USER
    assert _calls.get("t_need_page_id", 0) == 0


def test_execute_plan_marks_failed_when_verify_not_achieved():
    from domains.agent import store

    plan = {
        "status": store.STATUS_APPROVED,
        "goal": "로드맵",
        "steps": [{"n": 1, "tool": "t_ok", "args": {}, "side_effect": False}],
        "results": {},
        "user_email": "u@x.com",
    }
    with patch.object(store, "load_plan", return_value=plan), \
         patch.object(store, "set_status") as set_status, \
         patch.object(store, "save_results"), \
         patch.object(orchestrator, "verify_outcome", return_value={"achieved": False, "reason": "보고서 없음"}):
        out = orchestrator.execute_plan("pid")
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_USER
    assert "달성하지 못" in out["message"]
    statuses = [c.args[1] for c in set_status.call_args_list]
    assert store.STATUS_FAILED in statuses


def test_execute_plan_runs_when_approved():
    from domains.agent import store

    plan = {
        "status": store.STATUS_APPROVED,
        "goal": "g",
        "steps": [{"n": 1, "tool": "t_ok", "args": {}, "side_effect": False}],
        "results": {},
        "user_email": "u@x.com",
    }
    with patch.object(store, "load_plan", return_value=plan), \
         patch.object(store, "set_status") as set_status, \
         patch.object(store, "save_results"), \
         patch.object(orchestrator, "verify_outcome", return_value={"achieved": True, "reason": ""}), \
         patch("domains.agent.memory.record_artifacts"):
        out = orchestrator.execute_plan("pid")
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    # running → done 으로 전이
    statuses = [c.args[1] for c in set_status.call_args_list]
    assert store.STATUS_RUNNING in statuses
    assert store.STATUS_DONE in statuses


def test_run_loop_done_message_includes_generate_content():
    steps = [
        {"n": 1, "tool": "t_ok", "args": {}, "side_effect": False},
    ]

    def _gen(**_: Any) -> dict[str, Any]:
        return {"ok": True, "content": "주간보고 핵심: 배포 완료, 다음 주 리팩터링"}

    register(Tool(name="t_gen", description="gen", run=_gen))
    steps.append({"n": 2, "tool": "t_gen", "args": {}, "side_effect": False})

    out = orchestrator.run_loop(goal="주간보고 요약", steps=steps)
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    assert "주간보고 핵심" in out["message"]
    assert "작업을 마쳤어요" in out["message"]
