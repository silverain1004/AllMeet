"""맥락 기반 LLM intent 라우팅 테스트."""

from __future__ import annotations

from unittest.mock import patch

import pytest


ROUTING_MATRIX = [
    ("Kafka 전문가 추천해줘", "expert_finder"),
    ("Kafka 아키텍처를 전문가처럼 쉽게 설명해줘", "daily_chat"),
    ("내일 회의실 예약해줘", "schedule_management"),
    ("지난주 회의록 문서 찾아서 요약해줘", "agent"),
    ("주간회의 팀 등록", "weekly_meeting"),
    ("주간회의 페이지 핵심 요약해줘", "agent"),
    ("점심 메뉴 추천해줘", "daily_chat"),
    ("E-BIZ 찾고 배포 체크리스트 만들어줘", "agent"),
    ("최신 주간보고 페이지 찾아서 요약해줘", "agent"),
    ("PC2팀 최근 주간보고 요약해줘", "agent"),
    ("회의 일정표 요약해줘", "daily_chat"),
]


@pytest.mark.parametrize("message,expected", ROUTING_MATRIX)
def test_classify_intent_routing_matrix(message, expected):
    from domains.routing.intent import classify_intent
    from domains.routing.patterns import looks_like_agent_task

    with patch("domains.routing.intent._classify_label", return_value=expected) as mock_cls:
        assert classify_intent(message, "") == expected
    if expected == "agent" and looks_like_agent_task(message):
        mock_cls.assert_not_called()
    else:
        mock_cls.assert_called_once()


def test_weekly_report_summarize_without_find_verb_is_agent():
    from domains.routing.intent import classify_intent
    from domains.routing.patterns import looks_like_agent_task

    msg = "PC2팀 최근 주간보고 요약해줘"
    assert looks_like_agent_task(msg)
    with patch("domains.routing.intent._classify_label") as mock_cls:
        assert classify_intent(msg, "") == "agent"
    mock_cls.assert_not_called()


def test_weekly_report_summarize_advice_stays_daily_chat():
    from domains.routing.patterns import looks_like_agent_task

    assert not looks_like_agent_task("주간보고 요약하는 방법 알려줘")
    assert not looks_like_agent_task("주간보고 내용 알려줘")
    """실사용 실패 4건 중 agent·home fast-path (LLM mock 없음)."""
    from domains.routing.intent import classify_intent

    assert classify_intent("최신 주간보고 페이지 찾아서 요약해줘", "") == "agent"
    assert classify_intent("PC2팀의 주간보고 confluence 최신 페이지 요약해줘", "") == "agent"
    assert classify_intent("PC2팀 최근 주간보고 요약해줘", "") == "agent"
    assert classify_intent("안녕", "") == "home_menu"
    with patch("domains.routing.intent._classify_label", return_value="daily_chat"):
        assert classify_intent("Kafka를 전문가처럼 설명해줘", "") == "daily_chat"


def test_exact_home_greeting_fastpath_even_with_context():
    from domains.routing.intent import classify_intent

    ctx = "\n[최근 대화]\n사용자: 회의 잡아줘\n"
    with patch("domains.routing.intent._classify_label") as mock_cls:
        assert classify_intent("안녕", ctx) == "home_menu"
    mock_cls.assert_not_called()


def test_home_capability_question_skipped_with_context():
    from domains.routing.intent import classify_intent

    ctx = "\n[최근 대화]\n사용자: 회의 잡아줘\n"
    with patch("domains.routing.intent._classify_label", return_value="daily_chat"):
        assert classify_intent("너 뭐 할 수 있어", ctx) == "daily_chat"


def test_main_routes_hello_to_home_menu():
    from main import hello_http

    payload = {
        "type": "MESSAGE",
        "user": {"email": "u@example.com"},
        "space": {"name": "spaces/AAA"},
        "message": {"text": "안녕"},
    }

    class _Req:
        method = "POST"
        path = "/"

        def get_json(self, silent=True):
            return payload

    combo = {"text": "안녕하세요!", "cardsV2": [{"cardId": "hm_home_menu"}]}
    with patch("main.load_ctx_block", return_value=""), patch(
        "main.agent_store.find_active_plan",
        return_value=None,
    ), patch("main.reply_with_home_menu", return_value=combo) as home:
        body, status, _ = hello_http(_Req())
    home.assert_called_once()
    assert status == 200
    assert "hm_home_menu" in body


def test_fastpath_agent_without_llm():
    from domains.routing.intent import classify_intent

    with patch("domains.routing.intent._classify_label") as mock_cls:
        assert classify_intent("최신 주간보고 페이지 찾아서 요약해줘", "") == "agent"
    mock_cls.assert_not_called()


def test_daily_chat_does_not_auto_open_home_menu():
    from domains.daily_chat.chat import reply_daily_chat

    with patch("domains.daily_chat.chat._needs_web_search", return_value=False), patch(
        "domains.daily_chat.home_menu.build_home_menu_card"
    ) as home, patch("domains.daily_chat.chat._get_generative_model") as mock_model:
        mock_model.return_value.generate_content.return_value.text = "답변"
        reply_daily_chat("너 뭐할수있어", chat_event=None)
    home.assert_not_called()


