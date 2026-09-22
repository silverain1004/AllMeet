"""자연어 예약 — 스페이스가 있는 MESSAGE 는 로딩 문구를 먼저 보내고 카드는 백그라운드로."""

from __future__ import annotations

from unittest.mock import patch


def test_message_in_space_returns_loading_and_defers_card():
    from domains.schedule_management import handler

    chat_event = {"type": "MESSAGE", "space": {"name": "spaces/AAA"}, "user": {"email": "u@x.com"}}
    with patch("api.chat.loading.start_background") as bg, \
         patch.object(handler, "_build_compose_from_message") as build:
        out = handler.handle_schedule_management("내일 3시 회의 잡아줘", chat_event=chat_event)
    assert "⏳" in out["text"] and "예약 카드" in out["text"]
    bg.assert_called_once()
    assert bg.call_args.args[0] is handler._run_compose_background
    build.assert_not_called()  # 동기 경로를 타지 않음


def test_background_posts_card_to_space():
    from domains.schedule_management import handler

    card = {"text": "간편 예약 화면입니다.", "cardsV2": [{"cardId": "sm_compose_quick"}], "actionResponse": {"type": "UPDATE_MESSAGE"}}
    posted: dict = {}
    with patch.object(handler, "_build_compose_from_message", return_value=card), \
         patch("api.chat.messages.post_message_to_space", side_effect=lambda **kw: posted.update(kw)):
        handler._run_compose_background("내일 3시 회의", {"type": "MESSAGE"}, "spaces/AAA")
    assert posted["space_name"] == "spaces/AAA"
    assert posted["payload"]["cardsV2"][0]["cardId"] == "sm_compose_quick"
    assert "actionResponse" not in posted["payload"]  # 새 메시지 push 에는 UPDATE 응답 타입이 없어야 한다


def test_background_failure_posts_friendly_error():
    from domains.schedule_management import handler

    posted: dict = {}
    with patch.object(handler, "_build_compose_from_message", side_effect=RuntimeError("boom")), \
         patch("api.chat.messages.post_message_to_space", side_effect=lambda **kw: posted.update(kw)):
        handler._run_compose_background("내일 3시 회의", {"type": "MESSAGE"}, "spaces/AAA")
    assert "다시 시도" in posted["payload"]["text"]


def test_card_click_or_no_space_stays_synchronous(monkeypatch):
    from domains.schedule_management import handler

    monkeypatch.setattr(handler, "_build_compose_from_message", lambda msg, ev: {"text": "sync"})
    assert handler.handle_schedule_management("회의실 예약", chat_event={"type": "CARD_CLICKED", "space": {"name": "spaces/AAA"}}) == {"text": "sync"}
    assert handler.handle_schedule_management("회의실 예약", chat_event={"user": {"email": "u@x.com"}}) == {"text": "sync"}


def test_identity_text_mentions_model_and_maker():
    from config.settings import ALLMEET_CHAT_MODEL
    from domains.daily_chat.chat import identity_text

    text = identity_text()
    assert "All-Meet" in text and "VNTG" in text and ALLMEET_CHAT_MODEL in text
    assert "얼버무리지" in text
