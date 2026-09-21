"""팀 연속 추가 시 카드 크기 회귀 방지 + 팀 제안/드롭다운 제거."""

from __future__ import annotations

import json

CHAT_CARD_LIMIT_BYTES = 32 * 1024


def _members() -> list[dict]:
    out: list[dict] = []
    for team, n in (("ERP2", 8), ("PC2", 9), ("MES2", 7)):
        for i in range(n):
            out.append(
                {
                    "name": f"{team}멤버{i}",
                    "email": f"{team.lower()}.member{i}@vntgcorp.com",
                    "nickname": [],
                    "team_id": team,
                    "team_name": f"{team}팀",
                }
            )
    return out


def _mock_network(monkeypatch, handler, members):
    monkeypatch.setattr(handler, "get_all_members", lambda: members)
    monkeypatch.setattr(handler, "get_rooms", lambda: [])
    monkeypatch.setattr(handler, "is_oauth_linked", lambda email: False)
    monkeypatch.setattr(handler, "detect_office_for_date", lambda email, date: "")
    monkeypatch.setattr(
        handler, "_calendar_options", lambda chat_event, linked=None: [{"id": "primary", "label": "내 캘린더"}]
    )


def _add_button_params(card: dict) -> dict[str, str]:
    """카드에서 '+ 추가' 버튼의 파라미터 — 다음 클릭을 흉내낸다."""
    def walk(o):
        if isinstance(o, dict):
            if o.get("text") == "+ 추가" and "onClick" in o:
                yield o["onClick"]["action"]["parameters"]
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    plist = next(walk(card))
    return {p["key"]: p["value"] for p in plist}


def test_adding_three_teams_in_a_row_stays_under_chat_card_limit(monkeypatch):
    from domains.schedule_management import handler

    members = _members()
    _mock_network(monkeypatch, handler, members)
    state = handler.compose_state_from({"compose_step": "full"}, {})
    params = handler.state_to_button_params(state)
    for team in ("ERP2팀", "PC2팀", "MES2팀 (팀 전원 추가)"):
        out = handler.handle_schedule_management_action(
            invoked_function="sm_compose_add_attendee",
            parameters=params,
            form_inputs={"attendee_input": {"stringInputs": {"value": [team]}}},
            chat_event={"user": {"email": "me@x.com"}},
        )
        size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        assert size < CHAT_CARD_LIMIT_BYTES, f"{team} 추가 후 카드 {size}B"
        params = _add_button_params(out)
    text = json.dumps(out, ensure_ascii=False)
    assert "추가된 참석자 24명" in text
    assert "mes2.member6@vntgcorp.com" in text


def test_team_suggestions_come_first():
    from domains.schedule_management.cards import _member_suggestion_items

    items = [i["text"] for i in _member_suggestion_items(_members())]
    assert items[:3] == ["ERP2팀 (팀 전원 추가)", "PC2팀 (팀 전원 추가)", "MES2팀 (팀 전원 추가)"]
    assert "ERP2멤버0 (erp2.member0@vntgcorp.com)" in items


def test_remove_attendee_via_dropdown_form_value(monkeypatch):
    from domains.schedule_management import handler

    members = _members()
    _mock_network(monkeypatch, handler, members)
    state = handler.compose_state_from({"compose_step": "full"}, {})
    state["attendees"] = [{"name": "A", "email": "a@x.com"}, {"name": "B", "email": "b@x.com"}]
    out = handler.handle_schedule_management_action(
        invoked_function="sm_compose_remove_attendee_email",
        parameters=handler.state_to_button_params(state),
        form_inputs={"remove_attendee_email": {"stringInputs": {"value": ["a@x.com"]}}},
        chat_event={"user": {"email": "me@x.com"}},
    )
    text = json.dumps(out, ensure_ascii=False)
    assert "b@x.com" in text and "a@x.com" not in text
