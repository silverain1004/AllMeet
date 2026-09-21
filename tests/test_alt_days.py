"""같은 날 대안이 없으면 다음 영업일(최대 3일)에서 모두 가능한 시간을 제안한다."""

from __future__ import annotations

from domains.schedule_management import conflict_slots
from domains.schedule_management.conflict_slots import SlotSuggestion


def _state(date: str) -> dict:
    return {
        "meeting_date": date,
        "meeting_time": "10:00",
        "duration_mode": "1h",
        "attendees": [{"name": "A", "email": "a@x.com"}],
    }


def test_alternatives_fall_back_to_next_business_day(monkeypatch):
    calls: list[str] = []

    def fake_slots(state, *, date, candidates, duration, access_token, api_calendar_id, rooms, max_n):
        calls.append(date)
        if date == "2026-09-29":  # 화요일 — 여기서 처음 나옴
            return [SlotSuggestion(date, "09:00", "10:00", 2, "A")], 0
        return [], 0

    monkeypatch.setattr(conflict_slots, "_slot_suggestions", fake_slots)
    # 금요일 요청 → 토·일 건너뛰고 월(9/28) → 화(9/29)
    out = conflict_slots.suggest_alternative_slots(
        _state("2026-09-25"), access_token="tok", user_email="me", user_name="나", api_calendar_id="primary"
    )
    assert calls == ["2026-09-25", "2026-09-28", "2026-09-29"]
    assert [(s.meeting_date, s.meeting_time) for s in out] == [("2026-09-29", "09:00")]


def test_alternatives_give_up_after_lookahead(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        conflict_slots, "_slot_suggestions",
        lambda state, *, date, **kw: (calls.append(date) or ([], 0)),
    )
    out = conflict_slots.suggest_alternative_slots(
        _state("2026-09-21"), access_token="tok", user_email="me", user_name="나", api_calendar_id="primary"
    )
    assert out == []
    # 5영업일: 화·수·목·금 + 다음 주 월 (주말 건너뜀)
    assert calls == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]


def test_same_day_alternatives_do_not_look_ahead(monkeypatch):
    calls: list[str] = []

    def fake_slots(state, *, date, candidates, duration, **kw):
        calls.append(date)
        assert "10:00" not in candidates  # 요청 시각은 같은 날 후보에서 제외
        return [SlotSuggestion(date, "11:00", "12:00", 1, "A")], 0

    monkeypatch.setattr(conflict_slots, "_slot_suggestions", fake_slots)
    out = conflict_slots.suggest_alternative_slots(
        _state("2026-09-21"), access_token="tok", user_email="me", user_name="나", api_calendar_id="primary"
    )
    assert calls == ["2026-09-21"]
    assert out[0].meeting_time == "11:00"


def test_slot_buttons_prefix_other_day():
    from domains.schedule_management.cards import _conflict_widgets

    check = conflict_slots.ConflictCheckResult(
        has_conflict=True,
        conflicts=[
            conflict_slots.ConflictInfo(
                kind="attendee", label="A", event_summary="다른 회의", start_iso="", end_iso="", html_link=""
            )
        ],
        alternatives=[SlotSuggestion("2026-09-23", "09:00", "10:00", 2, "Bali")],
        requested_time="10:00",
    )
    widgets = _conflict_widgets(check, {"meeting_date": "2026-09-21", "meeting_time": "10:00"}, {"compose_step": "quick"})
    text = str(widgets)
    assert "가장 가까운 날의 가능한 시간" in text
    assert "9/23(수) 09:00~10:00 · Bali" in text
    btn = next(w for w in widgets if "buttonList" in w and "sm_compose_pick_slot" in str(w))
    params = {p["key"]: p["value"] for p in btn["buttonList"]["buttons"][0]["onClick"]["action"]["parameters"]}
    assert params["slot_date"] == "2026-09-23"
