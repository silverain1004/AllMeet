"""하이브리드 액션 랜딩 — CTA 래핑 / 미리보기 / ag_delegate 위임 테스트."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


def _find_cta(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    for c in cards:
        if c.get("cardId") == "ag_cta":
            return c
    return None


def _delegate_button(cta: dict[str, Any]) -> dict[str, Any]:
    widgets = cta["card"]["sections"][0]["widgets"]
    for w in widgets:
        if "buttonList" in w:
            return w["buttonList"]["buttons"][0]
    raise AssertionError("delegate 버튼 없음")


def test_wrap_appends_cta_card_and_carries_user_message():
    from domains.agent.landing import wrap_with_agent_cta

    base = {"text": "간편 예약 화면입니다.", "cardsV2": [{"cardId": "sm_compose", "card": {}}]}
    out = wrap_with_agent_cta(base, user_message="회의 잡아줘", intent_value="schedule_management")

    assert len(out["cardsV2"]) == 2  # 기존 + CTA
    cta = _find_cta(out["cardsV2"])
    assert cta is not None
    btn = _delegate_button(cta)
    assert btn["onClick"]["action"]["function"] == "ag_delegate"
    params = {p["key"]: p["value"] for p in btn["onClick"]["action"]["parameters"]}
    assert params["user_message"] == "회의 잡아줘"
    assert params["intent"] == "schedule_management"
    # 기존 카드는 보존
    assert out["cardsV2"][0]["cardId"] == "sm_compose"


def test_wrap_creates_cardsV2_when_absent():
    from domains.agent.landing import wrap_with_agent_cta

    base = {"text": "전문가 찾는 중"}
    out = wrap_with_agent_cta(base, user_message="kafka 전문가", intent_value="expert_finder")
    assert isinstance(out["cardsV2"], list)
    assert _find_cta(out["cardsV2"]) is not None


def test_wrap_passes_through_non_dict_and_non_action():
    from domains.agent.landing import wrap_with_agent_cta

    # 문자열 reply 는 그대로
    assert wrap_with_agent_cta("그냥 답변", user_message="x", intent_value="schedule_management") == "그냥 답변"
    # 비액션 intent 는 그대로
    base = {"text": "hi", "cardsV2": [{"cardId": "z"}]}
    out = wrap_with_agent_cta(base, user_message="x", intent_value="daily_chat")
    assert out == base


def test_cta_card_preview_contains_outline_steps():
    from domains.agent.cards import build_agent_cta_card

    cta = build_agent_cta_card(
        user_message="회의",
        intent_value="schedule_management",
        outline=["빈 시간 확인", "회의 생성", "참석자 초대/알림"],
    )
    text = cta["card"]["sections"][0]["widgets"][0]["textParagraph"]["text"]
    assert "빈 시간 확인" in text
    assert "회의 생성" in text
    assert "참석자 초대/알림" in text


def test_ag_delegate_auto_runs_in_background_with_space():
    """'AI에게 맡기기'는 승인 카드(handle_agent_request) 대신 자동 승인·실행(백그라운드)."""
    from domains.agent import actions

    chat_event = {"type": "CARD_CLICKED", "space": {"name": "spaces/AAA"}}
    with patch.object(actions, "agent_ui_enabled", return_value=True), patch.object(
        actions, "handle_agent_request"
    ) as mock_req, patch(
        "api.chat.loading.start_background"
    ) as mock_bg, patch("api.chat.loading.loading_text", return_value={"text": "⏳"}):
        out = actions.handle_agent_action(
            invoked_function="ag_delegate",
            parameters={"user_message": "회의 잡고 페이지도 만들어줘", "intent": "schedule_management"},
            chat_event=chat_event,
        )
    mock_bg.assert_called_once()
    assert mock_bg.call_args.args[0] is actions._run_delegate_background
    mock_req.assert_not_called()  # 승인 카드 경로를 타지 않음
    assert out == {"text": "⏳"}


def test_ag_delegate_without_space_falls_back_to_request():
    from domains.agent import actions

    chat_event = {"type": "CARD_CLICKED"}  # space 없음 → 동기 폴백
    with patch.object(actions, "agent_ui_enabled", return_value=True), patch.object(
        actions, "handle_agent_request", return_value={"text": "계획 카드"}
    ) as mock_req:
        out = actions.handle_agent_action(
            invoked_function="ag_delegate",
            parameters={"user_message": "회의 잡아줘"},
            chat_event=chat_event,
        )
    mock_req.assert_called_once()
    assert out == {"text": "계획 카드"}


def test_agent_ui_disabled_by_default():
    from domains.agent.config import agent_ui_enabled

    assert agent_ui_enabled() is False


def test_agent_ui_enabled_via_env(monkeypatch):
    from domains.agent.config import agent_ui_enabled

    monkeypatch.setenv("AGENT_UI_ENABLED", "true")
    assert agent_ui_enabled() is True


def test_cta_button_grayed_out_when_disabled():
    from domains.agent.cards import build_agent_cta_card

    cta = build_agent_cta_card(
        user_message="회의 잡아줘",
        intent_value="schedule_management",
        outline=["a", "b"],
        label="🤖 AI에게 맡기기",
    )
    btn = _delegate_button(cta)
    assert btn.get("disabled") is True
    assert "준비 중" in btn["text"]


def test_cta_button_active_when_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_UI_ENABLED", "true")
    from domains.agent.cards import build_agent_cta_card

    cta = build_agent_cta_card(
        user_message="회의 잡아줘",
        intent_value="schedule_management",
        outline=["a", "b"],
        label="🤖 AI에게 맡기기",
    )
    btn = _delegate_button(cta)
    assert not btn.get("disabled")
    assert btn["text"] == "🤖 AI에게 맡기기"


def test_handle_agent_request_disabled_by_default_returns_notice():
    from domains.agent.actions import handle_agent_request

    out = handle_agent_request("복잡한 요청", chat_event={"user": {"email": "u@x.com"}})
    assert "준비 중" in out["text"]


def test_handle_agent_action_disabled_blocks_approve():
    from domains.agent import actions

    out = actions.handle_agent_action(
        invoked_function="ag_approve",
        parameters={"plan_id": "PID-1"},
        chat_event={"space": {"name": "spaces/AAA"}},
    )
    assert "준비 중" in out["text"]


def test_ag_delegate_empty_message_asks_again():
    from domains.agent import actions

    with patch.object(actions, "agent_ui_enabled", return_value=True):
        out = actions.handle_agent_action(
            invoked_function="ag_delegate",
            parameters={"user_message": ""},
            chat_event={"space": {"name": "spaces/AAA"}},
        )
    assert "다시" in out["text"]
