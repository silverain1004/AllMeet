"""홈 메뉴 카드·액션 단위 테스트."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def _home_buttons(card: dict[str, Any]) -> list[dict[str, Any]]:
    widgets = card["cardsV2"][0]["card"]["sections"][0]["widgets"]
    for w in widgets:
        if "buttonList" in w:
            return w["buttonList"]["buttons"]
    return []


def _button_params(button: dict[str, Any]) -> dict[str, str]:
    action = button["onClick"]["action"]
    params = action.get("parameters") or []
    return {p["key"]: p["value"] for p in params}


def test_build_home_menu_card_has_six_buttons_with_prompts():
    from domains.daily_chat.home_menu import build_home_menu_card

    card = build_home_menu_card(
        daily_prompt="점심 메뉴 추천해줘",
        info_prompt="오늘 서울 날씨 알려줘",
    )
    buttons = _home_buttons(card)
    assert len(buttons) == 6
    assert _button_params(buttons[0])["prompt"] == "점심 메뉴 추천해줘"
    assert _button_params(buttons[1])["prompt"] == "오늘 서울 날씨 알려줘"
    assert buttons[2]["onClick"]["action"]["function"] == "hm_expert"
    assert buttons[3]["onClick"]["action"]["function"] == "hm_schedule"
    assert buttons[4]["onClick"]["action"]["function"] == "hm_weekly_draft"
    assert buttons[5]["onClick"]["action"]["function"] == "hm_open_settings"


def test_reply_with_home_menu_merges_chat_and_card():
    from domains.daily_chat.home_menu import reply_with_home_menu

    chat_event = {"type": "MESSAGE", "user": {"email": "u@example.com"}, "space": {"name": "spaces/AAA"}}
    with patch("domains.daily_chat.home_menu.reply_daily_chat", return_value="안녕하세요!") as mock_reply:
        out = reply_with_home_menu("안녕", chat_event=chat_event)
    mock_reply.assert_called_once_with("안녕", chat_event=chat_event)
    assert out["text"] == "안녕하세요!"
    assert out["cardsV2"][0]["cardId"] == "hm_home_menu"


def test_reply_with_home_menu_skips_duplicate_card():
    from domains.daily_chat.home_menu import reply_with_home_menu

    existing = {
        "text": "기능 안내입니다.",
        "cardsV2": [{"cardId": "hm_home_menu", "card": {"sections": []}}],
    }
    with patch("domains.daily_chat.home_menu.reply_daily_chat", return_value=existing):
        with patch("domains.daily_chat.home_menu.build_home_menu_card") as mock_menu:
            out = reply_with_home_menu("너 뭐할수있어", chat_event=None)
    mock_menu.assert_not_called()
    assert out == existing
    assert len(out["cardsV2"]) == 1


def test_dispatch_home_menu_calls_combo():
    from main import UserIntent, _dispatch_by_intent

    payload = {"type": "MESSAGE", "user": {"email": "u@example.com"}, "space": {"name": "spaces/AAA"}}
    combo = {"text": "안녕하세요!", "cardsV2": [{"cardId": "hm_home_menu"}]}
    with patch("main.reply_with_home_menu", return_value=combo) as mock_combo:
        out = _dispatch_by_intent(UserIntent.HOME_MENU, "안녕", payload)
    mock_combo.assert_called_once_with("안녕", chat_event=payload)
    assert out is combo


def test_hm_run_daily_calls_reply_with_prompt():
    from domains.daily_chat.home_menu import handle_home_menu_action

    chat_event = {
        "type": "CARD_CLICKED",
        "user": {"email": "u@example.com"},
        "space": {"name": "spaces/AAA"},
    }
    with patch("domains.daily_chat.home_menu.reply_daily_chat", return_value="답변입니다") as mock_reply:
        out = handle_home_menu_action(
            invoked_function="hm_run_daily",
            parameters={"prompt": "점심 메뉴 추천해줘"},
            chat_event=chat_event,
        )
    assert out == {"text": "답변입니다"}
    mock_reply.assert_called_once()
    assert mock_reply.call_args[0][0] == "점심 메뉴 추천해줘"
    assert mock_reply.call_args[1]["chat_event"]["type"] == "MESSAGE"


def test_hm_weekly_draft_blocks_without_oauth():
    from domains.daily_chat.home_menu import handle_home_menu_action

    chat_event = {
        "user": {"email": "u@example.com"},
        "space": {"name": "spaces/AAA"},
    }
    with patch("domains.daily_chat.home_menu.is_oauth_linked", return_value=False):
        with patch("domains.weekly_report.handle_weekly_report_draft") as mock_draft:
            out = handle_home_menu_action(
                invoked_function="hm_weekly_draft",
                parameters={},
                chat_event=chat_event,
            )
    mock_draft.assert_not_called()
    assert out["cardsV2"][0]["cardId"] == "hm_oauth_required"


def test_hm_weekly_draft_runs_when_linked():
    from domains.daily_chat.home_menu import handle_home_menu_action

    chat_event = {"user": {"email": "u@example.com"}, "space": {"name": "spaces/AAA"}}
    draft_card = {"text": "분석 중", "cardsV2": []}
    with patch("domains.daily_chat.home_menu.is_oauth_linked", return_value=True):
        with patch(
            "domains.weekly_report.handle_weekly_report_draft",
            return_value=draft_card,
        ) as mock_draft:
            out = handle_home_menu_action(
                invoked_function="hm_weekly_draft",
                parameters={},
                chat_event=chat_event,
            )
    mock_draft.assert_called_once_with("주간보고초안", chat_event=chat_event)
    assert out is draft_card


def test_build_settings_hub_has_three_branches():
    from domains.settings.cards import build_settings_hub_card

    card = build_settings_hub_card()
    buttons = _home_buttons(card)
    functions = [b["onClick"]["action"]["function"] for b in buttons]
    assert "st_open_personal" in functions
    assert "st_open_team" in functions
    assert "st_open_rooms" in functions
    assert "hm_open_menu" in functions
