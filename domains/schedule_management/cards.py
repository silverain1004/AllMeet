"""캘린더 예약 카드 UI (compose v2)."""

from __future__ import annotations

import html
from typing import Any

from domains.schedule_management.compose_state import format_date_korean, state_to_button_params


def _wrap_card(
    card_id: str,
    header: dict[str, Any],
    widgets: list[dict[str, Any]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": card_id,
                "card": {
                    "header": header,
                    "sections": [{"widgets": widgets}],
                },
            }
        ],
    }
    if include_action_response:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def _time_options(*, start: str = "06:00", end: str = "22:50", step_min: int = 10) -> list[dict[str, Any]]:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    cursor = sh * 60 + sm
    end_min = eh * 60 + em
    items: list[dict[str, Any]] = [{"text": "(선택)", "value": ""}]
    while cursor <= end_min:
        h, m = divmod(cursor, 60)
        label = f"{h:02d}:{m:02d}"
        items.append({"text": label, "value": label})
        cursor += step_min
    return items


def _params_list(params: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "value": str(v)} for k, v in params.items()]


def build_reservation_menu_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>캘린더 예약</b><br>원하는 작업을 선택해 주세요."}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "회의 예약", "onClick": {"action": {"function": "sm_open_compose"}}},
                    {"text": "캘린더 설정", "onClick": {"action": {"function": "sm_open_settings"}}},
                    {"text": "회의실 동기화", "onClick": {"action": {"function": "sm_sync_rooms"}}},
                ]
            }
        },
    ]
    return _wrap_card(
        "sm_main_menu",
        {"title": "AllMeet", "subtitle": "캘린더 예약"},
        widgets,
        include_action_response=include_action_response,
    )


def build_settings_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    team_items = (
        [{"text": t["name"], "value": t["id"]} for t in teams]
        if teams
        else [{"text": "등록된 팀이 없습니다", "value": "__none__"}]
    )
    widgets = [
        {
            "textParagraph": {
                "text": (
                    "<b>예약 캘린더 설정</b><br>"
                    "개인 예약은 '내 데이터 연결'(OAuth) 후 compose에서 캘린더를 선택하세요.<br>"
                    "팀 공유 캘린더는 아래에서 Calendar ID를 저장합니다."
                )
            }
        },
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": team_items}},
        {"textInput": {"name": "calendar_id", "label": "Calendar ID"}},
        {
            "buttonList": {
                "buttons": [{"text": "저장", "onClick": {"action": {"function": "sm_settings_save_calendar"}}}]
            }
        },
        {
            "buttonList": {
                "buttons": [{"text": "메뉴로", "onClick": {"action": {"function": "sm_open_menu"}}}]
            }
        },
    ]
    return _wrap_card(
        "sm_settings",
        {"title": "AllMeet", "subtitle": "캘린더 설정"},
        widgets,
        include_action_response=include_action_response,
    )


