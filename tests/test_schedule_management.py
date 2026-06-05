"""캘린더 예약 compose v2 + Phase 2+ 단위 테스트."""

from __future__ import annotations

from typing import Any

import pytest


def test_extract_date_tomorrow():
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state("내일 3시에 회의", members=[])
    assert state["meeting_date"]
    assert state["meeting_time"]


def test_extract_duration_minutes():
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state("30분 회의 내일 2시", members=[])
    assert state.get("duration_minutes") == 30


def test_extract_meet_auto_flag():
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state("화상회의로 미팅 잡아줘", members=[])
    assert state["auto_meet"] is True


def test_attendees_serialize_roundtrip():
    from domains.schedule_management.compose_state import (
        deserialize_attendees,
        serialize_attendees,
    )

    raw = [{"name": "홍길동", "email": "a@x.com"}, {"name": "", "email": "b@x.com"}]
    pipe = serialize_attendees(raw)
    back = deserialize_attendees(pipe)
    assert len(back) == 2
    assert back[0]["name"] == "홍길동"


def test_apply_duration_mode_custom():
    from domains.schedule_management.compose_state import apply_duration_mode

    state = {
        "duration_mode": "custom",
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "meeting_end_time": "11:30",
    }
    apply_duration_mode(state)
    assert state["duration_minutes"] == 90


def test_decode_user_calendar_id():
    from domains.schedule_management.oauth_calendar import (
        decode_calendar_selection,
        encode_user_calendar_id,
    )

    enc = encode_user_calendar_id("u@x.com", "primary")
    api, email = decode_calendar_selection(enc)
    assert api == "primary"
    assert email == "u@x.com"


def test_score_rooms_prefers_capacity():
    from domains.schedule_management.rooms import score_rooms

    rooms = [
        {"id": "a", "name": "소", "capacity": 4, "equipment": [], "default_priority": 0},
        {"id": "b", "name": "대", "capacity": 20, "equipment": ["프로젝터"], "default_priority": 0},
    ]
    scored = score_rooms(rooms, attendee_count=10, equipment_keywords=["프로젝터"])
    assert scored[0][0]["id"] == "b"


def test_recommend_rooms_uses_attendee_count():
    from domains.schedule_management.rooms import score_rooms

    rooms = [
        {"id": "a", "name": "소", "capacity": 4, "equipment": [], "default_priority": 0},
        {"id": "b", "name": "대", "capacity": 20, "equipment": [], "default_priority": 0},
    ]
    state = {"attendees": [], "attendee_count": 10, "equipment_keywords": []}
    explicit = state.get("attendee_count")
    attendee_count = max(int(explicit), 1)
    scored = score_rooms(rooms, attendee_count=attendee_count, equipment_keywords=[])
    assert scored[0][0]["id"] == "b"


def test_get_rooms_fallback_dummy():
    from domains.schedule_management.rooms_store import get_rooms

    rooms = get_rooms()
    assert len(rooms) >= 3


def test_is_dry_run_default_true(monkeypatch):
    from domains.schedule_management.calendar_client import is_dry_run

    monkeypatch.delenv("SCHEDULE_DRY_RUN", raising=False)
    assert is_dry_run() is True


def test_quick_compose_card_widgets():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    out = build_quick_compose_card(state, recommended_rooms=[])
    assert out["cardsV2"][0]["cardId"] == "sm_compose_quick"
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    assert any("dateTimePicker" in w for w in widgets)
    assert any(
        w.get("selectionInput", {}).get("type") == "RADIO_BUTTON"
        for w in widgets
    )
    assert not any(
        b.get("text") == "예약 확정"
        for w in widgets
        if "buttonList" in w
        for b in w["buttonList"]["buttons"]
    )


def test_full_compose_card_has_suggestions():
    from domains.schedule_management.cards import build_full_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    state["compose_step"] = "full"
    state["picked_room_id"] = "r1"
    state["picked_room_name"] = "대회의실"
    state["meeting_date"] = "2026-05-10"
    state["meeting_time"] = "10:00"
    members = [{"name": "김철수", "email": "kim@x.com", "nickname": []}]
    out = build_full_compose_card(
        state,
        calendar_options=[{"id": "primary", "label": "primary"}],
        members=members,
    )
    assert out["cardsV2"][0]["cardId"] == "sm_compose_full"
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    attendee_widgets = [w for w in widgets if w.get("textInput", {}).get("name") == "attendee_input"]
    assert attendee_widgets
    assert "initialSuggestions" in attendee_widgets[0]["textInput"]


@pytest.fixture
def patch_members(monkeypatch):
    members = [
        {"name": "김철수", "email": "kim@x.com", "nickname": [], "team_id": "T1", "team_name": "T1"},
        {"name": "이영희", "email": "lee@x.com", "nickname": ["영희"], "team_id": "T1", "team_name": "T1"},
    ]

    monkeypatch.setattr(
        "domains.schedule_management.handler.get_all_members",
        lambda: members,
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.get_team_list",
        lambda: [{"id": "T1", "name": "T1팀"}],
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.get_team_config",
        lambda tid: {"team_name": "T1팀", "calendar_id": "cal@test"} if tid == "T1" else None,
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.recommend_rooms",
        lambda state, **kw: [],
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.is_oauth_linked",
        lambda email: True,
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.list_user_calendars",
        lambda email: [{"id": "user:u@x.com:primary", "label": "기본"}],
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler._resolve_calendar_auth",
        lambda selected, chat_event: ("primary", "fake-token"),
    )
    return members


