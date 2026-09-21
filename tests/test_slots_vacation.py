"""팀 공통 빈 시간(suggest_day_slots)·휴가 참석자 경고(vacation_conflicts) — 네트워크는 전부 mock."""

from __future__ import annotations

from typing import Any

from domains.schedule_management import conflict_slots

ROOMS = [
    {"id": "r1", "name": "A", "display_name": "A", "capacity": 6, "equipment": [],
     "calendar_resource_id": "a@resource.calendar.google.com", "office": "seoul", "default_priority": 10},
    {"id": "r2", "name": "B", "display_name": "B", "capacity": 20, "equipment": [],
     "calendar_resource_id": "b@resource.calendar.google.com", "office": "seoul", "default_priority": 10},
]


def _no_cache(monkeypatch):
    monkeypatch.setattr(conflict_slots, "part_cache_get", lambda part, key: (False, None))
    monkeypatch.setattr(conflict_slots, "part_cache_put", lambda part, key, val: None)
    monkeypatch.setattr(conflict_slots, "get_room_calendar_config", lambda: {"group_calendar_id": ""})
    monkeypatch.setattr(conflict_slots, "list_group_room_bookings", lambda **kw: [])


def test_suggest_day_slots_skips_busy_people_and_counts_unknown(monkeypatch):
    _no_cache(monkeypatch)

    def fake_freebusy(calendar_ids, *, time_min, time_max, access_token):
        busy: dict[str, list[dict[str, str]]] = {}
        for cid in calendar_ids:
            if cid == "primary":
                busy[cid] = [{"start": "2026-10-05T08:00:00+09:00", "end": "2026-10-05T10:00:00+09:00"}]
            elif cid == "a@x.com":
                busy[cid] = [{"start": "2026-10-05T10:00:00+09:00", "end": "2026-10-05T11:00:00+09:00"}]
            elif cid.endswith("@resource.calendar.google.com"):
                busy[cid] = []
            # b@x.com 은 free/busy 를 비공개 → 응답에 없음 → '확인 불가'
        return busy

    monkeypatch.setattr(conflict_slots, "_freebusy_chunked", fake_freebusy)
    state = {
        "meeting_date": "2026-10-05",
        "meeting_time": "",
        "duration_mode": "1h",
        "attendees": [{"name": "A", "email": "a@x.com"}, {"name": "B", "email": "b@x.com"}],
        "attendee_count": 4,
    }
    slots, unknown = conflict_slots.suggest_day_slots(
        state, access_token="tok", api_calendar_id="primary", rooms=ROOMS, max_n=3
    )
    assert unknown == 1
    assert [s.meeting_time for s in slots] == ["11:00", "11:30", "12:00"]
    assert slots[0].meeting_end_time == "12:00"
    assert slots[0].top_room_name == "A"  # 정원 6이 4명 회의에 더 맞다


def test_suggest_day_slots_requires_date_and_token():
    assert conflict_slots.suggest_day_slots({"meeting_date": ""}, access_token="t", api_calendar_id="p") == ([], 0)
    assert conflict_slots.suggest_day_slots({"meeting_date": "2026-10-05"}, access_token="", api_calendar_id="p") == ([], 0)


def test_vacation_conflicts_matches_team_calendar_events(monkeypatch):
    monkeypatch.setattr(
        conflict_slots,
        "get_all_members",
        lambda: [
            {"name": "김민수", "email": "minsu@x.com", "team_id": "PC2"},
            {"name": "이지은", "email": "jieun@x.com", "team_id": "PC2"},
        ],
    )
    monkeypatch.setattr(conflict_slots, "get_team_config", lambda tid: {"vacation_calendar_id": "vac-PC2"})
    calls: list[tuple[str, str]] = []

    def fake_events(calendar_id, date):
        calls.append((calendar_id, date))
        return [{"summary": "연차(민수)", "start": "2026-10-05", "end": "2026-10-06"}]

    monkeypatch.setattr(conflict_slots, "_vacation_events_for_calendar", fake_events)
    state = {"attendees": [{"name": "김민수", "email": "minsu@x.com"}, {"name": "이지은", "email": "jieun@x.com"}]}
    out = conflict_slots.vacation_conflicts(state, date="2026-10-05")
    assert [(c.kind, c.label, c.attendee_email) for c in out] == [("vacation", "김민수", "minsu@x.com")]
    assert out[0].event_summary == "연차(민수)"
    # 같은 팀 캘린더는 하루치 1회만 조회
    assert calls == [("vac-PC2", "2026-10-05")]


