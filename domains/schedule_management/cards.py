"""캘린더 예약 카드 UI (compose v2, 2단계)."""

from __future__ import annotations

import html
from typing import Any

from domains.schedule_management.compose_state import (
    format_date_korean,
    resolve_end_time,
    state_to_button_params,
)


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


def _quick_update_action(base_params: dict[str, str]) -> dict[str, Any]:
    return {
        "function": "sm_compose_quick_update",
        "parameters": _params_list(base_params),
    }


def _error_widgets(errors: list[str]) -> list[dict[str, Any]]:
    if not errors:
        return []
    return [
        {
            "textParagraph": {
                "text": "<font color=\"#d93025\">" + "<br>".join(html.escape(e) for e in errors) + "</font>"
            }
        }
    ]


def _duration_radio_widget(state: dict[str, Any], base_params: dict[str, str]) -> dict[str, Any]:
    mode = str(state.get("duration_mode") or "1h")
    items = [
        {"text": "1시간", "value": "1h", "selected": mode == "1h"},
        {"text": "2시간", "value": "2h", "selected": mode == "2h"},
        {"text": "직접입력", "value": "custom", "selected": mode == "custom"},
    ]
    return {
        "selectionInput": {
            "name": "duration_mode",
            "label": "회의 시간",
            "type": "RADIO_BUTTON",
            "items": items,
            "onChangeAction": _quick_update_action(base_params),
        }
    }


def _time_dropdown(
    name: str,
    label: str,
    selected: str,
    base_params: dict[str, str],
) -> dict[str, Any]:
    items = _time_options()
    for item in items:
        if item["value"] == selected:
            item["selected"] = True
    return {
        "selectionInput": {
            "name": name,
            "label": label,
            "type": "DROPDOWN",
            "items": items,
            "onChangeAction": _quick_update_action(base_params),
        }
    }


def _member_suggestion_items(members: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        name = str(m.get("name") or "").strip()
        email = str(m.get("email") or "").strip()
        if name and email:
            label = f"{name} ({email})"
            if label not in seen:
                items.append({"text": label})
                seen.add(label)
        if email and email not in seen:
            items.append({"text": email})
            seen.add(email)
        if len(items) >= 100:
            break
    return items


def _compose_summary_line(state: dict[str, Any]) -> str:
    parts: list[str] = []
    date_kr = format_date_korean(str(state.get("meeting_date") or ""))
    if date_kr:
        parts.append(date_kr)
    start = str(state.get("meeting_time") or "").strip()
    end = resolve_end_time(state)
    if start and end:
        parts.append(f"{start}~{end}")
    elif start:
        parts.append(start)
    ac = state.get("attendee_count")
    if ac:
        parts.append(f"참석 {ac}명")
    room = str(state.get("picked_room_name") or "").strip()
    if room:
        parts.append(room)
    return " · ".join(parts)


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
    *,
    pick_action: str = "sm_compose_pick_room",
) -> list[dict[str, Any]]:
    has_schedule = bool(state.get("meeting_date") and state.get("meeting_time"))
    header = "<b>추천 회의실</b>"
    if has_schedule:
        header += " (참석 인원·일시 기준, 최대 3개)"
    else:
        header += " (일시 입력 후 가용성 확인)"
    widgets: list[dict[str, Any]] = [{"textParagraph": {"text": header}}]
    if not rooms:
        widgets.append({"textParagraph": {"text": "(추천할 회의실이 없습니다)"}})
        return widgets
    for room in rooms:
        params = dict(base_params)
        params["picked_room_id"] = str(room.get("id") or "")
        params["picked_room_name"] = str(room.get("name") or "")
        line = html.escape(str(room.get("display_line") or room.get("name") or ""))
        avail = html.escape(str(room.get("availability_label") or ""))
        if not has_schedule and not avail:
            avail = "일시 입력 후 확인"
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
                                "function": pick_action,
                                "parameters": _params_list(params),
                            }
                        },
                    },
                }
            }
        )
    return widgets


