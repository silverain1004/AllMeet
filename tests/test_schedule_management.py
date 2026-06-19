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


def test_recommend_rooms_filters_by_minimum_capacity():
    from domains.schedule_management.rooms import recommend_rooms

    state = {
        "meeting_date": "",
        "meeting_time": "",
        "duration_mode": "",
        "attendees": [],
        "attendee_count": 15,
        "equipment_keywords": [],
    }
    rooms = recommend_rooms(state, access_token=None)
    capacities = [int(r.get("capacity") or 0) for r in rooms]
    assert capacities
    assert all(c >= 15 or c == 0 for c in capacities)


def test_attendee_count_button_selection_filled():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    state["attendee_count"] = 8
    out = build_quick_compose_card(state, recommended_rooms=[])
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    buttons = widgets[2]["buttonList"]["buttons"]
    filled = [b for b in buttons if b.get("type") == "FILLED"]
    assert len(filled) == 1
    assert filled[0]["text"] == "8+"


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


def test_recommend_rooms_prefers_tight_capacity_fit():
    from domains.schedule_management.compose_availability import ComposeCalendarSnapshot
    from domains.schedule_management.rooms import recommend_rooms

    rooms = [
        {"id": "big", "name": "V-Room", "capacity": 18, "equipment": [], "default_priority": 0},
        {"id": "mid", "name": "N-Room", "capacity": 12, "equipment": [], "default_priority": 0},
        {"id": "small", "name": "T-Room", "capacity": 4, "equipment": [], "default_priority": 0},
    ]
    state = {
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "duration_mode": "1h",
        "attendee_count": 4,
        "equipment_keywords": [],
    }
    snapshot = ComposeCalendarSnapshot(
        start_iso="2026-05-10T10:00:00+09:00",
        end_iso="2026-05-10T11:00:00+09:00",
        room_busy={"big": [], "mid": [], "small": []},
    )
    out = recommend_rooms(state, snapshot=snapshot, rooms=rooms)
    assert [r["id"] for r in out] == ["small", "mid", "big"]


def test_recommend_rooms_sorts_free_before_busy_with_capacity():
    from domains.schedule_management.compose_availability import ComposeCalendarSnapshot
    from domains.schedule_management.rooms import recommend_rooms

    rooms = [
        {
            "id": "busy_small",
            "name": "T-Room",
            "capacity": 4,
            "calendar_resource_id": "busy@resource.calendar.google.com",
            "equipment": [],
            "default_priority": 0,
        },
        {
            "id": "free_big",
            "name": "V-Room",
            "capacity": 18,
            "calendar_resource_id": "free@resource.calendar.google.com",
            "equipment": [],
            "default_priority": 0,
        },
    ]
    state = {
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "duration_mode": "1h",
        "attendee_count": 4,
        "equipment_keywords": [],
    }
    snapshot = ComposeCalendarSnapshot(
        start_iso="2026-05-10T10:00:00+09:00",
        end_iso="2026-05-10T11:00:00+09:00",
        room_busy={
            "busy@resource.calendar.google.com": [
                {"start": "2026-05-10T10:00:00+09:00", "end": "2026-05-10T11:00:00+09:00"}
            ],
            "free@resource.calendar.google.com": [],
        },
    )
    out = recommend_rooms(state, snapshot=snapshot, rooms=rooms, max_n=2)
    assert out[0]["id"] == "free_big"
    assert out[0]["availability"] == "free"


def test_get_rooms_fallback_dummy():
    from domains.schedule_management.rooms_store import get_rooms

    rooms = get_rooms()
    assert len(rooms) >= 3


def test_is_dry_run_default_false(monkeypatch):
    from domains.schedule_management.calendar_client import is_dry_run

    monkeypatch.delenv("SCHEDULE_DRY_RUN", raising=False)
    assert is_dry_run() is False


