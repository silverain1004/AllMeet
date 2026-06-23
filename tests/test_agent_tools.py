"""멀티스텝 에이전트 — 신규 범용 도구(생성/본문읽기) + 설계 근거 노출 테스트.

LLM·외부 API 없이 동작하도록 원본 함수/generate_text 를 monkeypatch 한다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from domains.agent import registry
from domains.agent.tools import get_tool


# ---------------------------------------------------------------------------
# 등록 여부 + side_effect
# ---------------------------------------------------------------------------


def test_new_tools_registered_with_expected_side_effect():
    assert get_tool("get_confluence_page_body").side_effect is False
    assert get_tool("get_email_body").side_effect is False
    # 생성 도구는 외부 변경이 없으므로 승인 불필요(side_effect=False)
    assert get_tool("generate_content").side_effect is False


# ---------------------------------------------------------------------------
# generate_content
# ---------------------------------------------------------------------------


def test_generate_content_missing_instruction():
    out = registry._run_generate_content(source="x")
    assert out["ok"] is False
    assert out["error_kind"] == "missing_args"


def test_generate_content_returns_content_and_html():
    text = "- 항목 1\n- 항목 2\n일반 문단"
    with patch("domains.agent._llm.generate_text", return_value=text):
        out = registry._run_generate_content(
            instruction="온보딩 체크리스트", source={"body": "참조"}, content_type="checklist"
        )
    assert out["ok"] is True
    assert out["content"] == text
    assert "<ul>" in out["content_html"]
    assert "<li>항목 1</li>" in out["content_html"]
    assert "<p>일반 문단</p>" in out["content_html"]


def test_generate_content_generation_failure():
    with patch("domains.agent._llm.generate_text", return_value=""):
        out = registry._run_generate_content(instruction="뭐든")
    assert out["ok"] is False
    assert out["error_kind"] == "generation_failed"


# ---------------------------------------------------------------------------
# _to_html / _stringify_source 유닛
# ---------------------------------------------------------------------------


def test_to_html_handles_bullets_and_numbered():
    html = registry._to_html("1. 첫째\n2. 둘째\n\n끝 문장")
    assert html.count("<li>") == 2
    assert "<p>끝 문장</p>" in html


def test_stringify_source_drops_envelope_keys():
    s = registry._stringify_source({"ok": True, "error_kind": "", "body": "본문내용"})
    assert "본문내용" in s
    assert "ok:" not in s


# ---------------------------------------------------------------------------
# 본문 읽기 도구
# ---------------------------------------------------------------------------


def test_get_confluence_page_body_ok():
    with patch("api.confluence.pages.get_page_body", return_value="페이지 본문"):
        out = registry._run_get_confluence_page_body(page_id="123")
    assert out == {"ok": True, "body": "페이지 본문"}


def test_get_confluence_page_body_missing_id():
    out = registry._run_get_confluence_page_body()
    assert out["error_kind"] == "missing_args"


def test_get_confluence_page_body_empty_is_not_found():
    with patch("api.confluence.pages.get_page_body", return_value=""):
        out = registry._run_get_confluence_page_body(page_id="123")
    assert out["error_kind"] == "not_found"


def test_get_email_body_requires_user_email():
    out = registry._run_get_email_body(message_id="m1")
    assert out["error_kind"] == "missing_args"


def test_get_email_body_ok():
    with patch("api.gmail.messages.get_message_body", return_value="메일 본문"):
        out = registry._run_get_email_body(message_id="m1", user_email="u@x.com")
    assert out == {"ok": True, "body": "메일 본문"}


# ---------------------------------------------------------------------------
# 플래너 plan_rationale 정규화
# ---------------------------------------------------------------------------


def test_normalize_plan_carries_rationale():
    from domains.agent.planner import _normalize_plan

    raw = {
        "goal": "g",
        "plan_rationale": "작년 보고서를 참조해 로드맵을 만든다",
        "steps": [{"n": 1, "tool": "search_confluence", "args": {"query": "x"}}],
    }
    plan = _normalize_plan(raw)
    assert plan["plan_rationale"] == "작년 보고서를 참조해 로드맵을 만든다"


# ---------------------------------------------------------------------------
# 카드 — 설계 노트 + 단계 뱃지
# ---------------------------------------------------------------------------


def _all_text(card: dict[str, Any]) -> str:
    widgets = card["cardsV2"][0]["card"]["sections"][0]["widgets"]
    return " ".join(
        w["textParagraph"]["text"] for w in widgets if "textParagraph" in w
    )


def test_card_renders_rationale_and_step_badges():
    from domains.agent.cards import build_plan_approval_card

    steps = [
        {"n": 1, "tool": "search_confluence", "why": "보고서 검색", "side_effect": False},
        {"n": 2, "tool": "generate_content", "why": "로드맵 작성", "side_effect": False},
        {"n": 3, "tool": "create_confluence_page", "why": "문서화", "side_effect": True},
    ]
    card = build_plan_approval_card(
        plan_id="P1", goal="목표", steps=steps, rationale="이래서 이렇게 설계함"
    )
    text = _all_text(card)
    assert "설계 노트" in text
    assert "이래서 이렇게 설계함" in text
    assert "[조회]" in text
    assert "[생성]" in text
    assert "[쓰기·승인]" in text
    assert "search_confluence" in text


def test_card_without_rationale_has_no_design_note():
    from domains.agent.cards import build_plan_approval_card

    card = build_plan_approval_card(
        plan_id="P1",
        goal="목표",
        steps=[{"n": 1, "tool": "find_free_slots", "why": "빈 시간", "side_effect": False}],
    )
    assert "설계 노트" not in _all_text(card)


# ---------------------------------------------------------------------------
# dry-run 통합 — search → body → generate → create 체인 ($ref 연결)
# ---------------------------------------------------------------------------


def test_dry_run_chain_search_generate_create(monkeypatch):
    from api.confluence.pages import ListPagesResult
    from domains.agent import orchestrator

    monkeypatch.setenv("AGENT_DRY_RUN", "1")

    def _fake_list_pages_by_query(**_: Any) -> ListPagesResult:
        return ListPagesResult(ok=True, pages=[{"id": "P-100", "title": "작년 보고서"}])

    steps = [
        {"n": 1, "tool": "search_confluence", "args": {"query": "챗봇 보고서"}, "side_effect": False},
        {
            "n": 2,
            "tool": "get_confluence_page_body",
            "args": {"page_id": {"$ref": "1.pages.0.id"}},
            "side_effect": False,
        },
        {
            "n": 3,
            "tool": "generate_content",
            "args": {"instruction": "3주 로드맵", "source": {"$ref": "2.body"}, "content_type": "roadmap"},
            "side_effect": False,
        },
        {
            "n": 4,
            "tool": "create_confluence_page",
            "args": {
                "title": "AI 프로젝트 로드맵",
                "html_content": {"$ref": "3.content_html"},
                "parent_id": "PARENT",
                "space_key": "PJ",
            },
            "side_effect": True,
        },
    ]

    with patch("api.confluence.pages.list_pages_by_query", _fake_list_pages_by_query), \
         patch("api.confluence.pages.get_page_body", return_value="작년 보고서 본문 텍스트"), \
         patch("domains.agent._llm.generate_text", return_value="- 1주차\n- 2주차\n- 3주차"):
        out = orchestrator.run_loop(goal="AI 프로젝트", steps=steps)

    assert out["outcome"] == orchestrator.OUTCOME_DONE
    # 본문이 $ref 로 generate_content 에 전달되어 content_html 생성됨
    assert "<li>1주차</li>" in out["results"][3]["content_html"]
    # 마지막 쓰기 단계는 dry-run preview 로 통과
    assert out["results"][4]["dry_run"] is True
    assert out["results"][4]["preview"]["title"] == "AI 프로젝트 로드맵"