def build_quick_compose_card(
    state: dict[str, Any],
    *,
    recommended_rooms: list[dict[str, Any]],
    include_action_response: bool = False,
) -> dict[str, Any]:
    base_params = state_to_button_params(state)
    base_params["compose_step"] = "quick"
    widgets: list[dict[str, Any]] = _error_widgets(state.get("errors") or [])

    widgets.append(
        {
            "dateTimePicker": {
                "name": "meeting_date",
                "label": "회의일자",
                "type": "DATE_ONLY",
                "onChangeAction": _quick_update_action(base_params),
            }
        }
    )
    widgets.append(_duration_radio_widget(state, base_params))

    mode = str(state.get("duration_mode") or "1h")
    sel_time = str(state.get("meeting_time") or "")
    widgets.append(_time_dropdown("meeting_time", "시작 시간", sel_time, base_params))
    if mode == "custom":
        sel_end = str(state.get("meeting_end_time") or "")
        widgets.append(_time_dropdown("meeting_end_time", "종료 시간", sel_end, base_params))

    ac_val = str(state.get("attendee_count") or "")
    widgets.append(
        {
            "textInput": {
                "name": "attendee_count",
                "label": "참석 인원",
                "value": ac_val,
                "validation": {"inputType": "INTEGER"},
                "onChangeAction": _quick_update_action(base_params),
            }
        }
    )

    widgets.extend(_room_widgets(recommended_rooms, state, base_params))

    widgets.append(
        {
            "buttonList": {
                "buttons": [{"text": "메뉴로", "onClick": {"action": {"function": "sm_open_menu"}}}]
            }
        }
    )

    return _wrap_card(
        "sm_compose_quick",
        {"title": "AllMeet", "subtitle": "간편 예약"},
        widgets,
        include_action_response=include_action_response,
    )


def build_full_compose_card(
    state: dict[str, Any],
    *,
    calendar_options: list[dict[str, str]],
    members: list[dict[str, Any]],
    pending_candidates: list[dict[str, str]] | None = None,
    oauth_linked: bool = True,
    oauth_url: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    base_params = state_to_button_params(state)
    base_params["compose_step"] = "full"
    widgets: list[dict[str, Any]] = _error_widgets(state.get("errors") or [])

    summary = _compose_summary_line(state)
    if summary:
        widgets.append({"textParagraph": {"text": f"<b>{html.escape(summary)}</b>"}})

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

    widgets.append({"textInput": {"name": "title", "label": "제목", "value": str(state.get("title") or "")}})

    widgets.append({"textParagraph": {"text": "<b>참석자</b>"}})
    widgets.extend(_attendee_chip_buttons(state, base_params))
    if pending_candidates:
        widgets.extend(_candidate_buttons(pending_candidates, base_params))

    attendee_input: dict[str, Any] = {
        "name": "attendee_input",
        "label": "이름 또는 이메일",
    }
    suggestions = _member_suggestion_items(members)
    if suggestions:
        attendee_input["initialSuggestions"] = {"items": suggestions}
    widgets.append({"textInput": attendee_input})
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

    back_params = dict(base_params)
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
                    {
                        "text": "간편예약 수정",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_back_quick",
                                "parameters": _params_list(back_params),
                            }
                        },
                    },
                    {"text": "메뉴로", "onClick": {"action": {"function": "sm_open_menu"}}},
                ]
            }
        }
    )

    return _wrap_card(
        "sm_compose_full",
        {"title": "AllMeet", "subtitle": "본 예약"},
        widgets,
        include_action_response=include_action_response,
    )


def build_compose_card(
    state: dict[str, Any],
    *,
    calendar_options: list[dict[str, str]],
    recommended_rooms: list[dict[str, Any]],
    members: list[dict[str, Any]] | None = None,
    pending_candidates: list[dict[str, str]] | None = None,
    oauth_linked: bool = True,
    oauth_url: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    step = str(state.get("compose_step") or "quick")
    if step == "full":
        return build_full_compose_card(
            state,
            calendar_options=calendar_options,
            members=members or [],
            pending_candidates=pending_candidates,
            oauth_linked=oauth_linked,
            oauth_url=oauth_url,
            include_action_response=include_action_response,
        )
    return build_quick_compose_card(
        state,
        recommended_rooms=recommended_rooms,
        include_action_response=include_action_response,
    )
