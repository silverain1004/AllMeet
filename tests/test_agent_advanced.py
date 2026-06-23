"""멀티스텝 에이전트 고도화 — Phase 1(retrieval/self-critique) 테스트.

LLM 호출은 generate_json 을 monkeypatch 로 모킹한다.
"""

from __future__ import annotations

from unittest.mock import patch


# ---------------------------------------------------------------------------
# Phase 1A — 도구 retrieval (select_catalog)
# ---------------------------------------------------------------------------


def test_select_catalog_returns_all_under_threshold():
    from domains.agent.tools import TOOL_REGISTRY, select_catalog

    cat = select_catalog("아무 요청", max_tools=100)
    assert len(cat) == len(TOOL_REGISTRY)


def test_select_catalog_filters_but_keeps_side_effect():
    from domains.agent.tools import TOOL_REGISTRY, select_catalog

    side_names = {t.name for t in TOOL_REGISTRY.values() if t.side_effect}
    cat = select_catalog("회의 빈 시간 캘린더 잡아줘", max_tools=8)
    names = {c["name"] for c in cat}

    assert len(cat) <= 8
    # side_effect 도구는 누락 위험 제거 위해 항상 포함
    assert side_names.issubset(names)
    # 요청 키워드와 맞는 읽기 도구가 선택됨
    assert "find_free_slots" in names


# ---------------------------------------------------------------------------
# Phase 1B — critique_plan / verify_outcome
# ---------------------------------------------------------------------------


def _base_plan():
    return {
        "goal": "회의 잡기",
        "plan_rationale": "r",
        "critique_note": "",
        "steps": [
            {"n": 1, "tool": "find_free_slots", "args": {"calendar_ids": ["a"], "time_min": "x", "time_max": "y"}, "side_effect": False},
        ],
        "needs_confirmation": True,
        "ask_user": "",
    }


def test_critique_plan_applies_revised_steps_and_note():
    from domains.agent import planner

    revised = {
        "ok": False,
        "note": "전제 조회 단계를 추가했습니다",
        "revised_steps": [
            {"n": 1, "tool": "list_calendar_events", "args": {"calendar_id": "a", "time_min": "x", "time_max": "y"}},
            {"n": 2, "tool": "find_free_slots", "args": {"calendar_ids": ["a"], "time_min": "x", "time_max": "y"}},
        ],
    }
    with patch.object(planner, "generate_json", return_value=revised):
        out = planner.critique_plan(_base_plan(), "회의 잡아줘")
    tools = [s["tool"] for s in out["steps"]]
    assert tools == ["list_calendar_events", "find_free_slots"]
    assert out["critique_note"] == "전제 조회 단계를 추가했습니다"


def test_critique_plan_no_change_when_llm_empty():
    from domains.agent import planner

    base = _base_plan()
    with patch.object(planner, "generate_json", return_value={}):
        out = planner.critique_plan(base, "회의 잡아줘")
    assert out["steps"] == base["steps"]
    assert out["critique_note"] == ""


def test_create_plan_runs_critique_after_normalize():
    from domains.agent import planner

    planner_out = {
        "goal": "g",
        "plan_rationale": "r",
        "steps": [{"n": 1, "tool": "find_free_slots", "args": {"calendar_ids": ["a"], "time_min": "x", "time_max": "y"}}],
        "needs_confirmation": True,
    }
    critique_out = {"ok": True, "note": "이상 없음", "revised_steps": []}

    # 첫 호출(plan) → planner_out, 둘째 호출(critique) → critique_out
    with patch.object(planner, "generate_json", side_effect=[planner_out, critique_out]):
        plan = planner.create_plan("회의 잡아줘", user_email="u@x.com")
    assert plan["critique_note"] == "이상 없음"
    assert [s["tool"] for s in plan["steps"]] == ["find_free_slots"]


def test_verify_outcome_reports_not_achieved():
    from domains.agent import orchestrator

    with patch("domains.agent._llm.generate_json", return_value={"achieved": False, "reason": "페이지 생성 누락"}):
        verdict = orchestrator.verify_outcome("문서화", {1: {"ok": True}})
    assert verdict["achieved"] is False
    assert "누락" in verdict["reason"]


