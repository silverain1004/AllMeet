"""설정 트리 핸들러 단위 테스트."""

from __future__ import annotations

from unittest.mock import patch


def test_st_open_team_renders_card():
    from domains.settings.handler import handle_settings_action

    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]):
        with patch(
            "domains.settings.handler.get_team_config",
            return_value={"team_name": "PC2팀", "team_members": [], "calendar_id": "primary"},
        ):
            out = handle_settings_action(
                invoked_function="st_open_team",
                parameters={},
                form_inputs={},
                chat_event={"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}},
            )
    assert out["cardsV2"][0]["cardId"] == "st_team_settings"


def test_st_calendar_test_shows_success_message():
    from domains.settings.handler import handle_settings_action

    class _Result:
        ok = True
        error_kind = ""
        events = [{"start": "2026-06-10T09:00:00+09:00"}]

    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]):
        with patch(
            "domains.settings.handler.get_team_config",
            return_value={"team_name": "PC2팀", "calendar_id": "cal@group.calendar.google.com"},
        ):
            with patch("domains.settings.handler.lookup_weekly_meeting", return_value=_Result()):
                out = handle_settings_action(
                    invoked_function="st_calendar_test",
                    parameters={"team_id": "PC2"},
                    form_inputs={"team_id": {"stringInputs": {"value": ["PC2"]}}},
                    chat_event={"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}},
                )
    body = str(out)
    assert "정상적으로 연결되었습니다" in body
    assert "2026-06-10" in body


def test_legacy_wm_open_team_menu_redirects():
    from domains.weekly_meeting.handler import handle_weekly_meeting_action

    with patch("domains.settings.handler.get_team_list", return_value=[]):
        with patch("domains.settings.handler.get_team_config", return_value=None):
            out = handle_weekly_meeting_action(
                invoked_function="wm_open_team_menu",
                parameters={},
                form_inputs={},
                chat_event={"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}},
            )
    assert out["cardsV2"][0]["cardId"] == "st_team_settings"


def test_home_menu_intent_triggers_home_card():
    from main import UserIntent, match_user_intent

    with patch("main.classify_intent", return_value="home_menu"):
        assert match_user_intent("홈 메뉴") == UserIntent.HOME_MENU
        assert match_user_intent("안녕") == UserIntent.HOME_MENU
        assert match_user_intent("너 뭐할수있어") == UserIntent.HOME_MENU


def _chat_event():
    return {"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}}


def _form_team(team_id: str = "PC2") -> dict:
    return {"team_id": {"stringInputs": {"value": [team_id]}}}


def test_st_members_load_edit_prefills_form():
    from domains.settings.handler import handle_settings_action

    members = [
        {"name": "최은비", "nickname": ["Ari", "아리"], "email": "silverain@vntgcorp.com"},
        {"name": "정인", "nickname": [], "email": "lee@example.com"},
    ]
    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]):
        with patch(
            "domains.settings.handler.get_team_config",
            return_value={"team_name": "PC2팀", "team_members": members},
        ):
            out = handle_settings_action(
                invoked_function="st_members_load_edit",
                parameters={"team_id": "PC2"},
                form_inputs={**_form_team(), "member_index": {"stringInputs": {"value": ["2"]}}},
                chat_event=_chat_event(),
            )
    card = out["cardsV2"][0]["card"]
    widgets = card["sections"][0]["widgets"]
    inputs = {w["textInput"]["name"]: w["textInput"].get("value", "") for w in widgets if "textInput" in w}
    assert inputs["member_index"] == "2"
    assert inputs["new_member_name"] == "정인"
    assert inputs["new_member_email"] == "lee@example.com"


def test_st_members_move_down_reorders():
    from domains.settings.handler import handle_settings_action

    members = [
        {"name": "A", "nickname": [], "email": ""},
        {"name": "B", "nickname": [], "email": ""},
        {"name": "C", "nickname": [], "email": ""},
    ]
    saved: list[dict] = []

    def _capture_upsert(**kwargs):
        saved.append(kwargs.get("updates") or {})

    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]):
        with patch(
            "domains.settings.handler.get_team_config",
            return_value={"team_name": "PC2팀", "team_members": members},
        ):
            with patch("domains.settings.handler.upsert_team_config", side_effect=_capture_upsert):
                handle_settings_action(
                    invoked_function="st_members_move",
                    parameters={"team_id": "PC2", "direction": "down"},
                    form_inputs={**_form_team(), "member_index": {"stringInputs": {"value": ["2"]}}},
                    chat_event=_chat_event(),
                )
    assert saved
    reordered = saved[-1]["team_members"]
    assert [m["name"] for m in reordered] == ["A", "C", "B"]


def test_st_members_move_up_at_top_shows_message():
    from domains.settings.handler import handle_settings_action

    members = [{"name": "A", "nickname": [], "email": ""}]
    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]):
        with patch(
            "domains.settings.handler.get_team_config",
            return_value={"team_name": "PC2팀", "team_members": members},
        ):
            with patch("domains.settings.handler.upsert_team_config") as mock_upsert:
                out = handle_settings_action(
                    invoked_function="st_members_move",
                    parameters={"team_id": "PC2", "direction": "up"},
                    form_inputs={**_form_team(), "member_index": {"stringInputs": {"value": ["1"]}}},
                    chat_event=_chat_event(),
                )
    mock_upsert.assert_not_called()
    assert "이미 맨 위" in str(out)
