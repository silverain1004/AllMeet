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


def test_run_weekly_page_job_skips_on_inactive_weekday():
    """weekly_active_weekdays 지정 팀(ERP2): 일요일(6) 외엔 스킵, Confluence 호출 없음."""
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
        "domains.weekly_meeting.page_creator._create_by_copy"
    ) as copy_fn:
        mock_dt.now.return_value = monday
        result = run_weekly_page_job("ERP2")

    assert "스킵" in result
    copy_fn.assert_not_called()


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
