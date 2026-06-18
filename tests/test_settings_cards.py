"""설정 트리 카드 단위 테스트."""

from __future__ import annotations

from typing import Any


def _buttons(card: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sections = card["cardsV2"][0]["card"]["sections"]
    for section in sections:
        for widget in section.get("widgets") or []:
            if "buttonList" in widget:
                out.extend(widget["buttonList"]["buttons"])
            cols = widget.get("columns") or {}
            for col in cols.get("columnItems") or []:
                for w in col.get("widgets") or []:
                    if "buttonList" in w:
                        out.extend(w["buttonList"]["buttons"])
    return out


def _all_widgets(card: dict[str, Any]) -> list[dict[str, Any]]:
    sections = card["cardsV2"][0]["card"]["sections"]
    out: list[dict[str, Any]] = []
    for section in sections:
        out.extend(section.get("widgets") or [])
    return out


def _divider_count(card: dict[str, Any]) -> int:
    return sum(1 for w in _all_widgets(card) if "divider" in w)


def _paragraph_texts(card: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for w in _all_widgets(card):
        para = w.get("textParagraph") or {}
        if para.get("text"):
            texts.append(str(para["text"]))
    return texts


def _text_inputs(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [w["textInput"] for w in _all_widgets(card) if "textInput" in w]


def test_settings_hub_has_three_branches():
    from domains.settings.cards import build_settings_hub_card

    card = build_settings_hub_card()
    functions = [b["onClick"]["action"]["function"] for b in _buttons(card)]
    assert "st_open_personal" in functions
    assert "st_open_team" in functions
    assert "st_open_rooms" in functions
    assert "hm_open_menu" in functions
    assert card["cardsV2"][0]["cardId"] == "st_settings_hub"


def test_personal_card_shows_linked_status(monkeypatch):
    from domains.daily_chat import home_menu
    from domains.settings.cards import build_personal_settings_card

    monkeypatch.setattr(
        home_menu, "get_token", lambda email: {"status": "linked", "refresh_token": "x"}
    )
    card = build_personal_settings_card(user_email="user@vntgcorp.com")

    assert any("연결되어 있어요" in t for t in _paragraph_texts(card))
    # 버튼은 상태와 무관하게 그대로 유지 — 재연결 경로 보존.
    functions = [b["onClick"]["action"]["function"] for b in _buttons(card)]
    assert "st_oauth_link" in functions


def test_personal_card_shows_connect_prompt_when_unlinked(monkeypatch):
    from domains.daily_chat import home_menu
    from domains.settings.cards import build_personal_settings_card

    monkeypatch.setattr(home_menu, "get_token", lambda email: None)
    card = build_personal_settings_card(user_email="user@vntgcorp.com")

    assert any("연결합니다" in t for t in _paragraph_texts(card))
    functions = [b["onClick"]["action"]["function"] for b in _buttons(card)]
    assert "st_oauth_link" in functions


def test_team_settings_card_has_four_dividers():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {"team_name": "PC2팀", "team_members": [], "shared_drive_ids": [], "calendar_id": "primary"},
        team_id="PC2",
    )
    assert _divider_count(card) == 4
    paragraphs = _paragraph_texts(card)
    assert sum(1 for t in paragraphs if "팀원" in t and "<b>" in t) == 1
    assert sum(1 for t in paragraphs if "컨플루언스" in t and "<b>" in t) == 1
    assert sum(1 for t in paragraphs if "팀 드라이브" in t and "<b>" in t) == 1
    assert sum(1 for t in paragraphs if "팀 캘린더" in t and "<b>" in t) == 1
    assert card["cardsV2"][0]["cardId"] == "st_team_settings"


def test_team_settings_direct_action_buttons():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {"team_name": "PC2팀", "team_members": []},
        team_id="PC2",
    )
    functions = [b["onClick"]["action"]["function"] for b in _buttons(card)]
    assert "st_conf_save" in functions
    assert "st_drive_save" in functions
    assert "st_calendar_save" in functions
    assert "st_calendar_test" in functions
    assert "st_members_mode" in functions
    assert "st_team_meta_open" in functions
    assert "st_section_edit" not in functions
    assert "st_section_view" not in functions


def test_team_settings_member_body_format():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {
            "team_name": "PC2팀",
            "team_members": [
                {"name": "최은비", "nickname": ["Ari", "아리"], "email": "silverain@vntgcorp.com"},
                {"name": "정인", "nickname": [], "email": "lee0930@example.com"},
            ],
        },
        team_id="PC2",
    )
    body = "\n".join(_paragraph_texts(card))
    assert "닉네임:" not in body
    assert "이메일:" not in body
    assert "최은비(Ari, 아리) / silverain@vntgcorp.com" in body
    assert "정인 / lee0930@example.com" in body


def test_team_settings_edit_mode_prefills_draft():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {"team_name": "PC2팀", "team_members": [{"name": "최은비", "nickname": ["Ari"], "email": "a@x.com"}]},
        team_id="PC2",
        member_mode="edit",
        member_edit_index="1",
        member_edit_name="최은비",
        member_edit_nicknames="Ari",
        member_edit_email="a@x.com",
    )
    inputs = {ti["name"]: ti.get("value", "") for ti in _text_inputs(card)}
    assert inputs["member_index"] == "1"
    assert inputs["new_member_name"] == "최은비"
    assert inputs["new_member_nicknames"] == "Ari"
    assert inputs["new_member_email"] == "a@x.com"
    body = "\n".join(_paragraph_texts(card))
    assert "현재:" in body
    assert "최은비(Ari) / a@x.com" in body


def test_team_settings_reorder_mode_has_up_down():
    from domains.settings.cards import build_team_settings_card

    card = build_team_settings_card(
        [{"id": "PC2", "name": "PC2팀"}],
        {
            "team_name": "PC2팀",
            "team_members": [
                {"name": "A", "nickname": [], "email": ""},
                {"name": "B", "nickname": [], "email": ""},
            ],
        },
        team_id="PC2",
        member_mode="reorder",
        member_reorder_index="2",
    )
    functions = [b["onClick"]["action"]["function"] for b in _buttons(card)]
    assert "st_members_move" in functions
    assert "st_members_reorder" not in functions
    input_names = [ti["name"] for ti in _text_inputs(card)]
    assert "member_order" not in input_names
    body = "\n".join(_paragraph_texts(card))
    assert "위 목록의 번호 기준" in body
    inputs = {ti["name"]: ti.get("value", "") for ti in _text_inputs(card)}
    assert inputs.get("member_index") == "2"


def test_room_region_and_list_cards():
    from domains.settings.cards import build_room_list_card, build_room_region_card

    region = build_room_region_card()
    assert "st_rooms_view_gunsan" in [b["onClick"]["action"]["function"] for b in _buttons(region)]

    rooms = build_room_list_card(
        [{"name": "T-Room", "display_name": "T-Room", "capacity": 4, "location": "군산", "equipment": ["모니터"], "calendar_resource_id": "x@r"}],
        region_label="군산",
    )
    text = str(rooms)
    assert "T-Room" in text
    assert rooms["cardsV2"][0]["cardId"] == "st_room_list"