def build_result_card(
    *,
    title: str,
    lines: list[str],
    include_action_response: bool = False,
    cancel_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = "<br>".join(html.escape(line) for line in lines) if lines else "-"
    buttons: list[dict[str, Any]] = [{"text": "메뉴로", "onClick": {"action": {"function": "sm_open_menu"}}}]
    if cancel_params and cancel_params.get("last_event_id"):
        buttons.insert(
            0,
            {
                "text": "예약 취소",
                "onClick": {
                    "action": {
                        "function": "sm_cancel_reservation",
                        "parameters": _params_list(cancel_params),
                    }
                },
            },
        )
        buttons.insert(
            0,
            {
                "text": "일정 변경",
                "onClick": {
                    "action": {
                        "function": "sm_open_compose",
                        "parameters": _params_list(cancel_params),
                    }
                },
            },
        )
    widgets = [
        {"textParagraph": {"text": f"<b>{html.escape(title)}</b><br>{body}"}},
        {"buttonList": {"buttons": buttons}},
    ]
    return _wrap_card(
        "sm_result",
        {"title": "AllMeet", "subtitle": "예약"},
        widgets,
        include_action_response=include_action_response,
    )


def _attendee_chip_buttons(
    state: dict[str, Any],
    base_params: dict[str, str],
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    attendees = state.get("attendees") or []
    if not attendees:
        return widgets
    buttons: list[dict[str, Any]] = []
    for idx, person in enumerate(attendees):
        name = str(person.get("name") or "").strip()
        email = str(person.get("email") or "").strip()
        if name and email:
            label = f"{name}({email}) x"
        elif email:
            label = f"{email} x"
        else:
            label = f"{name} x"
        params = dict(base_params)
        params["remove_index"] = str(idx)
        buttons.append(
            {
                "text": label[:80],
                "onClick": {
                    "action": {
                        "function": "sm_compose_remove_attendee",
                        "parameters": _params_list(params),
                    }
                },
            }
        )
    if buttons:
        widgets.append({"textParagraph": {"text": "<b>추가된 참석자</b>"}})
        widgets.append({"buttonList": {"buttons": buttons}})
    return widgets


def _candidate_buttons(
    candidates: list[dict[str, str]],
    base_params: dict[str, str],
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    buttons = []
    for c in candidates[:5]:
        name = c.get("name", "")
        email = c.get("email", "")
        params = dict(base_params)
        params["pick_email"] = email
        params["pick_name"] = name
        buttons.append(
            {
                "text": f"{name} ({email})" if name else email,
                "onClick": {
                    "action": {
                        "function": "sm_compose_pick_attendee",
                        "parameters": _params_list(params),
                    }
                },
            }
        )
    return [{"textParagraph": {"text": "여러 명이 검색되었습니다. 선택해 주세요."}}, {"buttonList": {"buttons": buttons}}]


def _room_widgets(
    rooms: list[dict[str, Any]],
    state: dict[str, Any],
    base_params: dict[str, str],
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<b>추천 회의실</b> (참석자·일시 기준, 최대 3개)"}}
    ]
    if not rooms:
        widgets.append({"textParagraph": {"text": "(추천할 회의실이 없습니다)"}})
        return widgets
    for room in rooms:
        params = dict(base_params)
        params["picked_room_id"] = str(room.get("id") or "")
        params["picked_room_name"] = str(room.get("name") or "")
        line = html.escape(str(room.get("display_line") or room.get("name") or ""))
        avail = html.escape(str(room.get("availability_label") or ""))
        picked = state.get("picked_room_id") == room.get("id")
        prefix = "✓ " if picked else ""
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": f"{prefix}{html.escape(room.get('name', ''))} — {avail}",
                    "text": line,
                    "wrapText": True,
                    "button": {
                        "text": "선택",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_pick_room",
                                "parameters": _params_list(params),
                            }
                        },
                    },
                }
            }
        )
    return widgets


