"""회의실 충돌 대안 시간 제안 + 예약 현황 회의실 행 흡수."""

from __future__ import annotations

from typing import Any

import pytest

from domains.schedule_management.calendar_client import CalendarResult

DATE = "2026-09-18"
BOOKER = "me@x.com"
HONG = "hong@x.com"

ROOM_N = {
    "id": "rN",
    "name": "N-Room",
    "display_name": "VNTG 군산 N-Room (12)",
    "capacity": 12,
    "calendar_resource_id": "resN",
}
ROOM_V = {
    "id": "rV",
    "name": "V-Room",
    "display_name": "VNTG 군산 V-Room (18)",
    "capacity": 18,
    "calendar_resource_id": "resV",
}


def _iso(hhmm: str) -> str:
    from domains.schedule_management.calendar_client import to_kst_iso

    return to_kst_iso(DATE, hhmm)


def _span(start: str, end: str) -> dict[str, str]:
    return {"start": _iso(start), "end": _iso(end)}


def _state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "meeting_date": DATE,
        "meeting_time": "14:00",
        "duration_mode": "1h",
        "duration_minutes": 60,
        "attendees": [{"name": "홍길동", "email": HONG}],
    }
    base.update(over)
    return base


def _snapshot(busy_emails: set[str]) -> Any:
    from domains.schedule_management.compose_availability import ComposeCalendarSnapshot

    return ComposeCalendarSnapshot(
        start_iso=_iso("14:00"),
        end_iso=_iso("15:00"),
        attendee_busy_emails=busy_emails,
    )


@pytest.fixture(autouse=True)
def _clear_caches():
    from domains.schedule_management.compose_availability import clear_snapshot_cache

    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


@pytest.fixture
def stub_calendar(monkeypatch):
    """freebusy 스텁 — 사람/회의실 바쁜 구간을 테스트가 지정한다."""
    calls: list[list[str]] = []
    state: dict[str, dict[str, list[dict[str, str]]]] = {"busy": {}}

    def fake_freebusy(*, calendar_ids, time_min, time_max, access_token=None):
        calls.append(list(calendar_ids))
        return CalendarResult(
            ok=True,
            busy={cid: list(state["busy"].get(cid) or []) for cid in calendar_ids},
        )

    monkeypatch.setattr(
        "domains.schedule_management.conflict_slots.freebusy_query", fake_freebusy
    )
    monkeypatch.setattr(
        "domains.schedule_management.conflict_slots.get_room_calendar_config", lambda: {}
    )
    return type("Stub", (), {"calls": calls, "busy": state["busy"]})()


# ---------------------------------------------------------------- 대안 시간 제안


def test_quick_step_conflict_still_suggests_alternatives(stub_calendar):
    """간편예약(light) 단계에서도 대안 시간이 나와야 한다."""
    from domains.schedule_management.conflict_slots import check_schedule_conflicts

    stub_calendar.busy[HONG] = [_span("14:00", "15:00")]

    result = check_schedule_conflicts(
        _state(),
        access_token="tok",
        user_email=BOOKER,
        user_name="나",
        api_calendar_id=BOOKER,
        snapshot=_snapshot({HONG}),
        mode="light",
        rooms=[ROOM_N],
    )

    assert result.has_conflict is True
    assert result.alternatives, "light 모드에서 대안 시간이 비어 있으면 안 된다"


def test_no_conflict_does_not_search_alternatives(stub_calendar, monkeypatch):
    """충돌이 없으면 대안 탐색은 돌지 않는다 (렌더 지연 회귀 방지)."""
    from domains.schedule_management import conflict_slots

    searched: list[int] = []
    monkeypatch.setattr(
        conflict_slots,
        "suggest_alternative_slots",
        lambda *a, **k: searched.append(1) or [],
    )

    result = conflict_slots.check_schedule_conflicts(
        _state(),
        access_token="tok",
        user_email=BOOKER,
        user_name="나",
        api_calendar_id=BOOKER,
        snapshot=_snapshot(set()),
        mode="light",
        rooms=[ROOM_N],
    )

    assert result.has_conflict is False
    assert searched == []


def test_fully_booked_day_yields_no_alternatives(stub_calendar):
    """업무시간이 전부 차 있으면 대안은 빈 리스트."""
    from domains.schedule_management.conflict_slots import suggest_alternative_slots

    stub_calendar.busy[HONG] = [_span("08:00", "19:00")]

    found = suggest_alternative_slots(
        _state(),
        access_token="tok",
        user_email=BOOKER,
        user_name="나",
        api_calendar_id=BOOKER,
        rooms=[ROOM_N],
    )

    assert found == []


def test_day_freebusy_is_cached_across_calls(stub_calendar):
    """같은 날·같은 대상이면 두 번째 탐색은 freebusy 를 다시 치지 않는다."""
    from domains.schedule_management.conflict_slots import suggest_alternative_slots

    stub_calendar.busy[HONG] = [_span("14:00", "15:00")]
    kwargs = dict(
        access_token="tok",
        user_email=BOOKER,
        user_name="나",
        api_calendar_id=BOOKER,
        rooms=[ROOM_N],
    )

    first = suggest_alternative_slots(_state(), **kwargs)
    after_first = len(stub_calendar.calls)
    second = suggest_alternative_slots(_state(), **kwargs)

    assert after_first > 0
    assert len(stub_calendar.calls) == after_first, "캐시 hit 인데 freebusy 를 다시 쳤다"
    assert [s.meeting_time for s in second] == [s.meeting_time for s in first]


