"""ERP2 주간회의: flat 폴더 레이아웃 + 대시 형식 휴가 파서."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from domains.weekly_meeting.page_creator import (
    _parse_vacation_event,
    _resolve_target_folder,
    _resolve_vacation_category,
)


# ---------------------------------------------------------------------------
# 휴가 파서 — PC2/MES2 괄호 형식 (회귀)
# ---------------------------------------------------------------------------

def test_parse_paren_single_name():
    result = _parse_vacation_event("연차(김우상)")
    assert result == [("vacation", ["김우상"], "연차")]


def test_parse_paren_multiple_names():
    result = _parse_vacation_event("출장(김도현, 박소영)")
    assert result == [("business_trip", ["김도현", "박소영"], "출장")]


def test_parse_paren_remote():
    result = _parse_vacation_event("재택(정주현)")
    assert result == [("remote", ["정주현"], "재택")]


def test_parse_empty_and_unknown():
    assert _parse_vacation_event("") == []
    assert _parse_vacation_event("회의 준비") == []
    assert _parse_vacation_event(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 휴가 파서 — ERP2 대시 형식 (실데이터 예시)
# ---------------------------------------------------------------------------

def test_erp2_remote_dash():
    assert _parse_vacation_event("재택 - 정주현") == [("remote", ["정주현"], "재택")]


def test_erp2_trip_with_location_paren():
    # '출장(군산)' 은 괄호 형식이 아니라 종류 쪽에 지역이 붙은 형태
    result = _parse_vacation_event("출장(군산) - 김도현")
    assert result == [("business_trip", ["김도현"], "출장(군산)")]


def test_erp2_trip_no_spaces():
    assert _parse_vacation_event("합정출장-김도현") == [("business_trip", ["김도현"], "합정출장")]


def test_erp2_annual_with_substitute_annotation():
    result = _parse_vacation_event("연차 - 김도현(대체), 박소영(대체)")
    assert len(result) == 1
    cat, names, kind = result[0]
    assert cat == "vacation"
    assert names == ["김도현", "박소영"]
    assert kind == "연차(대체)"


def test_erp2_official_leave():
    assert _parse_vacation_event("공가 - 김도현") == [("vacation", ["김도현"], "공가")]


def test_erp2_childcare_leave():
    assert _parse_vacation_event("아이돌봄 휴가 - 오한빛") == [
        ("vacation", ["오한빛"], "아이돌봄 휴가")
    ]


def test_erp2_half_half_leave():
    assert _parse_vacation_event("반반차휴가 - 김우상") == [
        ("vacation", ["김우상"], "반반차휴가")
    ]


def test_erp2_two_pairs_in_one_title():
    result = _parse_vacation_event("오전반차 - 박소영, 연차 - 임연주")
    assert result == [
        ("vacation", ["박소영"], "오전반차"),
        ("vacation", ["임연주"], "연차"),
    ]


def test_erp2_one_kind_multiple_names():
    assert _parse_vacation_event("연차 - 김도현, 정주현") == [
        ("vacation", ["김도현", "정주현"], "연차")
    ]


def test_erp2_afternoon_half_half():
    assert _parse_vacation_event("오후 반반차 - 박소영") == [
        ("vacation", ["박소영"], "오후 반반차")
    ]


def test_resolve_category_prefix_and_keyword():
    assert _resolve_vacation_category("오후 반반차") == "vacation"
    assert _resolve_vacation_category("합정출장") == "business_trip"
    assert _resolve_vacation_category("출장(군산)") == "business_trip"
    assert _resolve_vacation_category("아이돌봄 휴가") == "vacation"
    assert _resolve_vacation_category("알수없음") is None


# ---------------------------------------------------------------------------
# flat 폴더 레이아웃
# ---------------------------------------------------------------------------

def test_resolve_target_folder_single_returns_root():
    client = MagicMock()
    cfg = {"weekly_folder_layout": "single"}
    assert _resolve_target_folder(client, cfg, "root123", "ERP2팀", datetime(2026, 7, 30)) == "root123"
    client.get_folder.assert_not_called()


def test_resolve_target_folder_default_uses_quarter(monkeypatch):
    client = MagicMock()
    cfg = {}
    called = {}

    def _fake_quarter(c, root_id, team_name, reference_date):
        called["args"] = (root_id, team_name, reference_date)
        return "quarter456"

    monkeypatch.setattr(
        "domains.weekly_meeting.page_creator._get_quarter_folder",
        _fake_quarter,
    )
    result = _resolve_target_folder(
        client, cfg, "root123", "PC2팀", datetime(2026, 7, 30)
    )
    assert result == "quarter456"
    assert called["args"][0] == "root123"
