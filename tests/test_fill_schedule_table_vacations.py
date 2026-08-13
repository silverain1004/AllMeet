"""일정 공유 표 채우기 — 이벤트 없는 주/카테고리는 지난 데이터가 남지 않고 비워져야 함
(실데이터에서 발견된 회귀: 몇 주 전 출장 이벤트가 계속 남아있던 버그)."""
from __future__ import annotations

from domains.weekly_meeting.page_html import fill_schedule_table_vacations


def _table(business_trip_this_week: str = "") -> str:
    return (
        "<p>일정 공유</p>"
        '<table><tbody><tr><th /><th>이번 주</th><th>다음 주</th></tr>'
        f"<tr><td><strong>출장</strong></td><td>{business_trip_this_week}</td><td><p></p></td></tr>"
        "<tr><td><strong>외근</strong></td><td><p></p></td><td><p></p></td></tr>"
        "<tr><td><strong>재택</strong></td><td><p></p></td><td><p></p></td></tr>"
        "<tr><td><strong>휴가</strong></td><td><p></p></td><td><p></p></td></tr>"
        "</tbody></table>"
    )


def test_stale_entry_cleared_when_no_current_week_event():
    stale = "<ul><li>08/03-05(월-수) 김도현(출장(군산))</li></ul>"
    html = _table(business_trip_this_week=stale)
    # business_trip 카테고리 자체는 있지만 this_week 이벤트가 없는 상태(신규 _build_vacation_map 동작)
    category_map = {"business_trip": {"this_week": "", "next_week": ""}}

    out = fill_schedule_table_vacations(html, category_map)

    assert stale not in out
    assert "김도현(출장(군산))" not in out


def test_new_event_overwrites_stale_entry():
    stale = "<ul><li>08/03-05(월-수) 김도현(출장(군산))</li></ul>"
    html = _table(business_trip_this_week=stale)
    new_entry = "<ul><li>08/10-11(월-화) 박소영(출장)</li></ul>"
    category_map = {"business_trip": {"this_week": new_entry, "next_week": ""}}

    out = fill_schedule_table_vacations(html, category_map)

    assert stale not in out
    assert new_entry in out


def test_category_not_in_map_leaves_row_untouched():
    stale = "<ul><li>08/03-05(월-수) 김도현(출장(군산))</li></ul>"
    html = _table(business_trip_this_week=stale)
    # business_trip 이 category_map 에 아예 없으면(조회 실패 등) 손대지 않음
    category_map = {"remote": {"this_week": "<ul><li>x</li></ul>", "next_week": ""}}

    out = fill_schedule_table_vacations(html, category_map)

    assert stale in out


def test_empty_category_map_returns_html_unchanged():
    html = _table(business_trip_this_week="<ul><li>x</li></ul>")
    assert fill_schedule_table_vacations(html, {}) == html


def test_next_week_cleared_independently_of_this_week():
    html = (
        "<p>일정 공유</p>"
        '<table><tbody><tr><th /><th>이번 주</th><th>다음 주</th></tr>'
        "<tr><td><strong>재택</strong></td>"
        "<td><ul><li>this week entry</li></ul></td>"
        "<td><ul><li>stale next week entry</li></ul></td></tr>"
        "</tbody></table>"
    )
    category_map = {"remote": {"this_week": "<ul><li>this week entry</li></ul>", "next_week": ""}}

    out = fill_schedule_table_vacations(html, category_map)

    assert "this week entry" in out
    assert "stale next week entry" not in out