def build_compose_card(
    state: dict[str, Any],
    *,
    calendar_options: list[dict[str, str]],
    recommended_rooms: list[dict[str, Any]],
    pending_candidates: list[dict[str, str]] | None = None,
    oauth_linked: bool = True,
    oauth_url: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    base_params = state_to_button_params(state)
    errors = state.get("errors") or []
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<b>회의 예약</b><br>항목을 입력한 뒤 확정해 주세요."}}
    ]
    if errors:
        widgets.append(
            {
                "textParagraph": {
                    "text": "<font color=\"#d93025\">" + "<br>".join(html.escape(e) for e in errors) + "</font>"
                }
            }
        )

    if not oauth_linked and oauth_url:
        widgets.append(
            {
                "textParagraph": {
                    "text": (
                        "<font color=\"#d93025\">캘린더가 연결되지 않았습니다.</font><br>"
                        "예약하려면 Google 계정 데이터 연결이 필요합니다."
                    )
                }
            }
        )
        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        {
                            "text": "내 데이터 연결",
                            "onClick": {"openLink": {"url": oauth_url}},
                        }
                    ]
                }
            }
        )

    cal_items = [{"text": c["label"], "value": c["id"]} for c in calendar_options]
    selected_cal = str(state.get("calendar_id") or "")
    for item in cal_items:
        if item["value"] == selected_cal:
            item["selected"] = True

    widgets.append(
        {
            "selectionInput": {
                "name": "calendar_id",
                "label": "캘린더 선택",
                "type": "DROPDOWN",
                "items": cal_items or [{"text": "(없음)", "value": ""}],
            }
        }
    )

    date_summary = format_date_korean(str(state.get("meeting_date") or ""))
    if date_summary:
        widgets.append({"textParagraph": {"text": f"선택 일자: <b>{html.escape(date_summary)}</b>"}})

    time_items = _time_options()
    sel_time = str(state.get("meeting_time") or "")
    for item in time_items:
        if item["value"] == sel_time:
            item["selected"] = True

    widgets.append({"textParagraph": {"text": "<b>회의일시</b>"}})
    widgets.append(
        {
            "dateTimePicker": {
                "name": "meeting_date",
                "label": "날짜",
                "type": "DATE_ONLY",
            }
        }
    )
    widgets.append(
        {
            "selectionInput": {
                "name": "meeting_time",
                "label": "시간 (10분 단위)",
                "type": "DROPDOWN",
                "items": time_items,
            }
        }
    )

    widgets.append({"textInput": {"name": "title", "label": "제목", "value": str(state.get("title") or "")}})

    widgets.append({"textParagraph": {"text": "<b>참석자</b>"}})
    widgets.extend(_attendee_chip_buttons(state, base_params))
    if pending_candidates:
        widgets.extend(_candidate_buttons(pending_candidates, base_params))
    widgets.append({"textInput": {"name": "attendee_input", "label": "이름 또는 이메일"}})
    widgets.append(
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "+ 추가",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_add_attendee",
                                "parameters": _params_list(base_params),
                            }
                        },
                    }
                ]
            }
        }
    )

    want_meet = bool(state.get("want_meet") or state.get("auto_meet"))
    meet_url = str(state.get("meet_url") or "")
    widgets.append({"textParagraph": {"text": "<b>화상회의</b>"}})
    if want_meet and not meet_url:
        widgets.append(
            {
                "textParagraph": {
                    "text": "<i>확정 시 Google Meet 링크가 자동 생성됩니다.</i>"
                }
            }
        )
    meet_row: list[dict[str, Any]] = [
        {
            "text": "+ 추가",
            "onClick": {
                "action": {
                    "function": "sm_compose_create_meet",
                    "parameters": _params_list(base_params),
                }
            },
        },
    ]
    if meet_url:
        meet_row.append(
            {
                "text": "제거",
                "onClick": {
                    "action": {
                        "function": "sm_compose_remove_meet",
                        "parameters": _params_list(base_params),
                    }
                },
            }
        )
    widgets.append({"buttonList": {"buttons": meet_row}})
    if meet_url:
        widgets.append({"textInput": {"name": "meet_url_display", "label": "Meet 링크", "value": meet_url}})
        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        {
                            "text": "링크 열기",
                            "onClick": {"openLink": {"url": meet_url}},
                        }
                    ]
                }
            }
        )

    widgets.extend(_room_widgets(recommended_rooms, state, base_params))

    refresh_params = dict(base_params)
    widgets.append(
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "회의실 다시 찾기",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_refresh",
                                "parameters": _params_list(refresh_params),
                            }
                        },
                    }
                ]
            }
        }
    )

    confirm_params = dict(base_params)
    widgets.append(
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "예약 확정",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_confirm",
                                "parameters": _params_list(confirm_params),
                            }
                        },
                    },
                    {"text": "메뉴로", "onClick": {"action": {"function": "sm_open_menu"}}},
                ]
            }
        }
    )

    return _wrap_card(
        "sm_compose",
        {"title": "AllMeet", "subtitle": "회의 예약"},
        widgets,
        include_action_response=include_action_response,
    )