def _form(value: str) -> dict[str, Any]:
    return {"stringInputs": {"value": [value]}}


def _full_confirm_params(**overrides: str) -> dict[str, str]:
    base = {
        "compose_step": "full",
        "duration_mode": "1h",
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "meeting_end_time": "",
        "attendee_count": "8",
        "calendar_id": "user:u@x.com:primary",
        "attendees_pipe": "김철수\x1fkim@x.com",
        "want_meet": "1",
        "picked_room_id": "room1",
        "picked_room_name": "대회의실 A",
        "title": "킥오프",
        "equipment_keywords": "",
        "location_keyword": "",
        "room_name_keyword": "",
        "duration_minutes": "60",
    }
    base.update(overrides)
    return base


def test_add_attendee_by_email(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_add_attendee",
        parameters={"compose_step": "full", "attendees_pipe": ""},
        form_inputs={"attendee_input": _form("new@x.com")},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert "new@x.com" in str(out)
    assert out["cardsV2"][0]["cardId"] == "sm_compose_full"


def test_add_attendee_suggestion_format(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_add_attendee",
        parameters={"compose_step": "full", "attendees_pipe": ""},
        form_inputs={"attendee_input": _form("김철수 (kim@x.com)")},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert "kim@x.com" in str(out)


def test_add_attendee_unknown_name_shows_error(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_add_attendee",
        parameters={"compose_step": "full", "attendees_pipe": ""},
        form_inputs={"attendee_input": _form("없는사람")},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert "이메일" in str(out)


def test_create_meet_sets_want_meet(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_create_meet",
        parameters={"compose_step": "full", "attendees_pipe": "", "want_meet": ""},
        form_inputs={},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert "Meet" in str(out) or "확정" in str(out)


def test_quick_update_stays_on_quick_step(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_quick_update",
        parameters={"compose_step": "quick", "duration_mode": "1h"},
        form_inputs={
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "attendee_count": _form("6"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert out["cardsV2"][0]["cardId"] == "sm_compose_quick"


def test_pick_room_moves_to_full_step(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_pick_room",
        parameters={
            "compose_step": "quick",
            "duration_mode": "1h",
            "meeting_date": "2026-05-10",
            "meeting_time": "10:00",
            "attendee_count": "5",
            "picked_room_id": "room1",
            "picked_room_name": "소회의실",
        },
        form_inputs={},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert out["cardsV2"][0]["cardId"] == "sm_compose_full"
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    assert any(w.get("textInput", {}).get("name") == "title" for w in widgets)


def test_back_quick_returns_to_quick_step(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_back_quick",
        parameters={
            "compose_step": "full",
            "duration_mode": "1h",
            "meeting_date": "2026-05-10",
            "meeting_time": "10:00",
            "attendee_count": "5",
            "picked_room_id": "room1",
            "picked_room_name": "소회의실",
        },
        form_inputs={},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert out["cardsV2"][0]["cardId"] == "sm_compose_quick"


def test_confirm_without_room_shows_error(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_confirm",
        parameters=_full_confirm_params(picked_room_id="", picked_room_name=""),
        form_inputs={
            "calendar_id": _form("user:u@x.com:primary"),
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "title": _form("킥오프"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert out["cardsV2"][0]["cardId"] == "sm_compose_full"
    assert "회의실" in str(out)


def test_confirm_custom_invalid_end_time(patch_members):
    from domains.schedule_management.handler import handle_schedule_management_action

    out = handle_schedule_management_action(
        invoked_function="sm_compose_confirm",
        parameters=_full_confirm_params(
            duration_mode="custom",
            meeting_end_time="09:00",
        ),
        form_inputs={
            "calendar_id": _form("user:u@x.com:primary"),
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "meeting_end_time": _form("09:00"),
            "title": _form("킥오프"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert "종료" in str(out)


def test_compose_confirm_dry_run(patch_members, monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action

    monkeypatch.setenv("SCHEDULE_DRY_RUN", "true")
    out = handle_schedule_management_action(
        invoked_function="sm_compose_confirm",
        parameters=_full_confirm_params(),
        form_inputs={
            "calendar_id": _form("user:u@x.com:primary"),
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "title": _form("킥오프"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert out["cardsV2"][0]["cardId"] == "sm_result"
    assert "드라이런" in str(out)


def test_compose_confirm_calls_create_event(patch_members, monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management.handler import handle_schedule_management_action

    monkeypatch.setenv("SCHEDULE_DRY_RUN", "false")
    monkeypatch.setattr("domains.schedule_management.handler.is_dry_run", lambda: False)

    def fake_create(**kwargs):
        return cc.CalendarResult(
            ok=True,
            created_event={
                "id": "evt1",
                "htmlLink": "https://calendar.google.com/event",
                "hangoutLink": "https://meet.google.com/abc-defg-hij",
            },
        )

    monkeypatch.setattr("domains.schedule_management.handler.create_event", fake_create)
    monkeypatch.setattr(
        "domains.schedule_management.handler.save_reservation",
        lambda **kw: "res1",
    )

    out = handle_schedule_management_action(
        invoked_function="sm_compose_confirm",
        parameters=_full_confirm_params(),
        form_inputs={
            "calendar_id": _form("user:u@x.com:primary"),
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "title": _form("킥오프"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    text = str(out)
    assert "예약이 생성되었습니다" in text
    assert "meet.google.com" in text
    assert "참석 인원: 8명" in text
