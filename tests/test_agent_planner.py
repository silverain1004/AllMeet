"""멀티스텝 에이전트 — 계획 정규화 / 도구 카탈로그 / 승인 카드 테스트."""

from __future__ import annotations

from typing import Any


def _buttons(card: dict[str, Any]) -> list[dict[str, Any]]:
    widgets = card["cardsV2"][0]["card"]["sections"][0]["widgets"]
    for w in widgets:
        if "buttonList" in w:
            return w["buttonList"]["buttons"]
    return []


def test_registry_has_read_and_write_tools_with_correct_side_effect():
    from domains.agent.tools import get_tool

    assert get_tool("find_free_slots").side_effect is False
    assert get_tool("search_emails").side_effect is False
    assert get_tool("create_meeting").side_effect is True
    assert get_tool("send_chat_message").side_effect is True


def test_normalize_plan_drops_unknown_tools_and_fixes_side_effect():
    from domains.agent.planner import _normalize_plan

    raw = {
        "goal": "테스트",
        "steps": [
            {"n": 1, "tool": "find_free_slots", "args": {"calendar_ids": ["a"]}, "side_effect": True},
            {"n": 2, "tool": "made_up_tool", "args": {}},
            {"n": 3, "tool": "create_meeting", "args": {}, "side_effect": False},
        ],
        "needs_confirmation": True,
    }
    plan = _normalize_plan(raw)
    tools = [s["tool"] for s in plan["steps"]]
    assert "made_up_tool" not in tools
    assert len(plan["steps"]) == 2
    # side_effect 는 레지스트리가 진실: find_free_slots=False, create_meeting=True
    by_tool = {s["tool"]: s["side_effect"] for s in plan["steps"]}
    assert by_tool["find_free_slots"] is False
    assert by_tool["create_meeting"] is True


def test_create_plan_returns_ask_user_when_llm_empty():
    from unittest.mock import patch

    from domains.agent import planner

    with patch.object(planner, "generate_json", return_value={}):
        plan = planner.create_plan("뭐 좀 해줘")
    assert plan["steps"] == []
    assert plan["ask_user"]


    with patch.object(planner, "generate_json", return_value={}):
        plan = planner.create_plan("뭐 좀 해줘")
    assert plan["steps"] == []
    assert plan["ask_user"]


def test_revise_plan_updates_goal_and_runs_critique():
    from unittest.mock import patch

    from domains.agent import planner

    existing = {
        "goal": "3주 로드맵",
        "plan_rationale": "r",
        "critique_note": "",
        "steps": [
            {
                "n": 1,
                "tool": "search_confluence",
                "args": {"query": "챗봇 보고서"},
                "why": "검색",
                "side_effect": False,
            }
        ],
    }
    revised_raw = {
        "goal": "2주 로드맵",
        "plan_rationale": "기간 변경",
        "steps": [
            {
                "n": 1,
                "tool": "search_confluence",
                "args": {"query": "챗봇 보고서"},
                "why": "검색",
            },
            {
                "n": 2,
                "tool": "generate_content",
                "args": {
                    "instruction": "2주 로드맵",
                    "require_source": True,
                    "source": {"$ref": "1.body"},
                },
                "why": "생성",
            },
        ],
    }
    critique_out = {"ok": True, "note": "2주로 반영", "revised_steps": []}
    with patch.object(planner, "generate_json", side_effect=[revised_raw, critique_out]):
        out = planner.revise_plan(existing, "2주로 바꿔줘", user_email="u@x.com")
    assert out["goal"] == "2주 로드맵"
    assert out["critique_note"] == "2주로 반영"
    assert len(out["steps"]) == 2


def test_build_plan_approval_card_has_three_action_buttons():
    from domains.agent.cards import build_plan_approval_card

    card = build_plan_approval_card(
        plan_id="PID123",
        goal="회의 잡고 페이지 만들기",
        steps=[{"n": 1, "tool": "find_free_slots", "why": "빈 시간", "side_effect": False}],
    )
    buttons = _buttons(card)
    fns = [b["onClick"]["action"]["function"] for b in buttons]
    assert fns == ["ag_approve", "ag_edit", "ag_reject"]
    # 모든 버튼이 plan_id 를 실어야 한다.
    for b in buttons:
        params = {p["key"]: p["value"] for p in b["onClick"]["action"]["parameters"]}
        assert params["plan_id"] == "PID123"
