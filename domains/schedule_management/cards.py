"""캘린더 예약 카드 UI (compose v2, 2단계)."""

from __future__ import annotations

import html

from datetime import datetime, timedelta, timezone

from typing import Any

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
KST_OFFSET_MINUTES = 9 * 60

from domains.schedule_management.compose_state import (
    ATTENDEE_COUNT_OPTIONS,
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
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


def _time_options(
    *,
    start: str = BUSINESS_HOUR_START,
    end: str = BUSINESS_HOUR_END,
    step_min: int = 10,
) -> list[dict[str, Any]]:
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


def _date_to_ms_epoch_utc(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
        return str(int(dt.timestamp() * 1000))
    except ValueError:
        return ""


def _meeting_date_widgets(date_val: str, base_params: dict[str, str]) -> list[dict[str, Any]]:
    """날짜 없음 → dateTimePicker(클릭), 날짜 있음(자연어·선택) → 표시 + 변경 버튼."""
    widgets: list[dict[str, Any]] = [{"textParagraph": {"text": "<b>회의일자</b>"}}]
    if date_val:
        clear_params = dict(base_params)
        clear_params["meeting_date"] = ""
        widgets.append(
            {
                "decoratedText": {
                    "topLabel": format_date_korean(date_val) or date_val,
                    "text": date_val,
                    "button": {
                        "text": "변경",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_quick_update",
                                "parameters": _params_list(clear_params),
                            }
                        },
                    },
                }
            }
        )
        return widgets

    picker: dict[str, Any] = {
        "name": "meeting_date",
        "type": "DATE_ONLY",
        "timezoneOffsetDate": KST_OFFSET_MINUTES,
        "onChangeAction": _quick_update_action(base_params),
    }
    widgets.append({"dateTimePicker": picker})
    return widgets


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


def _info_widgets(infos: list[str]) -> list[dict[str, Any]]:
    """에러(빨강)와 구분되는 회색 안내 — '이미 추가돼 있어요' 같은 정상 상황용."""
    if not infos:
        return []
    return [
        {
            "textParagraph": {
                "text": '<font color="#9aa0a6">' + "<br>".join(html.escape(i) for i in infos) + "</font>"
            }
        }
    ]


def _columns_two(left_widget: dict[str, Any], right_widget: dict[str, Any]) -> dict[str, Any]:
    return {
        "columns": {
            "columnItems": [
                {
                    "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                    "widgets": [left_widget],
                },
                {
                    "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                    "widgets": [right_widget],
                },
            ]
        }
    }


def _columns_widget_buttons(
    left_widget: dict[str, Any],
    buttons: list[dict[str, Any]],
) -> dict[str, Any]:
    """왼쪽 위젯 + 오른쪽 버튼 한 줄 배치."""
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


def _attendee_count_button_widget(state: dict[str, Any], base_params: dict[str, str]) -> dict[str, Any]:
    selected = state.get("attendee_count")
    try:
        selected_n = int(selected) if selected is not None else None
    except (TypeError, ValueError):
        selected_n = None
    buttons: list[dict[str, Any]] = []
    for n in ATTENDEE_COUNT_OPTIONS:
        params = dict(base_params)
        params["attendee_count"] = str(n)
        btn: dict[str, Any] = {
            "text": f"{n}+",
            "onClick": {
                "action": {
                    "function": "sm_compose_quick_update",
                    "parameters": _params_list(params),
                }
            },
        }
        if selected_n == n:
            btn["type"] = "FILLED"
        buttons.append(btn)
    return {"buttonList": {"buttons": buttons}}


def _headcount_note_widgets(state: dict[str, Any]) -> list[dict[str, Any]]:
    """'5명'이라 말했는데 버튼은 4+ 로 내림된 경우 — 추천은 실제 인원 기준임을 알린다."""
    try:
        headcount = int(state.get("attendee_headcount") or 0)
        bucket = int(state.get("attendee_count") or 0)
    except (TypeError, ValueError):
        return []
    if headcount <= 0 or headcount <= bucket:
        return []
    return [
        {
            "textParagraph": {
                "text": f'<font color="#9aa0a6">현재 참석 {headcount}명 기준으로 추천</font>'
            }
        }
    ]


