"""팀설정 — 회의실 지역 기본값 드롭다운/저장."""

from __future__ import annotations

from unittest.mock import patch


def test_team_settings_card_has_room_region_dropdown():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {"team_name": "PC2팀", "team_members": [], "room_region_default": "seoul"},
        team_id="PC2",
    )
    text = str(card)
    assert "회의실 지역 기본값" in text
    widgets = card["cardsV2"][0]["card"]["sections"][0]["widgets"]
    dd = next(w for w in widgets if w.get("selectionInput", {}).get("name") == "room_region_default")
    selected = [i["value"] for i in dd["selectionInput"]["items"] if i.get("selected")]
    assert selected == ["seoul"]
    # 고르는 즉시 저장 — 저장 버튼 대신 onChangeAction
    assert dd["selectionInput"]["onChangeAction"]["function"] == "st_room_region_save"
    assert not any(b.get("text") == "저장" and "st_room_region_save" in str(b) for w in widgets for b in w.get("buttonList", {}).get("buttons", []))
    team_dd = next(w for w in widgets if w.get("selectionInput", {}).get("name") == "team_id")
    assert team_dd["selectionInput"]["onChangeAction"]["function"] == "st_team_apply"
    assert "팀 적용" not in text


def test_st_room_region_save_persists_setting():
    from domains.settings.handler import handle_settings_action

    saved: dict = {}

    def fake_upsert(**kw):
        saved.update(kw)
        return {}

    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]), \
         patch("domains.settings.handler.get_team_config", return_value={"team_name": "PC2팀", "team_members": []}), \
         patch("domains.settings.handler.upsert_team_config", side_effect=fake_upsert):
        out = handle_settings_action(
            invoked_function="st_room_region_save",
            parameters={"team_id": "PC2"},
            form_inputs={
                "team_id": {"stringInputs": {"value": ["PC2"]}},
                "room_region_default": {"stringInputs": {"value": ["gunsan"]}},
            },
            chat_event={"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}},
        )
    assert saved["team_id"] == "PC2"
    assert saved["updates"] == {"room_region_default": "gunsan"}
    assert "회의실 기본 지역" in str(out) and "군산" in str(out)


def test_st_room_region_save_all_clears_setting():
    from domains.settings.handler import handle_settings_action

    saved: dict = {}
    with patch("domains.settings.handler.get_team_list", return_value=[{"id": "PC2", "name": "PC2팀"}]), \
         patch("domains.settings.handler.get_team_config", return_value={"team_name": "PC2팀", "team_members": []}), \
         patch("domains.settings.handler.upsert_team_config", side_effect=lambda **kw: saved.update(kw) or {}):
        handle_settings_action(
            invoked_function="st_room_region_save",
            parameters={"team_id": "PC2"},
            form_inputs={"team_id": {"stringInputs": {"value": ["PC2"]}}, "room_region_default": {"stringInputs": {"value": [""]}}},
            chat_event={"user": {"email": "u@example.com"}, "space": {"name": "spaces/x"}},
        )
    assert saved["updates"] == {"room_region_default": ""}


def test_room_region_default_is_a_team_setting_field():
    from firestore.team_config import TEAM_SETTING_FIELDS, _normalize_team_row

    assert "room_region_default" in TEAM_SETTING_FIELDS
    row = _normalize_team_row({"id": "PC2", "name": "PC2팀", "room_region_default": " seoul "}, team_id="PC2", team_name="PC2팀")
    assert row["room_region_default"] == "seoul"
