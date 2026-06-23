"""설정 트리 카드 UI 빌더."""

from __future__ import annotations

import html
from typing import Any

from domains.weekly_meeting.cards import _team_items


def _wrap_card(
    card_id: str,
    header: dict[str, Any],
    sections: list[dict[str, Any]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": card_id,
                "card": {
                    "header": header,
                    "sections": sections,
                },
            }
        ],
    }
    if include_action_response:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def _action_button(
    text: str,
    function: str,
    *,
    parameters: dict[str, str] | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {"function": function}
    if parameters:
        action["parameters"] = [{"key": k, "value": v} for k, v in parameters.items()]
    return {"text": text, "onClick": {"action": action}}


def _columns_row(left_widget: dict[str, Any], buttons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "columns": {
            "columnItems": [
                {
                    "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                    "verticalAlignment": "CENTER",
                    "widgets": [left_widget],
                },
                {
                    "horizontalSizeStyle": "FILL_MINIMUM_SPACE",
                    "horizontalAlignment": "END",
                    "verticalAlignment": "CENTER",
                    "widgets": [{"buttonList": {"buttons": buttons}}],
                },
            ]
        }
    }


def _back_settings_button() -> dict[str, Any]:
    return {"buttonList": {"buttons": [_action_button("설정으로", "hm_open_settings")]}}


def _back_team_button(**params: str) -> dict[str, Any]:
    return {
        "buttonList": {
            "buttons": [
                _action_button("팀 설정으로", "st_open_team", parameters=params or None),
            ]
        }
    }


def build_settings_hub_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": (
                    "<b>설정</b><br>"
                    "개인 데이터 연결, 팀 설정, 회의실 정보를 선택해 주세요."
                )
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button("개인설정", "st_open_personal"),
                    _action_button("팀설정", "st_open_team"),
                    _action_button("회의실설정", "st_open_rooms"),
                    _action_button("홈으로", "hm_open_menu"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "st_settings_hub",
        {"title": "AllMeet", "subtitle": "설정"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = "설정 메뉴입니다."
    return out


def build_personal_settings_card(
    *, user_email: str = "", include_action_response: bool = False
) -> dict[str, Any]:
    # 이미 연결된 사용자에겐 "연결됨" 상태를, 미연결/해제 상태엔 안내 문구를 보여준다.
    # 버튼은 상태와 무관하게 동일 — 재연결(만료/철회 복구)도 같은 버튼으로 진행.
    from api._auth.user_oauth import verify_oauth_connection

    try:
        linked = bool(user_email) and verify_oauth_connection(user_email)
    except Exception:
        linked = False
    if linked:
        header_text = (
            "<b>개인설정</b><br>"
            "✅ Gmail · 개인 Calendar · 내 Drive가 연결되어 있어요.<br>"
            "<font color=\"#888888\">권한을 새로 받거나 만료·철회 후 복구하려면 아래에서 다시 연결하세요.</font>"
        )
        link_button_label = "🔄 다시 연결 (GWS)"
    else:
        header_text = (
            "<b>개인설정</b><br>"
            "Gmail · 개인 Calendar · 내 Drive를 AllMeet와 연결합니다."
        )
        link_button_label = "내 데이터 연결하기 (GWS)"
    buttons = [_action_button(link_button_label, "st_oauth_link")]
    if linked:
        buttons.append(_action_button("🔓 연결 해지", "st_oauth_unlink"))
    buttons.append(_action_button("설정으로", "hm_open_settings"))
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": header_text}},
        {"buttonList": {"buttons": buttons}},
    ]
    out = _wrap_card(
        "st_personal_settings",
        {"title": "AllMeet", "subtitle": "개인설정"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = "개인설정입니다."
    return out


def build_oauth_unlink_confirm_card(
    *, include_action_response: bool = False
) -> dict[str, Any]:
    """연결 해지 확인 카드 — 실수 방지용 1단계 확인.

    해지하면 Google 측 토큰까지 폐기돼 다시 쓰려면 재동의가 필요하므로 확인을 받는다.
    """
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": (
                    "<b>연결을 해지할까요?</b><br>"
                    "Gmail · 개인 Calendar · 내 Drive 연결이 끊기고, "
                    "주간보고 초안·오늘의 할 일 등 개인 데이터 기반 기능을 쓸 수 없게 돼요.<br>"
                    "<font color=\"#888888\">다시 쓰려면 동의(연결)를 새로 해야 합니다.</font>"
                )
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button("🔓 해지하기", "st_oauth_unlink_confirm"),
                    _action_button("취소", "st_open_personal"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "st_oauth_unlink_confirm",
        {"title": "AllMeet", "subtitle": "연결 해지"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = "연결을 해지할까요?"
    return out


def _divider_block(title: str, widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """구분선 + 블록 제목(1회) + 본문 위젯."""
    return [
        {"divider": {}},
        {"textParagraph": {"text": f"<b>{html.escape(title)}</b>"}},
        *widgets,
    ]


def _members_body_text(members: list[dict[str, Any]]) -> str:
    """1. 최은비(Ari, 아리) / silverain@vntgcorp.com 형식."""
    if not members:
        return "(등록된 팀원이 없습니다)"
    lines: list[str] = []
    for i, member in enumerate(members, start=1):
        name = html.escape(str(member.get("name") or ""))
        raw_nick = member.get("nickname")
        if isinstance(raw_nick, list):
            nicks = [html.escape(str(n).strip()) for n in raw_nick if str(n).strip()]
        else:
            nicks = [html.escape(str(raw_nick).strip())] if str(raw_nick or "").strip() else []
        email = html.escape(str(member.get("email") or "").strip())
        label = name
        if nicks:
            label += f"({', '.join(nicks)})"
        if email:
            label += f" / {email}"
        lines.append(f"{i}. {label}")
    return "<br>".join(lines)


def _root_page_ids_text(root_pages: Any) -> str:
    if not isinstance(root_pages, list):
        return ""
    rows: list[str] = []
    for row in root_pages:
        if isinstance(row, dict) and row.get("page_id"):
            rows.append(str(row.get("page_id")))
    return "\n".join(rows)


def _member_snapshot_line(member: dict[str, Any]) -> str:
    lines = _members_body_text([member])
    return f"현재: {lines}" if lines and not lines.startswith("(") else ""


def build_team_settings_card(
    teams: list[dict[str, str]],
    team_config: dict[str, Any] | None,
    *,
    team_id: str = "",
    member_mode: str = "list",
    member_edit_index: str = "",
    member_edit_name: str = "",
    member_edit_nicknames: str = "",
    member_edit_email: str = "",
    member_reorder_index: str = "",
    calendar_test_message: str = "",
    status_message: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    team_name = str((team_config or {}).get("team_name") or team_id or "팀")
    members = team_config.get("team_members") if team_config else []
    if not isinstance(members, list):
        members = []

    member_params = {"team_id": team_id}

    widgets: list[dict[str, Any]] = []
    if status_message:
        widgets.append({"textParagraph": {"text": status_message}})
    widgets.extend(
        [
            _columns_row(
                {"textParagraph": {"text": "<b>팀 선택</b>"}},
                [_action_button("팀 관리", "st_team_meta_open", parameters={"team_id": team_id})],
            ),
            {
                "selectionInput": {
                    "name": "team_id",
                    "label": "팀",
                    "type": "DROPDOWN",
                    "items": _team_items(teams, selected_team_id=team_id),
                }
            },
            {
                "buttonList": {
                    "buttons": [
                        _action_button("팀 적용", "st_team_apply"),
                    ]
                }
            },
        ]
    )

    # --- 팀원 ---
    member_block: list[dict[str, Any]] = []
    if team_config:
        member_block.append({"textParagraph": {"text": _members_body_text(members)}})
        member_block.append(
            {
                "buttonList": {
                    "buttons": [
                        _action_button("추가", "st_members_mode", parameters={**member_params, "member_mode": "add"}),
                        _action_button("수정", "st_members_mode", parameters={**member_params, "member_mode": "edit"}),
                        _action_button("삭제", "st_members_mode", parameters={**member_params, "member_mode": "delete"}),
                        _action_button("순서 변경", "st_members_mode", parameters={**member_params, "member_mode": "reorder"}),
                    ]
                }
            }
        )
        if member_mode == "add":
            member_block.extend(
                [
                    {"textInput": {"name": "member_name", "label": "팀원 이름"}},
                    {"textInput": {"name": "member_nicknames", "label": "닉네임 (쉼표 구분)"}},
                    {"textInput": {"name": "member_email", "label": "이메일"}},
                    {
                        "buttonList": {
                            "buttons": [
                                _action_button("추가", "st_members_add"),
                                _action_button("취소", "st_members_cancel", parameters=member_params),
                            ]
                        }
                    },
                ]
            )
        elif member_mode == "edit":
            edit_widgets: list[dict[str, Any]] = [
                {
                    "textInput": {
                        "name": "member_index",
                        "label": "팀원 번호 (1부터)",
                        "value": member_edit_index,
                    }
                },
                {"buttonList": {"buttons": [_action_button("불러오기", "st_members_load_edit")]}},
            ]
            if member_edit_index and (member_edit_name or member_edit_nicknames or member_edit_email):
                snapshot_member = {
                    "name": member_edit_name,
                    "nickname": [n.strip() for n in member_edit_nicknames.split(",") if n.strip()],
                    "email": member_edit_email,
                }
                snapshot = _member_snapshot_line(snapshot_member)
                if snapshot:
                    edit_widgets.append({"textParagraph": {"text": snapshot}})
            edit_widgets.extend(
                [
                    {
                        "textInput": {
                            "name": "new_member_name",
                            "label": "새 이름 (비우면 유지)",
                            "value": member_edit_name,
                        }
                    },
                    {
                        "textInput": {
                            "name": "new_member_nicknames",
                            "label": "새 닉네임 (쉼표 구분, 비우면 유지)",
                            "value": member_edit_nicknames,
                        }
                    },
                    {
                        "textInput": {
                            "name": "new_member_email",
                            "label": "새 이메일 (비우면 유지)",
                            "value": member_edit_email,
                        }
                    },
                    {
                        "buttonList": {
                            "buttons": [
                                _action_button("수정", "st_members_edit"),
                                _action_button("취소", "st_members_cancel", parameters=member_params),
                            ]
                        }
                    },
                ]
            )
            member_block.extend(edit_widgets)
        elif member_mode == "delete":
            member_block.extend(
                [
                    {"textInput": {"name": "member_index", "label": "삭제할 팀원 번호"}},
                    {
                        "buttonList": {
                            "buttons": [
                                _action_button("삭제", "st_members_delete"),
                                _action_button("취소", "st_members_cancel", parameters=member_params),
                            ]
                        }
                    },
                ]
            )
        elif member_mode == "reorder":
            member_block.extend(
                [
                    {
                        "textParagraph": {
                            "text": (
                                "위 목록의 번호 기준입니다. "
                                "이동할 팀원 번호를 입력한 뒤 <b>위로</b> 또는 <b>아래로</b>를 누르세요."
                            )
                        }
                    },
                    {
                        "textInput": {
                            "name": "member_index",
                            "label": "팀원 번호 (1부터)",
                            "value": member_reorder_index,
                        }
                    },
                    {
                        "buttonList": {
                            "buttons": [
                                _action_button(
                                    "위로",
                                    "st_members_move",
                                    parameters={**member_params, "direction": "up"},
                                ),
                                _action_button(
                                    "아래로",
                                    "st_members_move",
                                    parameters={**member_params, "direction": "down"},
                                ),
                                _action_button("취소", "st_members_cancel", parameters=member_params),
                            ]
                        }
                    },
                ]
            )
    else:
        member_block.append({"textParagraph": {"text": "팀을 선택해 주세요."}})
    widgets.extend(_divider_block("팀원", member_block))

    # --- 컨플루언스 ---
    conf_block: list[dict[str, Any]] = []
    if team_config:
        root_pages = team_config.get("root_pages") or []
        conf_block.extend(
            [
                {
                    "textInput": {
                        "name": "confluence_space_key",
                        "label": "스페이스 키",
                        "value": str(team_config.get("confluence_space_key") or ""),
                    }
                },
                {
                    "textInput": {
                        "name": "root_page_ids",
                        "label": "폴더 구조 (page ID, 줄바꿈)",
                        "type": "MULTIPLE_LINE",
                        "value": _root_page_ids_text(root_pages),
                    }
                },
                {
                    "textInput": {
                        "name": "template_page_url",
                        "label": "템플릿 URL 또는 Page ID",
                        "value": str(team_config.get("template_page_url") or ""),
                    }
                },
                {"buttonList": {"buttons": [_action_button("저장", "st_conf_save")]}},
            ]
        )
    else:
        conf_block.append({"textParagraph": {"text": "팀을 선택해 주세요."}})
    widgets.extend(_divider_block("컨플루언스", conf_block))

    # --- 팀 드라이브 ---
    drive_ids = team_config.get("shared_drive_ids") if team_config else []
    if not isinstance(drive_ids, list):
        drive_ids = []
    drive_block: list[dict[str, Any]] = []
    if team_config:
        drive_block.extend(
            [
                {
                    "textInput": {
                        "name": "shared_drive_ids",
                        "label": "Shared Drive ID (줄바꿈/쉼표)",
                        "type": "MULTIPLE_LINE",
                        "value": ", ".join(str(x) for x in drive_ids if str(x).strip()),
                    }
                },
                {"buttonList": {"buttons": [_action_button("저장", "st_drive_save")]}},
            ]
        )
    else:
        drive_block.append({"textParagraph": {"text": "팀을 선택해 주세요."}})
    widgets.extend(_divider_block("팀 드라이브", drive_block))

    # --- 팀 캘린더 ---
    calendar_id = str((team_config or {}).get("calendar_id") or "")
    cal_block: list[dict[str, Any]] = []
    if team_config:
        cal_block.append(
            {
                "textInput": {
                    "name": "calendar_id",
                    "label": "Calendar ID",
                    "value": calendar_id,
                }
            }
        )
        if calendar_test_message:
            cal_block.append({"textParagraph": {"text": calendar_test_message}})
        cal_block.append(
            {
                "buttonList": {
                    "buttons": [
                        _action_button("저장", "st_calendar_save"),
                        _action_button("연동테스트", "st_calendar_test"),
                    ]
                }
            }
        )
    else:
        cal_block.append({"textParagraph": {"text": "팀을 선택해 주세요."}})
    widgets.extend(_divider_block("팀 캘린더", cal_block))

    widgets.append(_back_settings_button())

    out = _wrap_card(
        "st_team_settings",
        {"title": "AllMeet", "subtitle": "팀설정"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = f"{team_name} 팀 설정입니다."
    return out


def build_team_meta_card(
    teams: list[dict[str, str]],
    *,
    mode: str = "menu",
    team_id: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = [{"textParagraph": {"text": "<b>팀 관리</b>"}}]

    if mode == "menu":
        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        _action_button("팀 추가", "st_team_meta_mode", parameters={"team_meta_mode": "add"}),
                        _action_button("팀 수정", "st_team_meta_mode", parameters={"team_meta_mode": "edit"}),
                        _action_button("팀 삭제", "st_team_meta_mode", parameters={"team_meta_mode": "delete"}),
                        _action_button("팀 설정으로", "st_open_team", parameters={"team_id": team_id}),
                    ]
                }
            }
        )
    elif mode == "add":
        widgets.extend(
            [
                {"textInput": {"name": "new_team_id", "label": "팀 ID (영문/숫자)"}},
                {"textInput": {"name": "new_team_name", "label": "팀 이름"}},
                {
                    "buttonList": {
                        "buttons": [
                            _action_button("추가", "st_team_do_add"),
                            _action_button("취소", "st_team_meta_open", parameters={"team_id": team_id}),
                        ]
                    }
                },
            ]
        )
    elif mode == "edit":
        widgets.extend(
            [
                {
                    "selectionInput": {
                        "name": "team_id",
                        "label": "팀",
                        "type": "DROPDOWN",
                        "items": _team_items(teams, selected_team_id=team_id),
                    }
                },
                {"buttonList": {"buttons": [_action_button("불러오기", "st_team_load_edit")]}},
                {"textInput": {"name": "new_team_name", "label": "새 팀 이름"}},
                {"textInput": {"name": "calendar_id", "label": "Calendar ID (선택)"}},
                {
                    "buttonList": {
                        "buttons": [
                            _action_button("수정", "st_team_do_edit"),
                            _action_button("취소", "st_team_meta_open", parameters={"team_id": team_id}),
                        ]
                    }
                },
            ]
        )
    elif mode == "delete":
        widgets.extend(
            [
                {
                    "selectionInput": {
                        "name": "team_id",
                        "label": "팀",
                        "type": "DROPDOWN",
                        "items": _team_items(teams, selected_team_id=team_id),
                    }
                },
                {"textParagraph": {"text": "팀 삭제 시 팀원도 함께 삭제됩니다."}},
                {
                    "buttonList": {
                        "buttons": [
                            _action_button("삭제", "st_team_do_delete"),
                            _action_button("취소", "st_team_meta_open", parameters={"team_id": team_id}),
                        ]
                    }
                },
            ]
        )

    out = _wrap_card(
        "st_team_meta",
        {"title": "AllMeet", "subtitle": "팀 관리"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = "팀 관리입니다."
    return out


def build_room_region_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": "<b>회의실설정</b><br>지역을 선택하면 등록된 회의실 정보를 조회할 수 있습니다."
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button("군산", "st_rooms_view_gunsan", parameters={"region": "gunsan"}),
                    _action_button("설정으로", "hm_open_settings"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "st_room_regions",
        {"title": "AllMeet", "subtitle": "회의실설정"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = "회의실설정입니다."
    return out


def build_room_list_card(
    rooms: list[dict[str, Any]],
    *,
    region_label: str = "군산",
    include_action_response: bool = False,
) -> dict[str, Any]:
    lines: list[str] = [f"<b>{html.escape(region_label)} 회의실 ({len(rooms)}개)</b>"]
    if not rooms:
        lines.append("(등록된 회의실이 없습니다)")
    else:
        for i, room in enumerate(rooms, start=1):
            name = html.escape(str(room.get("display_name") or room.get("name") or "-"))
            cap = int(room.get("capacity") or 0)
            loc = html.escape(str(room.get("location") or "-"))
            equip = room.get("equipment") or []
            equip_s = ", ".join(html.escape(str(e)) for e in equip) if equip else "-"
            cal_id = html.escape(str(room.get("calendar_resource_id") or "-"))
            lines.append(
                f"{i}. <b>{name}</b> ({cap}인)<br>"
                f"&nbsp;&nbsp;위치: {loc}<br>"
                f"&nbsp;&nbsp;장비: {equip_s}<br>"
                f"&nbsp;&nbsp;리소스 ID: {cal_id}"
            )

    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<br>".join(lines)}},
        {
            "buttonList": {
                "buttons": [
                    _action_button("지역 선택으로", "st_open_rooms"),
                    _action_button("설정으로", "hm_open_settings"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "st_room_list",
        {"title": "AllMeet", "subtitle": f"회의실 · {region_label}"},
        [{"widgets": widgets}],
        include_action_response=include_action_response,
    )
    out["text"] = f"{region_label} 회의실 목록입니다."
    return out