def _duration_radio_widget(state: dict[str, Any], base_params: dict[str, str]) -> dict[str, Any]:
    mode = str(state.get("duration_mode") or "").strip()
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


def _time_row_widget(state: dict[str, Any], base_params: dict[str, str]) -> dict[str, Any]:
    mode = str(state.get("duration_mode") or "").strip()
    # Chat 드롭다운은 펼칠 때 스크롤 위치를 제어할 수 없어 항목 수를 줄인다(67→23개).
    # 직접입력 모드에서만 10분 단위. 목록에 없는 값(자연어 "3시 10분")은 제자리에 끼운다.
    step = 10 if mode == "custom" else 30
    sel_time = str(state.get("meeting_time") or "")
    start = _time_dropdown("meeting_time", "시작 시간", sel_time, base_params, step_min=step)
    if mode == "custom":
        sel_end = str(state.get("meeting_end_time") or "")
        end = _time_dropdown("meeting_end_time", "종료 시간", sel_end, base_params, step_min=step)
    else:
        end_label = resolve_end_time(state) or "-"
        end = {"textParagraph": {"text": f"종료 시간: {html.escape(end_label)}"}}
    return _columns_two(start, end)


def _time_dropdown(
    name: str,
    label: str,
    selected: str,
    base_params: dict[str, str],
    *,
    step_min: int = 30,
) -> dict[str, Any]:
    items = _time_options(step_min=step_min)
    if selected and not any(item.get("value") == selected for item in items):
        pos = next(
            (i for i, item in enumerate(items) if item.get("value") and item["value"] > selected),
            len(items),
        )
        items.insert(pos, {"text": selected, "value": selected, "selected": True})
    else:
        for item in items:
            if item.get("value") == selected:
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


TEAM_SUGGESTION_SUFFIX = " (팀 전원 추가)"


