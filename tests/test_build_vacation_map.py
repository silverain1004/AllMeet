"""_build_vacation_map: 조회 성공 시 4개 카테고리를 항상 포함(빈 주도 명시),
조회/파싱 실패 시엔 표를 건드리지 않도록 빈 dict 반환 (일정 공유 표 stale 데이터 버그 수정)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from domains.weekly_meeting.page_creator import _build_vacation_map
from domains.weekly_meeting.schedule_lookup import LookupResult

REFERENCE_DATE = datetime(2026, 8, 13)  # 목요일, 이번 주=08/10~08/14


def test_success_with_no_events_returns_all_four_categories_empty():
    with patch(
        "domains.weekly_meeting.schedule_lookup.fetch_all_calendar_events",
        return_value=LookupResult(ok=True, events=[]),
    ):
        result = _build_vacation_map(["김도현"], "cal-1", REFERENCE_DATE)

    assert set(result.keys()) == {"business_trip", "field_work", "remote", "vacation"}
    for cat in result.values():
        assert cat == {"this_week": "", "next_week": ""}


def test_fetch_failure_returns_empty_dict_not_touched():
    with patch(
        "domains.weekly_meeting.schedule_lookup.fetch_all_calendar_events",
        return_value=LookupResult(ok=False, events=[], error_kind="http_error"),
    ):
        result = _build_vacation_map(["김도현"], "cal-1", REFERENCE_DATE)

    assert result == {}


def test_exception_during_parsing_returns_empty_dict_not_partial():
    with patch(
        "domains.weekly_meeting.schedule_lookup.fetch_all_calendar_events",
        side_effect=RuntimeError("boom"),
    ):
        result = _build_vacation_map(["김도현"], "cal-1", REFERENCE_DATE)

    assert result == {}


def test_success_with_business_trip_event_this_week():
    events = [
        {
            "summary": "출장 - 김도현",
            "start": "2026-08-11",
            "end": "2026-08-12",
        }
    ]
    with patch(
        "domains.weekly_meeting.schedule_lookup.fetch_all_calendar_events",
        return_value=LookupResult(ok=True, events=events),
    ):
        result = _build_vacation_map(["김도현"], "cal-1", REFERENCE_DATE)

    assert "김도현(출장)" in result["business_trip"]["this_week"]
    # 이벤트 없는 다른 카테고리도 여전히 키는 존재, 값은 빈 문자열
    assert result["remote"] == {"this_week": "", "next_week": ""}
