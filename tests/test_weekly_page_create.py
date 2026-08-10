"""자연어 주간회의 페이지 수동 생성 — 라우팅·팀 해석·핸들러."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from domains.routing.patterns import looks_like_weekly_page_create


# ---------------------------------------------------------------------------
# 패턴
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg,expected",
    [
        ("ERP2팀 주간보고페이지 만들어줘", True),
        ("PC2팀 주간회의 페이지 생성해줘", True),
        ("주간회의 페이지 작성해줘", True),
        ("주간페이지 만들어줘", True),
        ("PC2팀 최근 주간보고 요약해줘", False),
        ("최신 주간보고 페이지 찾아서 요약해줘", False),
        ("주간보고초안", False),
        ("주간회의 팀 등록", False),
        ("주간회의 페이지 핵심 요약해줘", False),
        ("점심 메뉴 추천해줘", False),
    ],
)
def test_looks_like_weekly_page_create(msg, expected):
    assert looks_like_weekly_page_create(msg) is expected


# ---------------------------------------------------------------------------
# Intent 라우팅
# ---------------------------------------------------------------------------

PAGE_CREATE_ROUTING = [
    ("ERP2팀 주간보고페이지 만들어줘", "weekly_page_create"),
    ("PC2팀 주간회의 페이지 생성해줘", "weekly_page_create"),
    ("PC2팀 최근 주간보고 요약해줘", "agent"),
    ("주간보고초안", "weekly_report_draft"),
    ("주간회의 팀 등록", "weekly_meeting"),
]


@pytest.mark.parametrize("message,expected", PAGE_CREATE_ROUTING)
def test_weekly_page_create_intent_routing(message, expected):
    from domains.routing.intent import classify_intent, deterministic_intent

    with patch("domains.routing.intent._classify_label", return_value=expected) as mock_cls:
        assert classify_intent(message, "") == expected
    det = deterministic_intent(message)
    if det is not None:
        assert det == expected
        mock_cls.assert_not_called()
    else:
        mock_cls.assert_called_once()


def test_page_create_beats_agent_pattern():
    """'페이지 만들어줘'가 agent(페이지+만들)로 새지 않는지."""
    from domains.routing.intent import classify_intent

    msg = "ERP2팀 주간보고페이지 만들어줘"
    with patch("domains.routing.intent._classify_label") as mock_cls:
        assert classify_intent(msg, "") == "weekly_page_create"
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 팀 해석
# ---------------------------------------------------------------------------

_TEAMS = [
    {"id": "PC2", "name": "PC2팀"},
    {"id": "ERP2", "name": "ERP2팀"},
    {"id": "MES2", "name": "MES2팀"},
]


def test_resolve_team_by_registered_name():
    from domains.weekly_meeting.page_create_chat import resolve_team_id_from_message

    assert (
        resolve_team_id_from_message("ERP2팀 주간보고페이지 만들어줘", teams=_TEAMS)
        == "ERP2"
    )


def test_resolve_team_by_id_token():
    from domains.weekly_meeting.page_create_chat import resolve_team_id_from_message

    assert (
        resolve_team_id_from_message("erp2 주간회의 페이지 생성해줘", teams=_TEAMS)
        == "ERP2"
    )


def test_resolve_team_prefers_longer_name():
    from domains.weekly_meeting.page_create_chat import resolve_team_id_from_message

    teams = [
        {"id": "ERP2", "name": "ERP2팀"},
        {"id": "MERP2", "name": "제조ERP2팀"},
    ]
    assert (
        resolve_team_id_from_message("제조ERP2팀 주간페이지 만들어줘", teams=teams)
        == "MERP2"
    )


def test_resolve_team_email_fallback():
    from domains.weekly_meeting.page_create_chat import resolve_team_id_from_message

    with patch(
        "domains.weekly_meeting.page_create_chat.find_team_by_email",
        return_value="PC2",
    ):
        assert (
            resolve_team_id_from_message(
                "주간회의 페이지 만들어줘",
                "me@example.com",
                teams=_TEAMS,
            )
            == "PC2"
        )


def test_resolve_team_fails_when_unknown():
    from domains.weekly_meeting.page_create_chat import resolve_team_id_from_message

    with patch(
        "domains.weekly_meeting.page_create_chat.find_team_by_email",
        return_value=None,
    ), patch(
        "domains.weekly_meeting.page_create_chat.get_team_config",
        return_value=None,
    ):
        assert (
            resolve_team_id_from_message(
                "주간회의 페이지 만들어줘",
                "nobody@example.com",
                teams=_TEAMS,
            )
            is None
        )


# ---------------------------------------------------------------------------
# 핸들러
# ---------------------------------------------------------------------------

def test_handle_weekly_page_create_starts_job():
    from domains.weekly_meeting.page_create_chat import handle_weekly_page_create

    chat_event = {
        "space": {"name": "spaces/AAA"},
        "user": {"email": "u@example.com"},
    }
    mock_thread = MagicMock()

    with patch(
        "domains.weekly_meeting.page_create_chat.get_team_list",
        return_value=_TEAMS,
    ), patch(
        "domains.weekly_meeting.page_create_chat.get_team_config",
        return_value={"team_name": "ERP2팀"},
    ), patch(
        "domains.weekly_meeting.page_create_chat.threading.Thread",
        return_value=mock_thread,
    ) as thread_cls:
        reply = handle_weekly_page_create(
            "ERP2팀 주간보고페이지 만들어줘",
            chat_event=chat_event,
        )

    assert "text" in reply
    assert "ERP2팀" in reply["text"]
    assert "생성 중" in reply["text"]
    thread_cls.assert_called_once()
    args, kwargs = thread_cls.call_args
    assert kwargs.get("args") == ("spaces/AAA", "ERP2", "ERP2팀") or (
        thread_cls.call_args.kwargs.get("args") == ("spaces/AAA", "ERP2", "ERP2팀")
    )
    mock_thread.start.assert_called_once()


def test_handle_weekly_page_create_asks_team_when_unresolved():
    from domains.weekly_meeting.page_create_chat import handle_weekly_page_create

    chat_event = {
        "space": {"name": "spaces/AAA"},
        "user": {"email": "nobody@example.com"},
    }
    with patch(
        "domains.weekly_meeting.page_create_chat.get_team_list",
        return_value=_TEAMS,
    ), patch(
        "domains.weekly_meeting.page_create_chat.resolve_team_id_from_message",
        return_value=None,
    ), patch(
        "domains.weekly_meeting.page_create_chat.threading.Thread",
    ) as thread_cls:
        reply = handle_weekly_page_create(
            "주간회의 페이지 만들어줘",
            chat_event=chat_event,
        )

    assert "어느 팀" in reply["text"]
    thread_cls.assert_not_called()


def test_run_page_create_background_pushes_result():
    from domains.weekly_meeting.page_create_chat import _run_page_create_background

    with patch(
        "domains.weekly_meeting.page_creator.run_weekly_page_job",
        return_value="페이지 생성 완료: title (page_id=1)",
    ), patch(
        "domains.weekly_meeting.page_create_chat.post_message_to_space",
        return_value=True,
    ) as post:
        _run_page_create_background("spaces/AAA", "ERP2", "ERP2팀")

    post.assert_called_once()
    payload = post.call_args.kwargs["payload"]
    assert "cardsV2" in payload
    assert "생성 완료" in payload["cardsV2"][0]["card"]["header"]["subtitle"]


def test_run_page_create_background_pushes_failure():
    from domains.weekly_meeting.page_create_chat import _run_page_create_background

    with patch(
        "domains.weekly_meeting.page_creator.run_weekly_page_job",
        side_effect=RuntimeError("설정 없음"),
    ), patch(
        "domains.weekly_meeting.page_create_chat.post_message_to_space",
        return_value=True,
    ) as post:
        _run_page_create_background("spaces/AAA", "ERP2", "ERP2팀")

    payload = post.call_args.kwargs["payload"]
    assert "생성 실패" in payload["cardsV2"][0]["card"]["header"]["subtitle"]
    assert "설정 없음" in payload["cardsV2"][0]["card"]["sections"][0]["widgets"][0]["textParagraph"]["text"]
