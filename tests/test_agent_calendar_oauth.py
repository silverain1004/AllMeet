"""에이전트 캘린더 도구의 OAuth 인증 배선 — '내 캘린더'(개인) 접근 회귀 방지.

개인 캘린더는 요청자 OAuth 토큰(get_user_access_token)+calendar_id='primary',
미연결이면 auth_required, 팀/공유 캘린더는 봇 SA(access_token=None) 유지.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from domains.schedule_management.calendar_client import CalendarResult

LINKED = "domains.schedule_management.oauth_calendar.is_oauth_linked"
TOKEN = "domains.schedule_management.oauth_calendar.get_user_access_token"


def _capture(result: CalendarResult):
    box: dict[str, Any] = {}

    def _fn(**kw):
        box.update(kw)
        return result

    return box, _fn


def test_is_personal_calendar_helper():
    from domains.agent.registry import _is_personal_calendar

    assert _is_personal_calendar("primary", "u@x.com")
    assert _is_personal_calendar("", "u@x.com")  # 미지정 + 요청자 → 본인
    assert _is_personal_calendar("u@x.com", "u@x.com")
    assert _is_personal_calendar("내 캘린더", "")
    assert not _is_personal_calendar("team@group.calendar.google.com", "u@x.com")
    assert not _is_personal_calendar("", "")  # 요청자 없으면 미지정은 본인 아님


def test_list_calendar_events_personal_uses_user_token():
    from domains.agent import registry

    box, fn = _capture(CalendarResult(ok=True, events=[{"id": "e1", "summary": "회의"}]))
    with patch(LINKED, return_value=True), patch(TOKEN, return_value="USER_TOK"), patch(
        "domains.schedule_management.calendar_client.list_events", side_effect=fn
    ):
        out = registry._run_list_calendar_events(
            calendar_id="primary",
            time_min="2026-06-23T00:00:00+09:00",
            time_max="2026-06-29T23:59:59+09:00",
            user_email="silverain@vntgcorp.com",
        )
    assert out["ok"] is True
    assert box["calendar_id"] == "primary"
    assert box["access_token"] == "USER_TOK"


def test_list_calendar_events_empty_id_with_email_resolves_primary():
    from domains.agent import registry

    box, fn = _capture(CalendarResult(ok=True, events=[]))
    with patch(LINKED, return_value=True), patch(TOKEN, return_value="USER_TOK"), patch(
        "domains.schedule_management.calendar_client.list_events", side_effect=fn
    ):
        out = registry._run_list_calendar_events(
            time_min="t1", time_max="t2", user_email="u@x.com"
        )
    assert out["ok"] is True
    assert box["calendar_id"] == "primary"
    assert box["access_token"] == "USER_TOK"


def test_list_calendar_events_personal_unlinked_returns_auth_required():
    from domains.agent import registry

    mock_le = MagicMock()
    with patch(LINKED, return_value=False), patch(
        "domains.schedule_management.calendar_client.list_events", mock_le
    ):
        out = registry._run_list_calendar_events(
            calendar_id="primary", time_min="t1", time_max="t2", user_email="u@x.com"
        )
    assert out["ok"] is False
    assert out["error_kind"] == "auth_required"
    mock_le.assert_not_called()  # 인증 실패 시 API 호출 안 함


def test_list_calendar_events_team_calendar_keeps_service_account():
    from domains.agent import registry

    box, fn = _capture(CalendarResult(ok=True, events=[]))
    # 팀/공유 캘린더는 user_email 이 있어도 SA 경로(access_token=None) 유지 — 회귀 방지
    with patch(LINKED) as linked, patch(
        "domains.schedule_management.calendar_client.list_events", side_effect=fn
    ):
        out = registry._run_list_calendar_events(
            calendar_id="team-room@group.calendar.google.com",
            time_min="t1",
            time_max="t2",
            user_email="u@x.com",
        )
    assert out["ok"] is True
    assert box["calendar_id"] == "team-room@group.calendar.google.com"
    assert box["access_token"] is None
    linked.assert_not_called()  # 공유 캘린더는 OAuth 조회조차 안 함


def test_find_free_slots_personal_uses_user_token():
    from domains.agent import registry

    box, fn = _capture(CalendarResult(ok=True, busy={}))
    with patch(LINKED, return_value=True), patch(TOKEN, return_value="USER_TOK"), patch(
        "domains.schedule_management.calendar_client.freebusy_query", side_effect=fn
    ):
        out = registry._run_find_free_slots(
            calendar_ids=["primary"], time_min="t1", time_max="t2", user_email="u@x.com"
        )
    assert out["ok"] is True
    assert box["calendar_ids"] == ["primary"]
    assert box["access_token"] == "USER_TOK"


def test_create_meeting_personal_uses_user_token_and_primary():
    from domains.agent import registry

    box, fn = _capture(CalendarResult(ok=True, created_event={"id": "ev1", "htmlLink": "L"}))
    with patch(LINKED, return_value=True), patch(TOKEN, return_value="USER_TOK"), patch(
        "domains.schedule_management.calendar_client.create_event", side_effect=fn
    ):
        out = registry._run_create_meeting(
            calendar_id="primary",
            summary="동기화 미팅",
            meeting_date="2026-06-25",
            meeting_time="15:00",
            duration_minutes=30,
            user_email="u@x.com",
        )
    assert out["ok"] is True
    assert box["calendar_id"] == "primary"
    assert box["access_token"] == "USER_TOK"