def test_quick_compose_card_widgets():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    out = build_quick_compose_card(state, recommended_rooms=[])
    assert out["cardsV2"][0]["cardId"] == "sm_compose_quick"
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    assert widgets[0]["textParagraph"]["text"] == "<b>회의일자</b>"
    picker = widgets[1]["dateTimePicker"]
    assert picker["name"] == "meeting_date"
    assert picker["type"] == "DATE_ONLY"
    assert picker["timezoneOffsetDate"] == 540
    assert picker["onChangeAction"]["function"] == "sm_compose_quick_update"
    assert "onChangeAction" not in widgets[1]
    ac_buttons = widgets[2]["buttonList"]["buttons"]
    assert [b["text"] for b in ac_buttons] == ["4+", "8+", "10+", "15+"]
    assert not any(b.get("type") == "FILLED" for b in ac_buttons)
    assert "columns" in widgets[3]
    radio = widgets[4]["selectionInput"]
    assert radio["type"] == "RADIO_BUTTON"
    assert not any(item.get("selected") for item in radio["items"])
    assert not any(
        b.get("text") == "예약 확정"
        for w in widgets
        if "buttonList" in w
        for b in w["buttonList"]["buttons"]
    )


def test_quick_compose_shows_date_display_when_prefilled():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    state["meeting_date"] = "2026-06-05"
    out = build_quick_compose_card(state, recommended_rooms=[])
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    assert "dateTimePicker" not in widgets[1]
    assert widgets[1]["decoratedText"]["text"] == "2026-06-05"
    assert widgets[1]["decoratedText"]["button"]["text"] == "변경"


def test_compose_state_clear_meeting_date_via_parameters():
    from domains.schedule_management.compose_state import compose_state_from

    state = compose_state_from({"meeting_date": ""}, {})
    assert state["meeting_date"] == ""


def test_handle_schedule_extracts_date_from_natural_language():
    from domains.schedule_management.handler import handle_schedule_management

    out = handle_schedule_management("내일 오후 3시 회의", chat_event={"user": {"email": "u@x.com"}})
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    assert "dateTimePicker" not in str(widgets[1])
    assert out["cardsV2"][0]["cardId"] == "sm_compose_quick"


def test_date_input_ms_parsed_as_utc_not_kst():
    from datetime import datetime, timezone

    from domains.schedule_management.compose_state import compose_state_from

    utc = timezone.utc
    ms = str(int(datetime(2026, 6, 5, tzinfo=utc).timestamp() * 1000))
    state = compose_state_from({}, {"meeting_date": {"dateInput": {"msSinceEpoch": ms}}})
    assert state["meeting_date"] == "2026-06-05"


def test_duration_mode_default_empty():
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    assert state["duration_mode"] == ""
    assert state["duration_minutes"] == 0


def test_validate_time_requires_duration_mode():
    from domains.schedule_management.handler import _validate_time_state

    state = {
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "duration_mode": "",
        "meeting_end_time": "",
    }
    errors = _validate_time_state(state)
    assert any("회의 시간" in e for e in errors)


def test_room_display_line_and_availability_labels():
    from domains.schedule_management.rooms import recommend_rooms

    state = {
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "duration_mode": "1h",
        "duration_minutes": 60,
        "attendees": [],
        "attendee_count": 4,
        "equipment_keywords": [],
    }
    rooms = recommend_rooms(state, access_token=None)
    assert rooms
    row = rooms[0]
    assert int(row.get("capacity") or 0) == 4
    assert "T-Room" in row.get("display_name", row.get("name", ""))
    assert row["display_line"].startswith("수용 ")
    assert "|" in row["display_line"]
    assert "모니터" in row["display_line"]
    assert row.get("show_availability")
    assert row["availability_label"] in ("사용 가능", "사용 중")


def test_room_availability_without_duration_mode():
    from domains.schedule_management.rooms import recommend_rooms

    state = {
        "meeting_date": "2026-05-10",
        "meeting_time": "10:00",
        "duration_mode": "",
        "duration_minutes": 0,
        "attendees": [],
        "attendee_count": 4,
        "equipment_keywords": [],
    }
    rooms = recommend_rooms(state, access_token=None)
    assert rooms
    assert rooms[0].get("show_availability")
    assert rooms[0]["availability_label"] in ("사용 가능", "사용 중")


