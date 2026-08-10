"""copy_latest: 기존 페이지면 스킵하지 않고 일정 공유(휴가) 패치."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch


def test_create_by_copy_existing_page_patches_even_without_vacation_events():
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "ERP2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "ERP2",
        "weekly_folder_layout": "single",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    client = MagicMock()
    client.get_page.return_value = {
        "body": {"storage": {"value": "<p>일정 공유</p><table><tr><td>x</td></tr></table>"}}
    }

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 7, 30)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="src1"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value={}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="123"
    ), patch.object(
        pc, "_find_page_in_folder", return_value="exist99"
    ), patch.object(
        pc, "fill_schedule_table_vacations", side_effect=lambda h, m: h
    ) as fill, patch.object(
        pc, "update_week_range_in_schedule_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "update_week_range_in_assignee_table", side_effect=lambda h, a, b: h
    ):
        result = pc._create_by_copy("ERP2", cfg)

    assert "스킵" not in result
    assert "패치" in result
    client.update_page.assert_called_once()
    # vacation_map 비어도 스킵하지 않음 (fill은 호출 안 됨)
    fill.assert_not_called()


def test_create_by_copy_existing_page_refreshes_vacation_map():
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "ERP2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "ERP2",
        "weekly_folder_layout": "single",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    existing_html = "<p>일정 공유</p><table><tr><td>휴가</td><td></td><td></td></tr></table>"
    client = MagicMock()
    client.get_page.return_value = {"body": {"storage": {"value": existing_html}}}
    vac = {"vacation": {"this_week": "<ul><li>x</li></ul>", "next_week": ""}}

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 7, 30)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="src1"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value=vac
    ), patch.object(
        pc, "_resolve_target_folder", return_value="123"
    ), patch.object(
        pc, "_find_page_in_folder", return_value="exist99"
    ), patch.object(
        pc, "fill_schedule_table_vacations", return_value=existing_html + "PATCHED"
    ) as fill, patch.object(
        pc, "update_week_range_in_schedule_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "update_week_range_in_assignee_table", side_effect=lambda h, a, b: h
    ):
        result = pc._create_by_copy("ERP2", cfg)

    assert "일정 공유 갱신" in result
    assert fill.call_count == 2  # 신규 HTML 경로 + 기존 본문 패치
    # 기존 본문 패치가 마지막 호출
    assert fill.call_args_list[-1].args[0] == existing_html
    assert "PATCHED" in client.update_page.call_args.kwargs["html_content"]


def test_create_by_copy_new_page_moved_to_top_when_single_layout():
    """single(flat) 레이아웃: 신규 페이지 생성 시 직전 최신 페이지 앞으로 이동(최신이 항상 위)."""
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "ERP2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "ERP2",
        "weekly_folder_layout": "single",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    client = MagicMock()
    client.create_page.return_value = {"id": "new100"}

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 8, 13)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="old99"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value={}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="123"
    ), patch.object(
        pc, "_find_page_in_folder", return_value=None
    ):
        result = pc._create_by_copy("ERP2", cfg)

    assert "페이지 생성 완료" in result
    client.move_page_position.assert_called_once_with("new100", "before", "old99")


def test_create_by_copy_applies_status_chip_colors_when_label_date_mode():
    """status_chip_mode='label_date': 신규 페이지·기존 페이지 패치 모두 상태 칩 색상 갱신."""
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "ERP2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "ERP2",
        "weekly_folder_layout": "single",
        "status_chip_mode": "label_date",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    client = MagicMock()
    client.get_page.return_value = {
        "body": {"storage": {"value": "<p>일정 공유</p><table><tr><td>x</td></tr></table>"}}
    }

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 7, 30)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="src1"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value={}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="123"
    ), patch.object(
        pc, "_find_page_in_folder", return_value="exist99"
    ), patch.object(
        pc, "fill_schedule_table_vacations", side_effect=lambda h, m: h
    ), patch.object(
        pc, "update_week_range_in_schedule_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "update_week_range_in_assignee_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "apply_status_chip_colors", side_effect=lambda h: h + "|CHIP"
    ) as chip, patch.object(
        pc, "apply_bold_adjacent_status_chips", side_effect=lambda h: h + "|BOLDCHIP"
    ) as bold_chip:
        pc._create_by_copy("ERP2", cfg)

    # 복사한 full_html 경로(신규 생성용) + 기존 페이지 패치 경로, 2번 호출됨
    assert chip.call_count == 2
    assert bold_chip.call_count == 2
    assert "CHIP" in client.update_page.call_args.kwargs["html_content"]


def test_create_by_copy_skips_status_chip_colors_when_mode_unset():
    """status_chip_mode 미설정(MES2 등)이면 상태 칩 색상 로직을 타지 않음."""
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "MES2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "MES2",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    client = MagicMock()
    client.get_page.return_value = {
        "body": {"storage": {"value": "<p>일정 공유</p><table><tr><td>x</td></tr></table>"}}
    }

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 7, 30)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="src1"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value={}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="123"
    ), patch.object(
        pc, "_find_page_in_folder", return_value="exist99"
    ), patch.object(
        pc, "fill_schedule_table_vacations", side_effect=lambda h, m: h
    ), patch.object(
        pc, "update_week_range_in_schedule_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "update_week_range_in_assignee_table", side_effect=lambda h, a, b: h
    ), patch.object(
        pc, "apply_status_chip_colors"
    ) as chip, patch.object(
        pc, "apply_bold_adjacent_status_chips"
    ) as bold_chip:
        pc._create_by_copy("MES2", cfg)

    chip.assert_not_called()
    bold_chip.assert_not_called()


def test_create_by_copy_new_page_skips_reorder_when_not_single():
    """quarter 레이아웃: 신규 페이지 생성 후 순서 재배치를 하지 않음."""
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "MES2팀",
        "root_pages": ["123"],
        "calendar_id": "cal-meeting",
        "vacation_calendar_id": "cal-vacation",
        "confluence_space_key": "MES2",
        "team_members": [{"name": "김도현", "nickname": []}],
    }
    client = MagicMock()
    client.create_page.return_value = {"id": "new100"}

    with patch.object(pc, "_get_root_id", return_value="123"), patch.object(
        pc, "_find_reference_date", return_value=datetime(2026, 8, 13)
    ), patch.object(pc, "ConfluenceClient", return_value=client), patch.object(
        pc, "get_latest_weekly_report_page_id_for_team_root", return_value="old99"
    ), patch.object(
        pc, "fetch_weekly_report_html", return_value="<p>source</p>"
    ), patch.object(
        pc, "_build_vacation_map", return_value={}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="quarter456"
    ), patch.object(
        pc, "_find_page_in_folder", return_value=None
    ):
        pc._create_by_copy("MES2", cfg)

    client.move_page_position.assert_not_called()
