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


def test_ag_delegate_routes_to_handle_agent_request():
    from domains.agent import actions

    chat_event = {"type": "CARD_CLICKED", "space": {"name": "spaces/AAA"}}
    with patch.object(actions, "handle_agent_request", return_value={"text": "계획 카드"}) as mock_req:
        out = actions.handle_agent_action(
            invoked_function="ag_delegate",
            parameters={"user_message": "회의 잡고 페이지도 만들어줘", "intent": "schedule_management"},
            chat_event=chat_event,
        )
    mock_req.assert_called_once_with(
        "회의 잡고 페이지도 만들어줘",
        chat_event=chat_event,
        ctx_block="",
    )
    assert out == {"text": "계획 카드"}


def test_ag_delegate_empty_message_asks_again():
    from domains.agent import actions

    out = actions.handle_agent_action(
        invoked_function="ag_delegate",
        parameters={"user_message": ""},
        chat_event={"space": {"name": "spaces/AAA"}},
    )
    assert "다시" in out["text"]