def test_verify_outcome_safe_fallback_when_llm_empty():
    from domains.agent import orchestrator

    with patch("domains.agent._llm.generate_json", return_value={}):
        verdict = orchestrator.verify_outcome("문서화", {1: {"ok": True}})
    assert verdict["achieved"] is False
    assert verdict["reason"] == "검증 불가"


# ---------------------------------------------------------------------------
# Phase 2 — 사용자 메모리
# ---------------------------------------------------------------------------


def test_extract_artifacts_picks_page_and_event():
    from domains.agent import memory

    results = {
        1: {"ok": True, "pages": [{"id": "x"}]},  # 산출물 아님(조회)
        2: {"ok": True, "page_id": "P-1", "title": "로드맵"},
        3: {"ok": True, "event_id": "E-1", "summary": "킥오프"},
        4: {"ok": False, "page_id": "P-2"},  # 실패는 제외
    }
    arts = memory._extract_artifacts("AI 프로젝트", results)
    kinds = {(a["kind"], a["ref_id"]) for a in arts}
    assert ("page", "P-1") in kinds
    assert ("event", "E-1") in kinds
    assert ("page", "P-2") not in kinds


def test_record_artifacts_appends_to_subcollection():
    from domains.agent import memory

    with patch.object(memory, "document_ref", return_value="REF"), \
         patch.object(memory, "ensure_document") as ensure, \
         patch.object(memory, "append_subcollection_docs") as append:
        memory.record_artifacts("u@x.com", "목표", {1: {"ok": True, "page_id": "P-1"}})
    ensure.assert_called_once()
    append.assert_called_once()
    # 적재된 문서에 page 산출물이 들어갔는지
    _, args, kwargs = append.mock_calls[0]
    docs = args[2]
    assert docs[0]["ref_id"] == "P-1"


def test_record_artifacts_noop_without_user_or_artifacts():
    from domains.agent import memory

    with patch.object(memory, "append_subcollection_docs") as append:
        memory.record_artifacts("", "목표", {1: {"ok": True, "page_id": "P-1"}})  # user 없음
        memory.record_artifacts("u@x.com", "목표", {1: {"ok": True}})  # 산출물 없음
    append.assert_not_called()


def test_load_user_memory_and_format():
    from domains.agent import memory

    rows = [
        {"kind": "page", "ref_id": "P-1", "title": "로드맵", "goal": "AI"},
        {"kind": "event", "ref_id": "E-1", "title": "킥오프", "goal": "AI"},
    ]
    with patch.object(memory, "document_ref", return_value="REF"), \
         patch.object(memory, "list_subcollection_recent_chronological", return_value=rows):
        items = memory.load_user_memory("u@x.com")
    block = memory.format_memory_block(items)
    assert "[페이지] 로드맵" in block
    assert "[회의] 킥오프" in block


def test_planner_prompt_includes_memory_block():
    from domains.agent.prompts import build_planner_prompt

    prompt = build_planner_prompt(
        user_message="이어서 해줘",
        catalog=[],
        today="2026-06-19",
        user_name="홍길동",
        user_email="u@x.com",
        memory_block="- [페이지] 온보딩 가이드 (id=P-1)",
    )
    assert "최근 만든 산출물" in prompt
    assert "온보딩 가이드" in prompt


# ---------------------------------------------------------------------------
# Phase 3 — ReAct 동적 재계획 (하이브리드 승인)
# ---------------------------------------------------------------------------

from domains.agent.tools import Tool, register  # noqa: E402

register(Tool(name="rt_read", description="fake read", run=lambda **_: {"ok": True}))
register(Tool(name="wt_write", description="fake write", run=lambda **_: {"ok": True}, side_effect=True))


def _read_steps():
    return [{"n": 1, "tool": "rt_read", "args": {}, "side_effect": False}]


def test_react_disabled_does_not_call_decider(monkeypatch):
    from domains.agent import orchestrator, planner

    monkeypatch.delenv("AGENT_REACT_ENABLED", raising=False)
    with patch.object(planner, "decide_next_step") as decider:
        out = orchestrator.run_loop(goal="g", steps=_read_steps())
    decider.assert_not_called()
    assert out["outcome"] == orchestrator.OUTCOME_DONE