def test_quick_compose_room_widgets_show_availability():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state
    from domains.schedule_management.gunsan_rooms import gunsan_rooms_from_catalog

    state = empty_compose_state()
    state["meeting_date"] = "2026-05-10"
    state["meeting_time"] = "10:00"
    rooms = gunsan_rooms_from_catalog()
    for room in rooms:
        room["show_availability"] = True
        room["availability_label"] = "사용 가능"
        room["display_line"] = f"수용 {room['capacity']}명 | 빔프로젝터, 모니터"
    out = build_quick_compose_card(state, recommended_rooms=rooms)
    widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
    room_widget = next(
        w
        for w in widgets
        if "decoratedText" in w and "선택" in str(w.get("decoratedText", {}).get("button", {}))
    )
    top = room_widget["decoratedText"]["topLabel"]
    text = room_widget["decoratedText"]["text"]
    assert "사용 가능" in top
    assert text.startswith("수용 ")
    assert "|" in text


def test_member_suggestion_items_name_email_only():
    from domains.schedule_management.cards import _member_suggestion_items

    members = [
        {"name": "이민규", "email": "imk1984@vntgcorp.com"},
        {"name": "김철수", "email": "kim@x.com"},
    ]
    items = _member_suggestion_items(members)
    texts = [i["text"] for i in items]
    assert texts == ["이민규 (imk1984@vntgcorp.com)", "김철수 (kim@x.com)"]
    assert "imk1984@vntgcorp.com" not in texts


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
    def _find_attendee_input(widget_list: list[dict]) -> dict | None:
        for w in widget_list:
            if w.get("textInput", {}).get("name") == "attendee_input":
                return w["textInput"]
            if "columns" in w:
                for col in w["columns"]["columnItems"]:
                    found = _find_attendee_input(col.get("widgets") or [])
                    if found:
                        return found
        return None

    attendee_field = _find_attendee_input(widgets)
    assert attendee_field
    assert "initialSuggestions" in attendee_field
    assert "label" not in attendee_field
    assert "placeholderText" not in attendee_field
    attendee_columns = [
        w for w in widgets if "columns" in w and any(
            "attendee_input" in str(col)
            for col in w["columns"]["columnItems"]
        )
    ]
    assert attendee_columns
    meet_columns = [
        w for w in widgets if "columns" in w and any(
            "화상회의" in str(col) for col in w["columns"]["columnItems"]
        )
    ]
    assert meet_columns
    footer = next(
        w
        for w in widgets
        if any(b.get("text") == "예약 확정" for b in (w.get("buttonList", {}).get("buttons") or []))
    )
    footer_texts = [b["text"] for b in footer["buttonList"]["buttons"]]
    assert footer_texts == ["홈으로", "회의실수정", "예약 확정"]
    assert footer["buttonList"]["buttons"][-1]["type"] == "FILLED"


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
    assert out["cardsV2"][0]["cardId"] == "sm_booking_confirmed"
    assert "드라이런" in str(out)