def test_check_schedule_conflicts_vacation_only_has_no_alternatives(monkeypatch):
    monkeypatch.setattr(conflict_slots, "_conflicts_for_slot", lambda state, **kw: [])
    monkeypatch.setattr(
        conflict_slots,
        "vacation_conflicts",
        lambda state, *, date: [
            conflict_slots.ConflictInfo(
                kind="vacation", label="김민수", event_summary="연차", start_iso="", end_iso="",
                html_link="", attendee_email="minsu@x.com",
            )
        ],
    )
    called = {"alt": False}

    def fake_alt(*a, **kw):
        called["alt"] = True
        return []

    monkeypatch.setattr(conflict_slots, "suggest_alternative_slots", fake_alt)
    state = {"meeting_date": "2026-10-05", "meeting_time": "10:00", "duration_mode": "1h",
             "attendees": [{"name": "김민수", "email": "minsu@x.com"}]}
    res = conflict_slots.check_schedule_conflicts(
        state, access_token="tok", user_email="me@x.com", user_name="나", api_calendar_id="primary"
    )
    assert res.has_conflict is True
    assert [c.kind for c in res.conflicts] == ["vacation"]
    assert called["alt"] is False


def test_conflict_widgets_render_vacation_row_with_remove_button():
    from domains.schedule_management.cards import _conflict_widgets

    check = conflict_slots.ConflictCheckResult(
        has_conflict=True,
        conflicts=[
            conflict_slots.ConflictInfo(
                kind="vacation", label="김민수", event_summary="연차(민수)", start_iso="", end_iso="",
                html_link="", attendee_email="minsu@x.com",
            )
        ],
        requested_time="10:00",
    )
    widgets = _conflict_widgets(check, {"meeting_time": "10:00"}, {"compose_step": "quick"})
    text = str(widgets)
    assert "휴가·부재 참석자가 있어요" in text
    assert "일정이 겹칩니다" not in text
    assert "그대로 진행" not in text
    btn = next(w for w in widgets if "decoratedText" in w)["decoratedText"]["button"]
    assert btn["text"] == "빼고 진행"
    assert btn["onClick"]["action"]["function"] == "sm_compose_remove_attendee_email"
    params = {p["key"]: p["value"] for p in btn["onClick"]["action"]["parameters"]}
    assert params["remove_email"] == "minsu@x.com"


def test_day_slot_widgets_in_quick_card():
    from domains.schedule_management.cards import build_quick_compose_card
    from domains.schedule_management.compose_state import empty_compose_state

    state = empty_compose_state()
    state["meeting_date"] = "2026-10-05"
    state["find_slot"] = True
    slot = conflict_slots.SlotSuggestion("2026-10-05", "11:00", "12:00", 2, "A")
    out = build_quick_compose_card(
        state, recommended_rooms=[], room_preview_ready=False, day_slots=[slot], day_slots_note="확인 불가 1명"
    )
    text = str(out)
    assert "참석자·회의실 모두 가능한 시간" in text
    assert "sm_compose_pick_slot" in text
    assert "확인 불가 1명" in text


def test_remove_attendee_email_action(monkeypatch):
    from domains.schedule_management import handler

    monkeypatch.setattr(handler, "get_all_members", lambda: [])
    monkeypatch.setattr(handler, "get_rooms", lambda: [])
    monkeypatch.setattr(handler, "is_oauth_linked", lambda email: False)
    monkeypatch.setattr(handler, "detect_office_for_date", lambda email, date: "")
    monkeypatch.setattr(handler, "_team_default_region", lambda email: "")
    monkeypatch.setattr(
        handler, "_calendar_options", lambda chat_event, linked=None: [{"id": "primary", "label": "내 캘린더"}]
    )
    state = handler.compose_state_from({"compose_step": "quick"}, {})
    state["attendees"] = [{"name": "김민수", "email": "minsu@x.com"}, {"name": "이지은", "email": "jieun@x.com"}]
    params = handler.state_to_button_params(state)
    params["remove_email"] = "minsu@x.com"
    out = handler.handle_schedule_management_action(
        invoked_function="sm_compose_remove_attendee_email",
        parameters=params,
        form_inputs={},
        chat_event={"user": {"email": "me@x.com"}},
    )
    text = str(out)
    assert "jieun@x.com" in text
    assert "minsu@x.com" not in text