def _member_suggestion_items(members: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        team_name = str(m.get("team_name") or "").strip()
        if team_name and team_name not in seen:
            items.append({"text": f"{team_name}{TEAM_SUGGESTION_SUFFIX}"})
            seen.add(team_name)
    for m in members:
        name = str(m.get("name") or "").strip()
        email = str(m.get("email") or "").strip()
        if name and email:
            label = f"{name} ({email})"
            if label not in seen:
                items.append({"text": label})
                seen.add(label)
                seen.add(email)
        elif email and email not in seen:
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
        parts.append(f"참석 {ac}+")
    room = str(state.get("picked_room_name") or "").strip()
    if room:
        parts.append(room)
    return " · ".join(parts)


def build_settings_card(
    teams: list[dict[str, str]],
    *,
    room_calendar_config: dict[str, Any] | None = None,
    include_action_response: bool = False,
) -> dict[str, Any]:
    team_items = (
        [{"text": t["name"], "value": t["id"]} for t in teams]
        if teams
        else [{"text": "등록된 팀이 없습니다", "value": "__none__"}]
    )
    rcfg = room_calendar_config or {}
    resource_ids_text = "\n".join(rcfg.get("room_resource_ids") or [])
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
            "textParagraph": {
                "text": (
                    "<b>회의실 설정 (군산·서울)</b><br>"
                    "집계 캘린더 ID와 리소스 ID를 저장합니다. "
                    "동기화 0건일 때 리소스 ID를 줄바꿈으로 입력하세요."
                )
            }
        },
        {
            "textInput": {
                "name": "group_calendar_id",
                "label": "군산 집계 캘린더 ID",
                "value": str(rcfg.get("group_calendar_id") or ""),
            }
        },
        {
            "textInput": {
                "name": "room_resource_ids",
                "label": "회의실 리소스 ID (줄바꿈/쉼표)",
                "value": resource_ids_text,
            }
        },
        {
            "textInput": {
                "name": "sync_name_filter",
                "label": "동기화 이름 필터",
                "value": str(rcfg.get("sync_name_filter") or "군산"),
            }
        },
        {
            "textInput": {
                "name": "impersonate_email",
                "label": "DWD 사용자 이메일 (선택)",
                "value": str(rcfg.get("impersonate_email") or ""),
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {"text": "회의실 설정 저장", "onClick": {"action": {"function": "sm_settings_save_room_calendar"}}}
                ]
            }
        },
        {
            "buttonList": {
                "buttons": [{"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}}]
            }
        },
    ]
    return _wrap_card(
        "sm_settings",
        {"title": "AllMeet", "subtitle": "캘린더 설정"},
        widgets,
        include_action_response=include_action_response,
    )


_ICON_CALENDAR = "https://www.gstatic.com/images/branding/product/2x/calendar_48dp.png"
_ICON_SCHEDULE = "https://www.gstatic.com/images/icons/material/system/2x/schedule_gm_blue_24dp.png"
_ICON_ROOM = "https://www.gstatic.com/images/icons/material/system/2x/meeting_room_gm_blue_24dp.png"
_ICON_EVENT = "https://www.gstatic.com/images/icons/material/system/2x/event_gm_blue_24dp.png"
_ICON_GROUP = "https://www.gstatic.com/images/icons/material/system/2x/group_gm_blue_24dp.png"
_ICON_VIDEO = "https://www.gstatic.com/images/icons/material/system/2x/videocam_gm_blue_24dp.png"
_ICON_MAIL = "https://www.gstatic.com/images/icons/material/system/2x/email_gm_blue_24dp.png"

_WEEKDAYS_KO = ("월", "화", "수", "목", "금", "토", "일")


def _icon_text_widget(*, top_label: str, text: str, icon_url: str) -> dict[str, Any]:
    return {
        "decoratedText": {
            "topLabel": top_label,
            "text": html.escape(text),
            "wrapText": True,
            "startIcon": {"iconUrl": icon_url},
        }
    }


def _format_booking_datetime(date_str: str, start_time: str, end_time: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        wd = _WEEKDAYS_KO[d.weekday()]
        date_part = f"{d.year}년 {d.month}월 {d.day}일 ({wd})"
    except ValueError:
        date_part = format_date_korean(date_str) or date_str
    return f"{date_part}  {start_time} ~ {end_time}"


def build_booking_confirmed_card(
    *,
    status: str,
    meeting_title: str,
    meeting_date: str,
    meeting_time: str,
    meeting_end_time: str,
    location: str = "",
    attendee_count: str | int | None = None,
    attendee_emails: list[str] | None = None,
    invite_sent: bool = False,
    invite_params: dict[str, str] | None = None,
    booker_email: str = "",
    meet_link: str = "",
    html_link: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    """예약 확정 결과 카드 — 필드별 아이콘·링크 버튼."""
    status_messages = {
        "created": ('<font color="#137333"><b>✅ 예약이 완료되었습니다</b></font>', "예약 완료"),
        "updated": ('<font color="#137333"><b>✅ 예약이 변경되었습니다</b></font>', "예약 변경"),
        "dry_run": (
            '<font color="#b06000"><b>⚠️ 드라이런 모드</b></font><br>'
            "실제 캘린더에는 저장되지 않았습니다.",
            "드라이런",
        ),
    }
    banner_html, subtitle = status_messages.get(status, status_messages["created"])

    detail_widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": banner_html}},
        {"divider": {}},
        _icon_text_widget(
            top_label="일시",
            text=_format_booking_datetime(meeting_date, meeting_time, meeting_end_time),
            icon_url=_ICON_SCHEDULE,
        ),
    ]
    if location:
        detail_widgets.append(
            _icon_text_widget(top_label="회의실", text=location, icon_url=_ICON_ROOM)
        )
    detail_widgets.append(
        _icon_text_widget(top_label="제목", text=meeting_title or "제목 없음", icon_url=_ICON_EVENT)
    )

    attendees = [e for e in (attendee_emails or []) if e]
    if attendees:
        attendee_lines = "\n".join(attendees)
        detail_widgets.append(
            _icon_text_widget(
                top_label=f"참석자 ({len(attendees)}명)",
                text=attendee_lines,
                icon_url=_ICON_GROUP,
            )
        )
    elif attendee_count:
        detail_widgets.append(
            _icon_text_widget(
                top_label="참석 인원",
                text=f"{attendee_count}명 이상",
                icon_url=_ICON_GROUP,
            )
        )
    if invite_sent:
        detail_widgets.append(
            _icon_text_widget(
                top_label="초대 메일",
                text="참석자에게 Google Calendar 초대 메일을 보냈습니다.",
                icon_url=_ICON_MAIL,
            )
        )

    sections: list[dict[str, Any]] = [{"widgets": detail_widgets}]

    link_buttons: list[dict[str, Any]] = []
    if meet_link:
        link_buttons.append(
            {
                "text": "Google Meet 참여",
                "type": "FILLED",
                "onClick": {"openLink": {"url": meet_link}},
            }
        )
    if html_link:
        link_buttons.append(
            {
                "text": "캘린더에서 보기",
                "onClick": {"openLink": {"url": html_link}},
            }
        )
    if link_buttons:
        sections.append({"header": "바로가기", "widgets": [{"buttonList": {"buttons": link_buttons}}]})

    footer_buttons: list[dict[str, Any]] = [
        {"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}}
    ]
    booker_lower = str(booker_email or "").strip().lower()
    other_attendees = [e for e in attendees if e.lower() != booker_lower]
    if (
        invite_params
        and not invite_sent
        and other_attendees
        and status != "dry_run"
    ):
        footer_buttons.insert(
            0,
            {
                "text": "초대메일전송",
                "type": "FILLED",
                "onClick": {
                    "action": {
                        "function": "sm_send_invite",
                        "parameters": _params_list(invite_params),
                    }
                },
            },
        )
    sections.append({"widgets": [{"buttonList": {"buttons": footer_buttons}}]})

    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": "sm_booking_confirmed",
                "card": {
                    "header": {
                        "title": "예약 확정",
                        "subtitle": subtitle,
                        "imageUrl": _ICON_CALENDAR,
                        "imageType": "CIRCLE",
                    },
                    "sections": sections,
                },
            }
        ]
    }
    if include_action_response:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def build_result_card(
    *,
    title: str,
    lines: list[str],
    include_action_response: bool = False,
) -> dict[str, Any]:
    body = "<br>".join(html.escape(line) for line in lines) if lines else "-"
    buttons: list[dict[str, Any]] = [{"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}}]
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
    """추가된 참석자 목록 + 제거 드롭다운.

    예전엔 참석자마다 'x' 버튼을 두고 버튼마다 전체 상태(참석자 목록 포함)를 파라미터로 실어,
    팀 두 개(13명)만 넣어도 카드가 48KB 로 Chat 한도(~32KB)를 넘겨 "요청을 처리할 수 없음"이
    났다. 상태 파라미터를 가진 버튼은 하나만 두고 대상은 드롭다운으로 고른다.
    """
    widgets: list[dict[str, Any]] = []
    attendees = state.get("attendees") or []
    if not attendees:
        return widgets
    labels: list[str] = []
    items: list[dict[str, Any]] = []
    for person in attendees:
        name = str(person.get("name") or "").strip()
        email = str(person.get("email") or "").strip()
        if not email and not name:
            continue
        label = f"{name}({email})" if name and email else (email or name)
        labels.append(html.escape(name or email))
        items.append({"text": label[:80], "value": email or name})
    widgets.append(
        {
            "textParagraph": {
                "text": f"<b>추가된 참석자 {len(items)}명</b><br>" + ", ".join(labels)
            }
        }
    )
    widgets.append(
        _columns_widget_buttons(
            {
                "selectionInput": {
                    "name": "remove_attendee_email",
                    "label": "제거할 참석자",
                    "type": "DROPDOWN",
                    "items": items,
                }
            },
            [
                {
                    "text": "제거",
                    "onClick": {
                        "action": {
                            "function": "sm_compose_remove_attendee_email",
                            "parameters": _params_list(base_params),
                        }
                    },
                }
            ],
        )
    )
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


def _availability_badge(room: dict[str, Any]) -> str:
    if not room.get("show_availability"):
        return ""
    label = str(room.get("availability_label") or "")
    if label == "사용 가능":
        return "🟢 사용 가능"
    if label == "사용 중":
        return "🔴 사용 중"
    return ""


_ROOM_REGION_OPTIONS: tuple[tuple[str, str], ...] = (("전체", "all"), ("군산", "gunsan"), ("서울", "seoul"))


def _room_region_button_widget(base_params: dict[str, str], active_region: str) -> list[dict[str, Any]]:
    """근무 위치 기준 기본 지역을 자동 선택해 두되, 버튼으로 다른 지역도 고를 수 있게 한다."""
    active = (active_region or "all").strip() or "all"
    buttons: list[dict[str, Any]] = []
    for label, value in _ROOM_REGION_OPTIONS:
        params = dict(base_params)
        params["room_region"] = value
        btn: dict[str, Any] = {
            "text": label,
            "onClick": {
                "action": {
                    "function": "sm_compose_quick_update",
                    "parameters": _params_list(params),
                }
            },
        }
        if value == active:
            btn["type"] = "FILLED"
        buttons.append(btn)
    return [
        {"textParagraph": {"text": "<b>회의실 지역</b>"}},
        {"buttonList": {"buttons": buttons}},
    ]


def _room_widgets_pending() -> list[dict[str, Any]]:
    return [
        {"textParagraph": {"text": "<b>추천 회의실</b>"}},
        {
            "textParagraph": {
                "text": "날짜·인원·시간을 모두 선택하면 추천 회의실이 표시됩니다.",
            }
        },
    ]


def _room_widgets(
    rooms: list[dict[str, Any]],
    state: dict[str, Any],
    base_params: dict[str, str],
    *,
    pick_action: str = "sm_compose_pick_room",
) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = [{"textParagraph": {"text": "<b>추천 회의실</b>"}}]
    if not rooms:
        widgets.append({"textParagraph": {"text": "(추천할 회의실이 없습니다)"}})
        return widgets

    for room in rooms:
        params = dict(base_params)
        params["picked_room_id"] = str(room.get("id") or "")
        params["picked_room_name"] = str(room.get("name") or "")
        display_name = str(room.get("display_name") or room.get("name") or "")
        line = html.escape(str(room.get("display_line") or ""))
        badge = _availability_badge(room)
        picked = state.get("picked_room_id") == room.get("id")
        prefix = "✓ " if picked else ""
        if badge:
            top_label = f"{prefix}{html.escape(display_name)} — {badge}"
        else:
            top_label = f"{prefix}{html.escape(display_name)}"
        is_busy = str(room.get("availability") or "") == "busy"

        # 왜 막혔는지는 방 이름 옆에 붙어야 읽힌다 — 카드 위 별도 요약 문단을 대신한다.
        reason = str(room.get("busy_reason") or "").strip()

        widgets.append(
            {
                "decoratedText": {
                    "topLabel": top_label,
                    "text": line,
                    **({"bottomLabel": html.escape(reason)} if reason else {}),
                    "wrapText": True,
                    "button": {
                        "text": "사용 중" if is_busy else "선택",
                        "disabled": is_busy,
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


def _slot_day_prefix(slot_date: str, current_date: str) -> str:
    """다른 날 후보면 '9/23(수) ' 를 앞에 붙인다."""
    if not slot_date or slot_date == current_date:
        return ""
    try:
        d = datetime.strptime(slot_date, "%Y-%m-%d")
    except ValueError:
        return f"{slot_date} "
    return f"{d.month}/{d.day}({_WEEKDAYS_KO[d.weekday()]}) "


def _slot_buttons(
    slots: list[Any],
    base_params: dict[str, str],
    *,
    current_date: str = "",
) -> dict[str, Any]:
    buttons: list[dict[str, Any]] = []
    for slot in slots:
        room = html.escape(str(slot.top_room_name or "회의실"))
        label = f"{_slot_day_prefix(slot.meeting_date, current_date)}{slot.meeting_time}~{slot.meeting_end_time} · {room}"
        params = dict(base_params)
        params.update(
            {
                "slot_date": slot.meeting_date,
                "slot_time": slot.meeting_time,
                "slot_end_time": slot.meeting_end_time,
            }
        )
        buttons.append(
            {
                "text": label[:80],
                "onClick": {
                    "action": {
                        "function": "sm_compose_pick_slot",
                        "parameters": _params_list(params),
                    }
                },
            }
        )
    return {"buttonList": {"buttons": buttons}}


def _day_slot_widgets(
    day_slots: list[Any] | None,
    note: str,
    base_params: dict[str, str],
) -> list[dict[str, Any]]:
    """"팀원들 다 되는 시간에" — 참석자·회의실이 모두 비는 시각 후보."""
    if not day_slots and not note:
        return []
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<b>참석자·회의실 모두 가능한 시간</b>"}},
    ]
    if day_slots:
        widgets.append(_slot_buttons(day_slots, base_params, current_date=str(base_params.get("meeting_date") or "")))
    if note:
        widgets.append({"textParagraph": {"text": f'<font color="#9aa0a6">{html.escape(note)}</font>'}})
    return widgets


def _conflict_widgets(
    conflict_check: Any,
    state: dict[str, Any],
    base_params: dict[str, str],
) -> list[dict[str, Any]]:
    if not conflict_check or not getattr(conflict_check, "has_conflict", False):
        return []
    conflicts = list(conflict_check.conflicts or [])
    time_conflicts = [c for c in conflicts if str(getattr(c, "kind", "") or "") != "vacation"]
    vacations = [c for c in conflicts if str(getattr(c, "kind", "") or "") == "vacation"]

    widgets: list[dict[str, Any]] = []
    if time_conflicts:
        widgets.append({"textParagraph": {"text": "<b>일정이 겹칩니다</b>"}})
    for conflict in time_conflicts:
        label = html.escape(str(conflict.label or ""))
        summary = html.escape(str(conflict.event_summary or ""))
        when = html.escape(str(conflict.display_time or ""))
        line = f"{label}: {summary}"
        if when:
            line += f" ({when})"
        link = str(conflict.html_link or "").strip()
        if link:
            widgets.append(
                {
                    "decoratedText": {
                        "text": line,
                        "wrapText": True,
                        "button": {
                            "text": "캘린더에서 보기",
                            "onClick": {"openLink": {"url": link}},
                        },
                    }
                }
            )
        else:
            widgets.append({"textParagraph": {"text": line}})

    if vacations:
        # 휴가는 차단이 아니라 경고 — 한 번에 그 사람만 빼고 이어갈 수 있게 한다.
        widgets.append({"textParagraph": {"text": "<b>휴가·부재 참석자가 있어요</b>"}})
        for conflict in vacations:
            label = html.escape(str(conflict.label or ""))
            summary = html.escape(str(conflict.event_summary or ""))
            params = dict(base_params)
            params["remove_email"] = str(getattr(conflict, "attendee_email", "") or "")
            widgets.append(
                {
                    "decoratedText": {
                        "text": f"{label}: {summary}",
                        "wrapText": True,
                        "button": {
                            "text": "빼고 진행",
                            "onClick": {
                                "action": {
                                    "function": "sm_compose_remove_attendee_email",
                                    "parameters": _params_list(params),
                                }
                            },
                        },
                    }
                }
            )

    if time_conflicts:
        alternatives = conflict_check.alternatives or []
        current_date = str(state.get("meeting_date") or "")
        if alternatives:
            other_day = any(str(getattr(s, "meeting_date", "") or "") != current_date for s in alternatives)
            header = (
                "이날은 모두 되는 시간이 없어요 — 가장 가까운 날의 가능한 시간"
                if other_day
                else "참석자·회의실 모두 가능한 시간"
            )
            widgets.append({"textParagraph": {"text": f"<b>{header}</b>"}})
            widgets.append(_slot_buttons(alternatives, base_params, current_date=current_date))
        else:
            # 대안이 없을 때 충돌 문구만 남기면 "그래서 언제 잡으라는 건지" 를 알 수 없다.
            widgets.append(
                {
                    "textParagraph": {
                        "text": (
                            "요청일부터 5영업일 안에는 "
                            f"{BUSINESS_HOUR_START}~{BUSINESS_HOUR_END} 사이에 "
                            "참석자와 회의실이 모두 되는 시간이 없습니다"
                        )
                    }
                }
            )

        requested = html.escape(str(conflict_check.requested_time or state.get("meeting_time") or ""))
        keep_params = dict(base_params)
        keep_params["ignore_conflict"] = "1"
        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        {
                            "text": f"요청한 {requested} 그대로 진행",
                            "onClick": {
                                "action": {
                                    "function": "sm_compose_keep_requested_time",
                                    "parameters": _params_list(keep_params),
                                }
                            },
                        }
                    ]
                }
            }
        )
    widgets.append({"divider": {}})
    return widgets


def build_quick_compose_card(
    state: dict[str, Any],
    *,
    recommended_rooms: list[dict[str, Any]],
    conflict_check: Any = None,
    room_preview_ready: bool = True,
    room_region_active: str = "all",
    day_slots: list[Any] | None = None,
    day_slots_note: str = "",
    include_action_response: bool = False,
) -> dict[str, Any]:
    base_params = state_to_button_params(state)
    base_params["compose_step"] = "quick"
    widgets: list[dict[str, Any]] = _error_widgets(state.get("errors") or [])
    widgets.extend(_info_widgets(state.get("info") or []))
    widgets.extend(_conflict_widgets(conflict_check, state, base_params))
    date_val = str(state.get("meeting_date") or "")
    widgets.extend(_meeting_date_widgets(date_val, base_params))
    widgets.append(_attendee_count_button_widget(state, base_params))
    widgets.extend(_headcount_note_widgets(state))
    widgets.append(_time_row_widget(state, base_params))
    widgets.append(_duration_radio_widget(state, base_params))
    widgets.extend(_day_slot_widgets(day_slots, day_slots_note, base_params))
    widgets.extend(_room_region_button_widget(base_params, room_region_active))

    if room_preview_ready:
        widgets.extend(_room_widgets(recommended_rooms, state, base_params))
    else:
        widgets.extend(_room_widgets_pending())

    widgets.append(
        {
            "buttonList": {
                "buttons": [{"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}}]
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
    widgets.extend(_info_widgets(state.get("info") or []))

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
    }
    suggestions = _member_suggestion_items(members)
    if suggestions:
        attendee_input["initialSuggestions"] = {"items": suggestions}
    widgets.append(
        _columns_widget_buttons(
            {"textInput": attendee_input},
            [
                {
                    "text": "+ 추가",
                    "onClick": {
                        "action": {
                            "function": "sm_compose_add_attendee",
                            "parameters": _params_list(base_params),
                        }
                    },
                }
            ],
        )
    )
    widgets.append(
        {
            "textParagraph": {
                "text": (
                    '<font color="#9aa0a6">💡 팀명(예: PC2팀)만 입력하면 팀원 전체가 추가돼요</font>'
                )
            }
        }
    )

    want_meet = bool(state.get("want_meet") or state.get("auto_meet"))
    meet_url = str(state.get("meet_url") or "")
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
    widgets.append(
        _columns_widget_buttons(
            {"textParagraph": {"text": "<b>화상회의</b>"}},
            meet_row,
        )
    )
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
                    {"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}},
                    {
                        "text": "회의실수정",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_back_quick",
                                "parameters": _params_list(back_params),
                            }
                        },
                    },
                    {
                        "text": "예약 확정",
                        "type": "FILLED",
                        "onClick": {
                            "action": {
                                "function": "sm_compose_confirm",
                                "parameters": _params_list(confirm_params),
                            }
                        },
                    },
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
    conflict_check: Any = None,
    room_preview_ready: bool = True,
    room_region_active: str = "all",
    day_slots: list[Any] | None = None,
    day_slots_note: str = "",
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
        conflict_check=conflict_check,
        room_preview_ready=room_preview_ready,
        room_region_active=room_region_active,
        day_slots=day_slots,
        day_slots_note=day_slots_note,
        include_action_response=include_action_response,
    )