def test_card_explains_when_no_alternative_exists():
    """대안이 없으면 충돌 문구만 남기지 말고 이유를 알려준다."""
    from domains.schedule_management.cards import _conflict_widgets
    from domains.schedule_management.conflict_slots import ConflictCheckResult, ConflictInfo

    check = ConflictCheckResult(
        has_conflict=True,
        conflicts=[
            ConflictInfo(
                kind="attendee",
                label="홍길동",
                event_summary="(일정 있음)",
                start_iso=_iso("14:00"),
                end_iso=_iso("15:00"),
                html_link="",
            )
        ],
        alternatives=[],
        requested_time="14:00",
    )

    rendered = str(_conflict_widgets(check, _state(), {}))

    assert "모두 되는 시간" in rendered


# ------------------------------------------------- 예약 현황 → 회의실 행 흡수


def _group_event(summary: str, start: str, end: str, resource_id: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "start": _iso(start),
        "end": _iso(end),
        "attendees": [{"email": resource_id, "resource": True}],
    }


def test_group_booking_reason_includes_title():
    """집계 캘린더 예약은 시간과 제목을 회의실별로 돌려준다."""
    from domains.schedule_management.rooms_group import build_room_booking_reasons

    reasons = build_room_booking_reasons(
        [_group_event("특수강 프로세스 설명", "14:00", "15:00", "resN")],
        [ROOM_N, ROOM_V],
        time_min_iso=_iso("14:00"),
        time_max_iso=_iso("15:00"),
        room_busy={},
    )

    assert reasons == {"resN": "14:00~15:00 특수강 프로세스 설명"}


def test_freebusy_only_booking_has_no_title():
    """리소스 캘린더로만 확인되는 직접 예약은 제목을 알 수 없다."""
    from domains.schedule_management.rooms_group import build_room_booking_reasons

    reasons = build_room_booking_reasons(
        [],
        [ROOM_N],
        time_min_iso=_iso("14:00"),
        time_max_iso=_iso("15:00"),
        room_busy={"resN": [_span("14:00", "15:30")]},
    )

    assert reasons == {"resN": "14:00~15:30 (예약됨)"}


def test_multiple_bookings_collapse_to_extra_count():
    """한 회의실에 여러 건이면 첫 건만 쓰고 나머지는 건수로 접는다."""
    from domains.schedule_management.rooms_group import build_room_booking_reasons

    reasons = build_room_booking_reasons(
        [
            _group_event("특수강 프로세스 설명", "14:00", "14:30", "resN"),
            _group_event("유사강종 협의", "14:30", "15:00", "resN"),
        ],
        [ROOM_N],
        time_min_iso=_iso("14:00"),
        time_max_iso=_iso("15:00"),
        room_busy={},
    )

    assert reasons == {"resN": "14:00~14:30 특수강 프로세스 설명 외 1건"}


def test_room_row_shows_booking_reason():
    """회의실 행 자체가 왜 막혔는지 설명한다."""
    from domains.schedule_management.cards import _room_widgets

    room = dict(ROOM_N)
    room["availability"] = "busy"
    room["display_line"] = "수용 12명 | TV"
    room["busy_reason"] = "14:00~15:00 특수강 프로세스 설명"

    widgets = _room_widgets([room], _state(), {})

    assert any(
        w.get("decoratedText", {}).get("bottomLabel") == "14:00~15:00 특수강 프로세스 설명"
        for w in widgets
    )


def test_recommend_rooms_attaches_busy_reason():
    """추천 회의실 결과에 예약 사유가 붙어 나온다."""
    from domains.schedule_management.compose_availability import ComposeCalendarSnapshot
    from domains.schedule_management.rooms import recommend_rooms

    snapshot = ComposeCalendarSnapshot(
        start_iso=_iso("14:00"),
        end_iso=_iso("15:00"),
        room_busy={"resN": [_span("14:00", "15:00")]},
        group_bookings=[_group_event("특수강 프로세스 설명", "14:00", "15:00", "resN")],
    )

    rooms = recommend_rooms(
        _state(),
        access_token="tok",
        snapshot=snapshot,
        rooms=[ROOM_N, ROOM_V],
    )

    by_id = {r["id"]: r for r in rooms}
    assert by_id["rN"].get("busy_reason") == "14:00~15:00 특수강 프로세스 설명"
    assert not by_id["rV"].get("busy_reason")


def test_compose_card_drops_group_booking_summary_param():
    """예약 현황은 회의실 행으로 흡수됐으므로 카드가 요약 문단을 받지 않는다."""
    import inspect

    from domains.schedule_management.cards import build_compose_card, build_quick_compose_card

    for fn in (build_compose_card, build_quick_compose_card):
        assert "group_booking_summary" not in inspect.signature(fn).parameters, fn.__name__
