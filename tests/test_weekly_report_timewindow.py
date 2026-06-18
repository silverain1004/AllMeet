"""주간보고초안 회의 일자 산정 — 공유 캘린더에서 본인 팀 회의로 좁히기."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from domains.weekly_report.timewindow import find_meeting_date

KST = timezone(timedelta(hours=9))


def _ev(summary: str, start: str) -> dict:
    return {"summary": summary, "start": start}


# 공유 캘린더 — 여러 팀의 주간회의가 섞여 있는 상황.
# 기준: 2026-06-18 (목). PC2팀 회의는 06-25 (목), 남의 팀 회의는 06-22 (월).
_REF = datetime(2026, 6, 18, 9, 0, tzinfo=KST)
_EVENTS = [
    _ev("제조사업2센터 주간회의", "2026-06-22T10:00:00+09:00"),
    _ev("MES팀 주간회의", "2026-06-22T11:00:00+09:00"),
    _ev("PC2팀 주간회의", "2026-06-25T09:00:00+09:00"),
]


def test_team_name_picks_own_meeting_not_nearest():
    """팀명으로 좁히면 더 가까운 남의 팀 회의 대신 본인 팀 회의를 고른다."""
    found = find_meeting_date(_EVENTS, reference=_REF, team_name="PC2팀")
    assert found is not None
    assert found.strftime("%Y-%m-%d") == "2026-06-25"


def test_no_team_name_picks_nearest_future():
    """팀명 없으면 종전 동작 — 가장 가까운 미래 회의."""
    found = find_meeting_date(_EVENTS, reference=_REF)
    assert found is not None
    assert found.strftime("%Y-%m-%d") == "2026-06-22"


def test_whitespace_insensitive_title_match():
    """제목 공백 변형도 매칭."""
    events = [_ev("PC2팀  주간 회의", "2026-06-25T09:00:00+09:00")]
    found = find_meeting_date(events, reference=_REF, team_name="PC2팀")
    assert found is not None
    assert found.strftime("%Y-%m-%d") == "2026-06-25"


def test_falls_back_to_title_only_when_team_absent():
    """팀명 일치 이벤트가 없으면 '주간회의' 제목만으로 폴백."""
    events = [_ev("전사 주간회의", "2026-06-22T10:00:00+09:00")]
    found = find_meeting_date(events, reference=_REF, team_name="PC2팀")
    assert found is not None
    assert found.strftime("%Y-%m-%d") == "2026-06-22"


def test_ignores_body_only_match_when_title_lacks_keyword():
    """q 풀텍스트로 잡혀와도 제목에 '주간회의' 없으면 후보에서 제외."""
    events = [
        _ev("PC2팀 스프린트 점검", "2026-06-19T10:00:00+09:00"),  # 본문에만 '주간회의'
        _ev("PC2팀 주간회의", "2026-06-25T09:00:00+09:00"),
    ]
    found = find_meeting_date(events, reference=_REF, team_name="PC2팀")
    assert found is not None
    assert found.strftime("%Y-%m-%d") == "2026-06-25"
