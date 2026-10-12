"""팀 추가 시 weekly_page_mode 기본값 (MES2 = copy_latest)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from firestore.team_config import DEFAULT_WEEKLY_PAGE_MODE


def test_default_weekly_page_mode_is_copy_latest():
    assert DEFAULT_WEEKLY_PAGE_MODE == "copy_latest"


def test_wm_team_do_add_sets_weekly_page_mode():
    from domains.weekly_meeting.handler import handle_weekly_meeting_action

    saved: list[dict] = []

    def _capture(**kwargs):
        saved.append(kwargs.get("updates") or {})
        return {}

    with patch("domains.weekly_meeting.handler.get_team_config", return_value=None), patch(
        "domains.weekly_meeting.handler.upsert_team_config", side_effect=_capture
    ):
        out = handle_weekly_meeting_action(
            invoked_function="wm_team_do_add",
            parameters={},
            form_inputs={
                "new_team_id": {"stringInputs": {"value": ["ERP2"]}},
                "new_team_name": {"stringInputs": {"value": ["ERP2팀"]}},
            },
            chat_event={
                "space": {"name": "spaces/AAA"},
                "user": {"email": "u@example.com", "displayName": "U"},
            },
        )

    assert "추가되었습니다" in str(out.get("text") or "")
    assert saved
    assert saved[0]["weekly_page_mode"] == "copy_latest"
    assert saved[0]["team_members"] == []
    assert saved[0]["setup_completed"] is False


def test_st_team_do_add_sets_weekly_page_mode():
    from domains.settings.handler import handle_settings_action

    saved: list[dict] = []

    def _capture(**kwargs):
        saved.append(kwargs.get("updates") or {})
        return {}

    with patch("domains.settings.handler.get_team_list", return_value=[]), patch(
        "domains.settings.handler.get_team_config", return_value=None
    ), patch(
        "domains.settings.handler.upsert_team_config", side_effect=_capture
    ), patch(
        "domains.settings.handler._render_team_card", return_value={"text": "ok"}
    ):
        handle_settings_action(
            invoked_function="st_team_do_add",
            parameters={},
            form_inputs={
                "new_team_id": {"stringInputs": {"value": ["ERP2"]}},
                "new_team_name": {"stringInputs": {"value": ["ERP2팀"]}},
            },
            chat_event={
                "space": {"name": "spaces/AAA"},
                "user": {"email": "u@example.com", "displayName": "U"},
            },
        )

    assert saved
    assert saved[0]["weekly_page_mode"] == "copy_latest"


def test_run_weekly_page_job_defaults_missing_mode_to_copy_latest():
    from domains.weekly_meeting.page_creator import run_weekly_page_job

    with patch(
        "firestore.team_config.get_team_config",
        return_value={"team_name": "ERP2팀"},  # weekly_page_mode 없음
    ), patch(
        "firestore.team_config.normalize_team_id",
        side_effect=lambda x: x,
    ), patch(
        "domains.weekly_meeting.page_creator._create_by_copy",
        return_value="copied",
    ) as copy_fn, patch(
        "domains.weekly_meeting.page_creator._create_from_template",
    ) as tpl_fn:
        result = run_weekly_page_job("ERP2")

    assert result == "copied"
    copy_fn.assert_called_once()
    tpl_fn.assert_not_called()


def test_run_weekly_page_job_missed_only_on_inactive_weekday():
    """weekly_active_weekdays 지정 팀(ERP2): 일요일(6) 외엔 누락 페이지 생성만(missed_since) — 기존 페이지 패치 안 함."""
    from datetime import datetime

    from domains.weekly_meeting.page_creator import run_weekly_page_job

    monday = datetime(2026, 8, 17)  # 월요일
    assert monday.weekday() == 0

    with patch(
        "firestore.team_config.get_team_config",
        return_value={
            "team_name": "ERP2팀",
            "weekly_page_mode": "copy_latest",
            "weekly_active_weekdays": [6],  # 일요일만 활성
        },
    ), patch(
        "firestore.team_config.normalize_team_id",
        side_effect=lambda x: x,
    ), patch(
        "domains.weekly_meeting.page_creator.datetime"
    ) as mock_dt, patch(
        "domains.weekly_meeting.page_creator._create_by_copy",
        return_value="copied",
    ) as copy_fn:
        mock_dt.now.return_value = monday
        result = run_weekly_page_job("ERP2")

    assert result == "copied"
    assert copy_fn.call_args.kwargs["missed_since"] == datetime(2026, 8, 16)  # 직전 일요일


def test_create_by_copy_missed_skips_existing_page():
    """비활성 요일: 이번 주 페이지가 이미 있으면 update_page 없이 스킵."""
    from datetime import datetime

    from domains.weekly_meeting import page_creator as pc

    client = MagicMock()
    with patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 10, 15)
    ), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="111"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>x</p>"
    ), patch.object(
        pc, "_find_page_in_folder", return_value="222"
    ):
        result = pc._create_by_copy(
            "ERP2",
            {"team_name": "ERP2팀", "report_root_page_id": "https://x/folder/2116845588",
             "weekly_folder_layout": "single", "space_key": "ERP2", "vacation_calendar_id": ""},
            missed_since=datetime(2026, 10, 11),  # 직전 일요일 > 직전 회의(10/08)
        )

    assert "스킵" in result and "222" in result
    client.update_page.assert_not_called()
    client.create_page.assert_not_called()


def test_create_by_copy_missed_creates_missing_page():
    """비활성 요일: 이번 주 페이지가 없으면 생성 (활성 요일 실행 실패 누락분 보충)."""
    from datetime import datetime

    from domains.weekly_meeting import page_creator as pc

    client = MagicMock()
    client.create_page.return_value = {"id": "333"}
    with patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 10, 15)
    ), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="111"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>x</p>"
    ), patch.object(
        pc, "_find_page_in_folder", return_value=None
    ):
        result = pc._create_by_copy(
            "ERP2",
            {"team_name": "ERP2팀", "report_root_page_id": "https://x/folder/2116845588",
             "weekly_folder_layout": "single", "space_key": "ERP2", "vacation_calendar_id": ""},
            missed_since=datetime(2026, 10, 11),  # 직전 일요일 > 직전 회의(10/08)
        )

    assert "생성 완료" in result
    assert client.create_page.call_args.kwargs["title"] == "2026-10-15 ERP2팀 주간회의"


def test_create_by_copy_waits_for_active_day_after_meeting():
    """ERP2 목요일 회의 직후(기준일=다음 주 목): 직전 일요일이 이번 회의 전이면 생성하지 않고 일요일까지 대기."""
    from datetime import datetime

    from domains.weekly_meeting import page_creator as pc

    client = MagicMock()
    with patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 10, 22)
    ), patch.object(pc, "get_latest_weekly_report_page_id_for_team_root") as latest_fn:
        result = pc._create_by_copy(
            "ERP2",
            {"team_name": "ERP2팀", "report_root_page_id": "https://x/folder/2116845588"},
            missed_since=datetime(2026, 10, 11),  # 직전 일요일 < 이번 회의(10/15)
        )

    assert "스킵" in result
    latest_fn.assert_not_called()
    client.create_page.assert_not_called()


def test_find_page_in_folder_raises_on_lookup_failure():
    """폴더 조회 실패(403 등)를 '페이지 없음'으로 삼키지 않음 → 중복 생성 방지."""
    import pytest

    from domains.weekly_meeting.page_creator import _find_page_in_folder

    client = MagicMock()
    client.get_folder_direct_children.side_effect = RuntimeError("폴더 자식 목록 조회 실패: 403")
    with pytest.raises(RuntimeError):
        _find_page_in_folder(client, "1", "t")


def test_run_weekly_page_job_runs_on_active_weekday():
    from datetime import datetime

    from domains.weekly_meeting.page_creator import run_weekly_page_job

    sunday = datetime(2026, 8, 16)
    assert sunday.weekday() == 6

    with patch(
        "firestore.team_config.get_team_config",
        return_value={
            "team_name": "ERP2팀",
            "weekly_page_mode": "copy_latest",
            "weekly_active_weekdays": [6],
        },
    ), patch(
        "firestore.team_config.normalize_team_id",
        side_effect=lambda x: x,
    ), patch(
        "domains.weekly_meeting.page_creator.datetime"
    ) as mock_dt, patch(
        "domains.weekly_meeting.page_creator._create_by_copy",
        return_value="copied",
    ) as copy_fn:
        mock_dt.now.return_value = sunday
        result = run_weekly_page_job("ERP2")

    assert result == "copied"
    copy_fn.assert_called_once()
    assert copy_fn.call_args.kwargs["missed_since"] is None


def test_run_weekly_page_job_no_weekday_gate_when_unset():
    """weekly_active_weekdays 미설정 팀(MES2/PC2 등): 요일 상관없이 항상 실행."""
    from domains.weekly_meeting.page_creator import run_weekly_page_job

    with patch(
        "firestore.team_config.get_team_config",
        return_value={"team_name": "MES2팀", "weekly_page_mode": "copy_latest"},
    ), patch(
        "firestore.team_config.normalize_team_id",
        side_effect=lambda x: x,
    ), patch(
        "domains.weekly_meeting.page_creator._create_by_copy",
        return_value="copied",
    ) as copy_fn:
        result = run_weekly_page_job("MES2")

    assert result == "copied"
    copy_fn.assert_called_once()