def test_classify_weekly_report_page_search_is_agent_not_meeting():
    from domains.routing.intent import classify_intent

    msg = "최신 주간보고 페이지 찾아서 요약해줘"
    assert classify_intent(msg, "") == "agent"
    assert classify_intent(msg, "") != "weekly_meeting"


def test_explicit_fastpath_weekly_report_draft():
    from domains.routing.intent import classify_intent

    assert classify_intent("주간보고초안", "") == "weekly_report_draft"
    assert classify_intent("주간 보고 초안", "") == "weekly_report_draft"


def test_explicit_fastpath_settings():
    from domains.routing.intent import classify_intent

    assert classify_intent("설정", "") == "settings"


def test_classify_passes_active_plan_to_llm():
    from domains.routing.intent import classify_intent

    active = {"goal": "로드맵 작성", "steps": [{"n": 1, "tool": "search_confluence", "why": "검색"}]}
    with patch("domains.routing.intent._classify_label", return_value="daily_chat") as mock_cls:
        classify_intent("다른 질문", "ctx", active_plan=active)
    extra = mock_cls.call_args.kwargs.get("extra_block") or ""
    assert "로드맵" in extra


def test_match_user_intent_forwards_active_plan():
    from main import UserIntent, match_user_intent

    active = {"goal": "test", "steps": []}
    with patch("main.classify_intent", return_value="daily_chat") as mock_cls:
        assert match_user_intent("hi", "ctx", active_plan=active) == UserIntent.DAILY_CHAT
    mock_cls.assert_called_once_with("hi", "ctx", active_plan=active)


def test_is_plan_revision_with_pending_plan():
    from domains.routing.intent import is_plan_revision

    active = {"goal": "3주 로드맵", "steps": [{"n": 1, "tool": "search_confluence", "why": "검색"}]}
    with patch("domains.routing.intent._yes_no_revision", return_value=True):
        assert is_plan_revision("2주로 바꿔줘", active, "") is True


def test_is_plan_revision_rejects_new_multi_step():
    from domains.routing.intent import is_plan_revision

    active = {"goal": "로드맵", "steps": []}
    with patch("domains.routing.intent._yes_no_revision", return_value=False):
        assert is_plan_revision("보고서 찾아서 로드맵 만들어줘", active, "") is False


def test_main_routes_by_llm_intent():
    from main import hello_http

    payload = {
        "type": "MESSAGE",
        "user": {"email": "u@example.com"},
        "space": {"name": "spaces/AAA"},
        "message": {"text": "최신 주간보고 페이지 찾아서 요약해줘"},
    }

    class _Req:
        method = "POST"
        path = "/"

        def get_json(self, silent=True):
            return payload

    with patch("main.load_ctx_block", return_value=""), patch(
        "main.agent_store.find_active_plan",
        return_value=None,
    ), patch(
        "main.classify_intent",
        return_value="agent",
    ), patch("main.handle_agent_request", return_value={"text": "⏳ 계획"}) as agent:
        body, status, _ = hello_http(_Req())
    agent.assert_called_once()
    assert status == 200
    assert "계획" in body


def test_main_schedule_keyword_doc_search_routes_agent_not_schedule():
    """'회의록 문서 찾아서' — schedule_management 오탐 방지."""
    from main import hello_http

    payload = {
        "type": "MESSAGE",
        "user": {"email": "u@example.com"},
        "space": {"name": "spaces/AAA"},
        "message": {"text": "지난주 회의록 문서 찾아서 요약해줘"},
    }

    class _Req:
        method = "POST"
        path = "/"

        def get_json(self, silent=True):
            return payload

    with patch("main.load_ctx_block", return_value=""), patch(
        "main.agent_store.find_active_plan",
        return_value=None,
    ), patch("main.classify_intent", return_value="agent"), patch(
        "main.handle_agent_request",
        return_value={"text": "⏳"},
    ) as agent, patch("main.handle_schedule_management") as sched:
        hello_http(_Req())
    agent.assert_called_once()
    sched.assert_not_called()


def test_intent_criteria_contains_disambiguation_examples():
    from domains.routing import intent as routing_intent

    criteria = routing_intent._INTENT_CRITERIA
    assert "expert_finder" in criteria
    assert "schedule_management" in criteria
    assert "전문가처럼" in criteria
    assert "회의 일정표" in criteria


def test_load_ctx_block_from_message():
    from domains.routing.context import load_ctx_block

    chat_event = {
        "type": "MESSAGE",
        "user": {"displayName": "U", "email": "u@x.com"},
        "space": {"name": "spaces/AAA"},
    }
    with patch(
        "domains.routing.context.conv.list_recent_messages",
        return_value=[{"role": "user", "content": "안녕"}],
    ):
        block = load_ctx_block(chat_event)
    assert "안녕" in block