def test_react_inserts_read_step_then_continues(monkeypatch):
    from domains.agent import orchestrator, planner

    monkeypatch.setenv("AGENT_REACT_ENABLED", "1")
    decisions = [
        {"action": "insert_step", "step": {"tool": "rt_read", "args": {}, "why": "추가 조회", "side_effect": False}},
        {"action": "continue", "step": None},
    ]
    with patch.object(planner, "decide_next_step", side_effect=decisions):
        out = orchestrator.run_loop(goal="g", steps=_read_steps())
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    # 원래 1단계 + 삽입 1단계 = 결과 2건
    assert len(out["results"]) == 2


def test_react_unapproved_write_triggers_reapproval(monkeypatch):
    from domains.agent import orchestrator, planner

    monkeypatch.setenv("AGENT_REACT_ENABLED", "1")
    decision = {"action": "insert_step", "step": {"tool": "wt_write", "args": {}, "why": "쓰기 필요", "side_effect": True}}
    with patch.object(planner, "decide_next_step", return_value=decision):
        out = orchestrator.run_loop(goal="g", steps=_read_steps())
    assert out["outcome"] == orchestrator.OUTCOME_NEEDS_REAPPROVAL
    assert any(s["tool"] == "wt_write" for s in out["steps"])


def test_react_respects_insert_cap(monkeypatch):
    from domains.agent import config, orchestrator, planner

    monkeypatch.setenv("AGENT_REACT_ENABLED", "1")
    monkeypatch.setattr(config, "MAX_REACT_INSERTS", 2)
    # 항상 read 삽입을 시도해도 상한(2)에서 멈춰야 한다.
    decision = {"action": "insert_step", "step": {"tool": "rt_read", "args": {}, "why": "또 조회", "side_effect": False}}
    with patch.object(planner, "decide_next_step", return_value=decision):
        out = orchestrator.run_loop(goal="g", steps=_read_steps())
    assert out["outcome"] == orchestrator.OUTCOME_DONE
    # 원래 1 + 삽입 2 = 3
    assert len(out["results"]) == 3


def test_keyword_fastpath_routes_search_then_create():
    from domains.agent import classify

    # 조회 + 생성/요약 동사가 함께면 LLM 없이 멀티스텝으로 인정
    assert classify._keyword_fastpath("작년 챗봇 보고서 찾아서 3주 로드맵 초안 만들어줘") is True
    assert classify._keyword_fastpath("최신 주간회의 페이지 찾아서 핵심을 요약 페이지로 정리해줘") is True
    assert classify._keyword_fastpath(
        "E-BIZ 시스템 인수인계 데이터를 찾고, 배포 관련 체크리스트를 만들어줘"
    ) is True
    # 단일 동작은 fastpath 미적용(LLM 판정으로 넘어감)
    assert classify._keyword_fastpath("안녕") is False
    assert classify._keyword_fastpath("회의 잡아줘") is False


def test_is_multi_step_uses_fastpath_without_llm(monkeypatch):
    from domains.agent import classify

    monkeypatch.setenv("AGENT_KEYWORD_FASTPATH", "1")
    with patch("domains.daily_chat.chat._yes_no_classify") as yn:
        assert classify.is_multi_step_request("보고서 찾아서 로드맵 만들어줘") is True
    yn.assert_not_called()


def test_looks_like_revision_detects_short_edit():
    from domains.agent import classify

    active = {"goal": "3주 로드맵", "steps": [{"n": 1}]}
    with patch("domains.routing.intent.is_plan_revision", side_effect=[True, False]):
        assert classify.looks_like_revision("3주가 아니라 2주로 바꿔줘", active) is True
        assert classify.looks_like_revision("작년 보고서 찾아서 로드맵 만들어줘", active) is False


def test_is_unapproved_side_effect_helper():
    from domains.agent import repair

    approved = [{"tool": "rt_read"}]
    assert repair.is_unapproved_side_effect("wt_write", approved_steps=approved) is True
    assert repair.is_unapproved_side_effect("rt_read", approved_steps=approved) is False
    # 승인된 write 는 미승인 아님
    assert repair.is_unapproved_side_effect("wt_write", approved_steps=[{"tool": "wt_write"}]) is False
