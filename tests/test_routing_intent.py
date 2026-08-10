"""맥락 기반 LLM intent 라우팅 테스트."""

from __future__ import annotations

from unittest.mock import patch

import pytest


ROUTING_MATRIX = [
    ("Kafka 전문가 추천해줘", "expert_finder"),
    ("Kafka 아키텍처를 전문가처럼 쉽게 설명해줘", "daily_chat"),
    ("내일 회의실 예약해줘", "schedule_management"),
    ("내일 3시 회의실 예약해줘", "schedule_management"),
    ("지난주 회의록 문서 찾아서 요약해줘", "agent"),
    ("주간회의 팀 등록", "weekly_meeting"),
    ("팀 등록", "weekly_meeting"),
    ("주간회의 페이지 핵심 요약해줘", "agent"),
    ("ERP2팀 주간보고페이지 만들어줘", "weekly_page_create"),
    ("PC2팀 주간회의 페이지 생성해줘", "weekly_page_create"),
    ("점심 메뉴 추천해줘", "daily_chat"),
    ("E-BIZ 찾고 배포 체크리스트 만들어줘", "agent"),
    ("최신 주간보고 페이지 찾아서 요약해줘", "agent"),
    ("PC2팀 최근 주간보고 요약해줘", "agent"),
    ("회의 일정표 요약해줘", "agent"),
    ("이번주 일정 요약해줘", "agent"),
    ("다음주 누구 쉬어?", "agent"),
]


@pytest.mark.parametrize("message,expected", ROUTING_MATRIX)
def test_classify_intent_routing_matrix(message, expected):
    from domains.routing.intent import classify_intent, deterministic_intent

    with patch("domains.routing.intent._classify_label", return_value=expected) as mock_cls:
        assert classify_intent(message, "") == expected
    det = deterministic_intent(message)
    if det is not None:
        # 결정적 fast-path 로 확정 — LLM 미호출, fast-path 라벨이 곧 정답이어야 한다.
        assert det == expected
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
    assert "캘린더 조회" in criteria


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


# --- 도메인 결정적 fast-path — 실사용 실패 케이스의 운영 회귀 방지 (LLM mock 없이) ---


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Kafka 전문가 추천해줘", "expert_finder"),
        ("이 분야 담당자 찾아줘", "expert_finder"),
        ("내일 3시 회의실 예약해줘", "schedule_management"),
        ("빈 시간 찾아서 잡아줘", "schedule_management"),
        ("주간회의 팀 등록", "weekly_meeting"),
        ("팀 등록", "weekly_meeting"),
        ("주간업무 설정", "weekly_meeting"),
    ],
)
def test_domain_fastpath_routes_without_llm(message, expected):
    """실사용 실패 발화가 LLM(flash) 흔들림과 무관하게 도메인으로 라우팅되는지."""
    from domains.routing.intent import classify_intent

    with patch("domains.routing.intent._classify_label") as mock_cls:
        assert classify_intent(message, "") == expected
    mock_cls.assert_not_called()


def test_fastpath_negative_guards():
    from domains.routing.patterns import (
        looks_like_expert_finder,
        looks_like_schedule_booking,
        looks_like_weekly_meeting_setup,
    )

    # "전문가처럼 설명" 류는 expert_finder fast-path 아님 (daily_chat 의도)
    assert not looks_like_expert_finder("Kafka 아키텍처를 전문가처럼 쉽게 설명해줘")
    assert not looks_like_expert_finder("전문 용어 뜻 알려줘")
    # 문서 검색·일정표 요약은 회의실 예약 fast-path 아님
    assert not looks_like_schedule_booking("예약 기록 문서 찾아줘")
    assert not looks_like_schedule_booking("회의 일정표 요약해줘")
    # 주간보고는 weekly_meeting 설정 fast-path 아님 (agent/draft 영역)
    assert not looks_like_weekly_meeting_setup("최신 주간보고 페이지 찾아서 요약해줘")
    assert not looks_like_weekly_meeting_setup("주간보고 페이지 요약")
    # 긍정 케이스 sanity
    assert looks_like_expert_finder("Kafka 전문가 추천해줘")
    assert looks_like_schedule_booking("내일 3시 회의실 예약해줘")
    assert looks_like_weekly_meeting_setup("팀 등록")


def test_deterministic_intent_helper():
    from domains.routing.intent import deterministic_intent

    assert deterministic_intent("Kafka 전문가 추천해줘") == "expert_finder"
    assert deterministic_intent("내일 3시 회의실 예약해줘") == "schedule_management"
    assert deterministic_intent("팀 등록") == "weekly_meeting"
    assert deterministic_intent("최신 주간보고 페이지 찾아서 요약해줘") == "agent"
    assert deterministic_intent("설정") == "settings"
    assert deterministic_intent("안녕") == "home_menu"
    # 개인 일정/휴가 조회는 agent (주어 없으면 내 캘린더)
    assert deterministic_intent("이번주 일정 요약해줘") == "agent"
    assert deterministic_intent("다음주 누구 쉬어?") == "agent"
    # 애매한 발화는 None → LLM(pro) 폴백
    assert deterministic_intent("E-BIZ 시스템 관련") is None
    assert deterministic_intent("점심 메뉴 추천해줘") is None


def test_recovery_cta_wraps_actionable_daily_chat():
    from domains.agent.landing import wrap_daily_chat_with_recovery

    out = wrap_daily_chat_with_recovery(
        "죄송하지만 예약은 직접 할 수 없어요.", user_message="회의 일정 요약해줘"
    )
    assert isinstance(out, dict)
    cards = out.get("cardsV2") or []
    assert any(c.get("cardId") == "ag_cta" for c in cards)


def test_recovery_cta_skips_casual_chat():
    from domains.agent.landing import wrap_daily_chat_with_recovery

    base = "안녕하세요! 무엇을 도와드릴까요?"
    assert wrap_daily_chat_with_recovery(base, user_message="오늘 기분 어때?") == base