def test_compose_confirm_calls_create_event(patch_members, monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management.handler import handle_schedule_management_action

    monkeypatch.setenv("SCHEDULE_DRY_RUN", "false")
    monkeypatch.setattr("domains.schedule_management.handler.is_dry_run", lambda: False)

    captured: dict[str, Any] = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
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
    assert out["cardsV2"][0]["cardId"] == "sm_booking_confirmed"
    assert "예약이 완료되었습니다" in text
    assert "meet.google.com" in text
    assert captured.get("send_updates") == "none"
    assert "초대메일전송" in text


def test_compose_confirm_booker_only_skips_invite_updates(patch_members, monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management.handler import handle_schedule_management_action

    monkeypatch.setenv("SCHEDULE_DRY_RUN", "false")
    monkeypatch.setattr("domains.schedule_management.handler.is_dry_run", lambda: False)

    captured: dict[str, Any] = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return cc.CalendarResult(
            ok=True,
            created_event={"id": "evt1", "htmlLink": "", "hangoutLink": ""},
        )

    monkeypatch.setattr("domains.schedule_management.handler.create_event", fake_create)
    monkeypatch.setattr("domains.schedule_management.handler.save_reservation", lambda **kw: "res1")

    handle_schedule_management_action(
        invoked_function="sm_compose_confirm",
        parameters=_full_confirm_params(attendees_pipe=""),
        form_inputs={
            "calendar_id": _form("user:u@x.com:primary"),
            "meeting_date": _form("2026-05-10"),
            "meeting_time": _form("10:00"),
            "title": _form("킥오프"),
        },
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert captured.get("send_updates") == "none"


def test_send_invite_patch_includes_attendees(patch_members, monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management.handler import handle_schedule_management_action

    captured: dict[str, Any] = {}

    def fake_patch(**kwargs):
        captured.update(kwargs)
        return cc.CalendarResult(ok=True, created_event={"id": "evt1"})

    monkeypatch.setattr("domains.schedule_management.handler.patch_event", fake_patch)
    monkeypatch.setattr(
        "domains.schedule_management.handler._resolve_calendar_auth",
        lambda *_a, **_k: ("primary", "token"),
    )

    out = handle_schedule_management_action(
        invoked_function="sm_send_invite",
        parameters={
            "last_api_calendar_id": "primary",
            "last_event_id": "evt1",
            "calendar_id": "user:u@x.com:primary",
            "title": "킥오프",
            "attendee_emails": "u@x.com,kim@x.com",
            "booker_email": "u@x.com",
            "meeting_date": "2026-05-10",
            "meeting_time": "10:00",
            "meeting_end_time": "11:00",
        },
        form_inputs={},
        chat_event={"user": {"email": "u@x.com"}},
    )
    assert captured.get("send_updates") == "all"
    assert captured.get("attendees") == ["u@x.com", "kim@x.com"]
    assert "초대 메일" in str(out)


def test_parse_capacity_from_room_name():
    from domains.schedule_management.gunsan_rooms import parse_capacity_from_name

    assert parse_capacity_from_name("VNTG 군산 V-Room (18)") == 18
    assert parse_capacity_from_name("VNTG 군산 T-Room (4)") == 4
    assert parse_capacity_from_name("이름 없음") == 10


def test_gunsan_catalog_has_three_rooms_with_capacity():
    from domains.schedule_management.gunsan_rooms import gunsan_rooms_from_catalog

    rooms = gunsan_rooms_from_catalog()
    assert len(rooms) == 3
    caps = {r["name"]: r["capacity"] for r in rooms}
    assert caps["VNTG 군산 V-Room (18)"] == 18
    assert caps["VNTG 군산 N-Room (12)"] == 12
    assert caps["VNTG 군산 T-Room (4)"] == 4
    assert all(r["calendar_resource_id"].endswith("@resource.calendar.google.com") for r in rooms)


def test_merge_attendees_includes_resource():
    from domains.schedule_management.calendar_client import _merge_attendees

    merged = _merge_attendees(["u@x.com"], ["room@resource.calendar.google.com"])
    assert {"email": "u@x.com"} in merged
    assert {"email": "room@resource.calendar.google.com", "resource": True} in merged


def test_sync_rooms_from_manual_ids(monkeypatch):
    from domains.schedule_management import rooms_sync as rs

    stored: list[dict[str, Any]] = []

    monkeypatch.setattr(
        rs,
        "get_room_calendar_config",
        lambda: {
            "group_calendar_id": "",
            "sync_name_filter": "군산",
            "room_resource_ids": [
                "gunsan-a@resource.calendar.google.com",
                "gunsan-b@resource.calendar.google.com",
            ],
            "room_catalog": [
                {
                    "name": "VNTG 군산 V-Room (18)",
                    "calendar_resource_id": "gunsan-a@resource.calendar.google.com",
                    "location": "브이엔티지(군산)-3층",
                },
                {
                    "name": "VNTG 군산 N-Room (12)",
                    "calendar_resource_id": "gunsan-b@resource.calendar.google.com",
                    "location": "브이엔티지(군산)-3층",
                },
            ],
            "impersonate_email": "",
        },
    )
    monkeypatch.setattr(rs, "upsert_rooms", lambda rooms: stored.extend(rooms))

    count, msg = rs.sync_resource_rooms_from_calendar_list()
    assert count == 2
    assert "수동 등록" in msg
    assert stored[0]["calendar_resource_id"] == "gunsan-a@resource.calendar.google.com"
    assert stored[0]["capacity"] == 18


def test_sync_rooms_filters_gunsan(monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management import rooms_sync as rs

    stored: list[dict[str, Any]] = []

    monkeypatch.setattr(
        rs,
        "get_room_calendar_config",
        lambda: {
            "group_calendar_id": "",
            "sync_name_filter": "군산",
            "room_resource_ids": [],
            "impersonate_email": "",
        },
    )
    monkeypatch.setattr(rs, "upsert_rooms", lambda rooms: stored.extend(rooms))
    monkeypatch.setattr(
        rs,
        "list_calendar_list",
        lambda **kw: cc.CalendarResult(
            ok=True,
            calendar_list_items=[
                {
                    "id": "gunsan-1@resource.calendar.google.com",
                    "summary": "군산 회의실 1",
                    "description": "",
                },
                {
                    "id": "seoul-1@resource.calendar.google.com",
                    "summary": "서울 회의실 1",
                    "description": "",
                },
            ],
        ),
    )

    count, msg = rs.sync_resource_rooms_from_calendar_list()
    assert count == 1
    assert "군산" in stored[0]["name"] or "gunsan" in stored[0]["calendar_resource_id"]


def test_create_event_includes_resource_attendee(monkeypatch):
    from domains.schedule_management import calendar_client as cc

    monkeypatch.setenv("SCHEDULE_DRY_RUN", "false")
    captured: dict[str, Any] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return (
            {"id": "evt1", "htmlLink": "https://calendar.google.com/event"},
            None,
        )

    monkeypatch.setattr(cc, "_request", fake_request)
    result = cc.create_event(
        calendar_id="primary",
        summary="회의",
        start_iso="2026-05-10T10:00:00+09:00",
        end_iso="2026-05-10T11:00:00+09:00",
        attendees=["u@x.com"],
        resource_emails=["room@resource.calendar.google.com"],
    )
    assert result.ok
    attendees = captured["body"]["attendees"]
    assert any(a.get("resource") for a in attendees)


def test_group_calendar_bookings_match_room():
    from domains.schedule_management.rooms_group import match_booking_to_room

    rooms = [
        {"id": "r1", "name": "군산 A", "calendar_resource_id": "a@resource.calendar.google.com"},
    ]
    event = {
        "summary": "팀 회의",
        "location": "",
        "attendees": [{"email": "a@resource.calendar.google.com", "resource": True}],
    }
    matched = match_booking_to_room(event, rooms)
    assert matched is not None
    assert matched["id"] == "r1"


def test_confirm_rejects_busy_room(patch_members, monkeypatch):
    from domains.schedule_management import calendar_client as cc
    from domains.schedule_management.handler import handle_schedule_management_action

    monkeypatch.setattr(
        "domains.schedule_management.handler.get_rooms",
        lambda: [
            {
                "id": "room1",
                "name": "군산 A",
                "calendar_resource_id": "busy@resource.calendar.google.com",
                "capacity": 10,
                "equipment": [],
                "default_priority": 0,
            }
        ],
    )
    monkeypatch.setattr(
        "domains.schedule_management.handler.freebusy_query",
        lambda **kw: cc.CalendarResult(
            ok=True,
            busy={
                "busy@resource.calendar.google.com": [
                    {"start": "2026-05-10T09:00:00+09:00", "end": "2026-05-10T12:00:00+09:00"}
                ]
            },
        ),
    )
    monkeypatch.setattr("domains.schedule_management.handler.is_dry_run", lambda: True)

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
    assert out["cardsV2"][0]["cardId"] == "sm_compose_full"
    assert "사용 중" in str(out)
