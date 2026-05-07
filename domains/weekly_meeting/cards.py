"""주간보고 카드 UI 빌더."""

from __future__ import annotations

import html
from typing import Any


def _wrap_card(
    card_id: str,
    header: dict[str, Any],
    widgets: list[dict[str, Any]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    out = {
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


def _team_items(teams: list[dict[str, str]], *, selected_team_id: str = "") -> list[dict[str, Any]]:
    if not teams:
        return [{"text": "등록된 팀이 없습니다", "value": "__none__"}]
    out: list[dict[str, Any]] = []
    for team in teams:
        item: dict[str, Any] = {"text": team["name"], "value": team["id"]}
        if selected_team_id and team["id"] == selected_team_id:
            item["selected"] = True
        out.append(item)
    return out


def _menu_back_button() -> dict[str, Any]:
    return {"buttonList": {"buttons": [{"text": "메뉴로 돌아가기", "onClick": {"action": {"function": "wm_open_menu"}}}]}}


def _member_menu_back_button() -> dict[str, Any]:
    return {"buttonList": {"buttons": [{"text": "팀원 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_member_menu"}}}]}}


def _members_text(team_name: str, members: list[dict[str, Any]]) -> str:
    lines = [f"<b>[{html.escape(team_name)}] 팀원 ({len(members)}명)</b>"]
    if not members:
        lines.append("(등록된 팀원이 없습니다)")
        return "<br>".join(lines)
    for i, member in enumerate(members, start=1):
        name = html.escape(str(member.get("name") or ""))
        raw_nickname = member.get("nickname")
        email = html.escape(str(member.get("email") or "").strip())
        if isinstance(raw_nickname, list):
            nicks = [html.escape(str(n).strip()) for n in raw_nickname if str(n).strip()]
        else:
            nicks = [html.escape(str(raw_nickname).strip())] if str(raw_nickname or "").strip() else []
        desc: list[str] = []
        if nicks:
            desc.append(f"닉네임: {', '.join(nicks)}")
        if email:
            desc.append(f"이메일: {email}")
        if desc:
            lines.append(f"{i}. {name} ({' / '.join(desc)})")
        else:
            lines.append(f"{i}. {name}")
    return "<br>".join(lines)


def _conf_text(team_name: str, data: dict[str, Any]) -> str:
    space_key = str(data.get("confluence_space_key") or "-")
    template_page_url = str(data.get("template_page_url") or "-")
    template_page_id = str(data.get("template_page_id") or "-")
    root_pages = data.get("root_pages") or []
    lines = [
        f"<b>[{html.escape(team_name)}] 컨플루언스 설정</b>",
        f"스페이스: {html.escape(space_key)}",
        f"템플릿 URL: {html.escape(template_page_url)}",
        f"템플릿 Page ID: {html.escape(template_page_id)}",
        "폴더 구조:",
    ]
    if not isinstance(root_pages, list) or not root_pages:
        lines.append("- (미설정)")
    else:
        for row in root_pages:
            if not isinstance(row, dict):
                continue
            level = row.get("level")
            page_id = row.get("page_id")
            lines.append(f"- level {level}: {page_id}")
    return "<br>".join(lines)


def build_weekly_meeting_menu_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>주간회의/주간보고 메뉴</b><br>원하는 작업을 선택해 주세요."}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "1. 일정 조회", "onClick": {"action": {"function": "wm_open_schedule_menu"}}},
                    {"text": "2. 팀 설정", "onClick": {"action": {"function": "wm_open_team_menu"}}},
                    {"text": "3. 팀원 설정", "onClick": {"action": {"function": "wm_open_member_menu"}}},
                    {"text": "4. 컨플루언스 설정", "onClick": {"action": {"function": "wm_open_conf_menu"}}},
                    {"text": "5. 스케줄러", "onClick": {"action": {"function": "wm_open_scheduler"}}},
                    {"text": "6. 내 데이터 연결 (Gmail/개인 Calendar)", "onClick": {"action": {"function": "wm_oauth_link"}}},
                ]
            }
        },
    ]
    return _wrap_card("wm_main_menu", {"title": "AllMeet", "subtitle": "주간회의/주간보고"}, widgets, include_action_response=include_action_response)


def build_oauth_link_card(
    *,
    user_email: str,
    auth_url: str,
    include_action_response: bool = False,
) -> dict[str, Any]:
    """OAuth 동의 안내 카드 — 사용자가 외부 링크를 눌러 Google 동의 화면으로."""
    safe_email = html.escape(user_email)
    widgets = [
        {
            "textParagraph": {
                "text": (
                    f"<b>{safe_email}</b> 의 Gmail · 개인 Calendar 를 봇과 연결합니다.<br>"
                    "아래 버튼을 누르면 Google 동의 화면이 열려요. "
                    "동의 후 자동으로 다시 안내됩니다."
                )
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "🔗 Google 동의 페이지 열기",
                        "onClick": {"openLink": {"url": auth_url}},
                    },
                ]
            }
        },
        _menu_back_button(),
    ]
    return _wrap_card(
        "wm_oauth_link",
        {"title": "AllMeet", "subtitle": "내 데이터 연결"},
        widgets,
        include_action_response=include_action_response,
    )


def build_schedule_menu_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>일정 조회</b><br>팀 선택 후 조회 종류를 선택해 주세요."}},
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "주간회의 일정 (이번주)", "onClick": {"action": {"function": "wm_schedule_meeting_this"}}},
                    {"text": "주간회의 일정 (다음주)", "onClick": {"action": {"function": "wm_schedule_meeting_next"}}},
                ]
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {"text": "팀원 휴가일정 (이번주)", "onClick": {"action": {"function": "wm_schedule_vacation_this"}}},
                    {"text": "팀원 휴가일정 (다음주)", "onClick": {"action": {"function": "wm_schedule_vacation_next"}}},
                ]
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {"text": "캘린더 ID 설정", "onClick": {"action": {"function": "wm_schedule_open_calendar_id"}}},
                ]
            }
        },
        _menu_back_button(),
    ]
    return _wrap_card("wm_schedule_menu", {"title": "AllMeet", "subtitle": "일정 조회"}, widgets, include_action_response=include_action_response)


def build_schedule_result_card(title: str, lines: list[str], *, include_action_response: bool = False) -> dict[str, Any]:
    text = f"<b>{html.escape(title)}</b><br>" + ("<br>".join(lines) if lines else "조회 결과가 없습니다.")
    widgets = [{"textParagraph": {"text": text}}, _menu_back_button()]
    return _wrap_card("wm_schedule_result", {"title": "AllMeet", "subtitle": "일정 조회 결과"}, widgets, include_action_response=include_action_response)


def build_schedule_calendar_id_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>팀별 캘린더 ID 설정</b><br>Google Calendar의 calendarId를 입력해 주세요. (예: primary 또는 xxx@group.calendar.google.com)"}},
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"textInput": {"name": "calendar_id", "label": "Calendar ID"}},
        {"buttonList": {"buttons": [{"text": "저장", "onClick": {"action": {"function": "wm_schedule_save_calendar_id"}}}]}},
        {"buttonList": {"buttons": [{"text": "일정 조회로 돌아가기", "onClick": {"action": {"function": "wm_open_schedule_menu"}}}]}},
    ]
    return _wrap_card("wm_schedule_calendar_id", {"title": "AllMeet", "subtitle": "일정 조회 > 캘린더 ID 설정"}, widgets, include_action_response=include_action_response)


def build_team_setting_menu_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>팀 설정</b>"}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "조회", "onClick": {"action": {"function": "wm_team_open_list"}}},
                    {"text": "추가", "onClick": {"action": {"function": "wm_team_open_add"}}},
                    {"text": "수정", "onClick": {"action": {"function": "wm_team_open_edit"}}},
                    {"text": "삭제", "onClick": {"action": {"function": "wm_team_open_delete"}}},
                ]
            }
        },
        _menu_back_button(),
    ]
    return _wrap_card("wm_team_menu", {"title": "AllMeet", "subtitle": "팀 설정"}, widgets, include_action_response=include_action_response)


def build_team_list_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    lines = [f"{i}. {html.escape(t['name'])} ({html.escape(t['id'])})" for i, t in enumerate(teams, start=1)] or ["(등록된 팀이 없습니다)"]
    widgets = [
        {"textParagraph": {"text": "<b>팀 목록</b><br>" + "<br>".join(lines)}},
        {"buttonList": {"buttons": [{"text": "팀 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_team_menu"}}}]}}
    ]
    return _wrap_card("wm_team_list", {"title": "AllMeet", "subtitle": "팀 설정 > 조회"}, widgets, include_action_response=include_action_response)


def build_team_add_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textInput": {"name": "new_team_id", "label": "팀 ID (영문/숫자/_/-)"}},
        {"textInput": {"name": "new_team_name", "label": "팀 이름"}},
        {"buttonList": {"buttons": [{"text": "추가", "onClick": {"action": {"function": "wm_team_do_add"}}}]}},
        {"buttonList": {"buttons": [{"text": "팀 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_team_menu"}}}]}}
    ]
    return _wrap_card("wm_team_add", {"title": "AllMeet", "subtitle": "팀 설정 > 추가"}, widgets, include_action_response=include_action_response)


def build_team_edit_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    current_team_name: str = "",
    current_calendar_id: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_team_load_edit"}}}]}},
        {"textInput": {"name": "new_team_name", "label": "새 팀 이름 (선택)", "value": current_team_name}},
        {"textInput": {"name": "calendar_id", "label": "Calendar ID (선택, 예: primary)", "value": current_calendar_id}},
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_team_do_edit"}}}]}},
        {"buttonList": {"buttons": [{"text": "팀 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_team_menu"}}}]}}
    ]
    return _wrap_card("wm_team_edit", {"title": "AllMeet", "subtitle": "팀 설정 > 수정"}, widgets, include_action_response=include_action_response)


def build_team_delete_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "팀 삭제 시 팀원도 함께 삭제됩니다."}},
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"buttonList": {"buttons": [{"text": "삭제", "onClick": {"action": {"function": "wm_team_do_delete"}}}]}},
        {"buttonList": {"buttons": [{"text": "팀 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_team_menu"}}}]}}
    ]
    return _wrap_card("wm_team_delete", {"title": "AllMeet", "subtitle": "팀 설정 > 삭제"}, widgets, include_action_response=include_action_response)


def build_team_member_menu_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>팀원 설정</b>"}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "조회", "onClick": {"action": {"function": "wm_tm_open_list"}}},
                    {"text": "추가", "onClick": {"action": {"function": "wm_tm_open_register"}}},
                    {"text": "수정", "onClick": {"action": {"function": "wm_tm_open_edit"}}},
                    {"text": "삭제", "onClick": {"action": {"function": "wm_tm_open_delete"}}},
                    {"text": "순서 변경", "onClick": {"action": {"function": "wm_tm_open_reorder"}}},
                ]
            }
        },
        _menu_back_button(),
    ]
    return _wrap_card("wm_member_menu", {"title": "AllMeet", "subtitle": "팀원 설정"}, widgets, include_action_response=include_action_response)


def build_team_member_list_select_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"buttonList": {"buttons": [{"text": "조회", "onClick": {"action": {"function": "wm_tm_do_list"}}}]}},
        _member_menu_back_button(),
    ]
    return _wrap_card("wm_tm_list_select", {"title": "AllMeet", "subtitle": "팀원 설정 > 조회"}, widgets, include_action_response=include_action_response)


def build_team_member_list_result_card(team_name: str, members: list[dict[str, Any]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [{"textParagraph": {"text": _members_text(team_name, members)}}, _member_menu_back_button()]
    return _wrap_card("wm_tm_list_result", {"title": "AllMeet", "subtitle": "팀원 조회 결과"}, widgets, include_action_response=include_action_response)


def build_team_member_register_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"textInput": {"name": "member_name", "label": "이름"}},
        {"textInput": {"name": "member_nicknames", "label": "닉네임 (쉼표 구분, 선택)"}},
        {"textInput": {"name": "member_email", "label": "이메일 (선택)"}},
        {"buttonList": {"buttons": [{"text": "추가", "onClick": {"action": {"function": "wm_tm_do_register_member"}}}]}},
        _member_menu_back_button(),
    ]
    return _wrap_card("wm_tm_add", {"title": "AllMeet", "subtitle": "팀원 설정 > 추가"}, widgets, include_action_response=include_action_response)


def build_team_member_edit_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    selected_member_index: str = "",
    current_member_name: str = "",
    current_member_nicknames: str = "",
    current_member_email: str = "",
    member_snapshot_text: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"textInput": {"name": "member_index", "label": "수정할 팀원 번호 (1부터)", "value": selected_member_index}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_tm_load_edit"}}}]}},
        {"textInput": {"name": "new_member_name", "label": "새 이름 (선택)", "value": current_member_name}},
        {"textInput": {"name": "new_member_nicknames", "label": "새 닉네임 (쉼표 구분, 선택)", "value": current_member_nicknames}},
        {"textInput": {"name": "new_member_email", "label": "새 이메일 (선택)", "value": current_member_email}},
    ]
    if member_snapshot_text:
        widgets.append({"textParagraph": {"text": member_snapshot_text}})
    widgets.extend([
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_tm_do_edit"}}}]}},
        _member_menu_back_button(),
    ])
    return _wrap_card("wm_tm_edit", {"title": "AllMeet", "subtitle": "팀원 설정 > 수정"}, widgets, include_action_response=include_action_response)


def build_team_member_delete_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"textInput": {"name": "member_index", "label": "삭제할 팀원 번호 (1부터)"}},
        {"buttonList": {"buttons": [{"text": "삭제", "onClick": {"action": {"function": "wm_tm_do_delete"}}}]}},
        _member_menu_back_button(),
    ]
    return _wrap_card("wm_tm_delete", {"title": "AllMeet", "subtitle": "팀원 설정 > 삭제"}, widgets, include_action_response=include_action_response)


def build_team_member_reorder_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"textInput": {"name": "member_order", "label": "새 순서 (예: 3 1 2 4)"}},
        {"buttonList": {"buttons": [{"text": "순서 변경", "onClick": {"action": {"function": "wm_tm_do_reorder"}}}]}},
        _member_menu_back_button(),
    ]
    return _wrap_card("wm_tm_reorder", {"title": "AllMeet", "subtitle": "팀원 설정 > 순서 변경"}, widgets, include_action_response=include_action_response)


def build_confluence_menu_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>컨플루언스 설정</b>"}},
        {
            "buttonList": {
                "buttons": [
                    {"text": "조회", "onClick": {"action": {"function": "wm_conf_open_view"}}},
                    {"text": "수정", "onClick": {"action": {"function": "wm_conf_open_edit"}}},
                ]
            }
        },
        _menu_back_button(),
    ]
    return _wrap_card("wm_conf_menu", {"title": "AllMeet", "subtitle": "컨플루언스 설정"}, widgets, include_action_response=include_action_response)


def build_confluence_view_select_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"buttonList": {"buttons": [{"text": "조회", "onClick": {"action": {"function": "wm_conf_do_view"}}}]}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_view_select", {"title": "AllMeet", "subtitle": "컨플루언스 설정 > 조회"}, widgets, include_action_response=include_action_response)


def build_confluence_view_result_card(team_name: str, data: dict[str, Any], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": _conf_text(team_name, data)}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_view_result", {"title": "AllMeet", "subtitle": "컨플루언스 조회 결과"}, widgets, include_action_response=include_action_response)


def build_confluence_edit_space_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    current_space_key: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_conf_load_edit_space"}}}]}},
        {"textInput": {"name": "confluence_space_key", "label": "스페이스 키 (예: PLATFORM)", "value": current_space_key}},
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_conf_do_edit_space"}}}]}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_edit_space", {"title": "AllMeet", "subtitle": "컨플루언스 설정 > 스페이스 수정"}, widgets, include_action_response=include_action_response)


def build_confluence_edit_root_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    current_root_page_ids: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_conf_load_edit_root"}}}]}},
        {"textInput": {"name": "root_page_ids", "label": "루트 페이지 ID 목록", "type": "MULTIPLE_LINE", "hintText": "한 줄에 하나씩 입력", "value": current_root_page_ids}},
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_conf_do_edit_root"}}}]}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_edit_root", {"title": "AllMeet", "subtitle": "컨플루언스 설정 > 폴더 구조 수정"}, widgets, include_action_response=include_action_response)


def build_confluence_edit_template_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    current_template_page_url: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_conf_load_edit_template"}}}]}},
        {"textInput": {"name": "template_page_url", "label": "템플릿 URL 또는 Page ID", "value": current_template_page_url}},
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_conf_do_edit_template"}}}]}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_edit_template", {"title": "AllMeet", "subtitle": "컨플루언스 설정 > 템플릿 수정"}, widgets, include_action_response=include_action_response)


def build_confluence_edit_card(
    teams: list[dict[str, str]],
    *,
    include_action_response: bool = False,
    selected_team_id: str = "",
    current_space_key: str = "",
    current_root_page_ids: str = "",
    current_template_page_url: str = "",
) -> dict[str, Any]:
    widgets = [
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams, selected_team_id=selected_team_id)}},
        {"buttonList": {"buttons": [{"text": "기존값 불러오기", "onClick": {"action": {"function": "wm_conf_load_edit"}}}]}},
        {"textInput": {"name": "confluence_space_key", "label": "스페이스 키 (예: PLATFORM)", "value": current_space_key}},
        {"textInput": {"name": "root_page_ids", "label": "루트 페이지 ID 목록", "type": "MULTIPLE_LINE", "hintText": "한 줄에 하나씩 입력", "value": current_root_page_ids}},
        {"textInput": {"name": "template_page_url", "label": "템플릿 URL 또는 Page ID", "value": current_template_page_url}},
        {"buttonList": {"buttons": [{"text": "수정", "onClick": {"action": {"function": "wm_conf_do_edit"}}}]}},
        {"buttonList": {"buttons": [{"text": "컨플루언스 설정으로 돌아가기", "onClick": {"action": {"function": "wm_open_conf_menu"}}}]}}
    ]
    return _wrap_card("wm_conf_edit", {"title": "AllMeet", "subtitle": "컨플루언스 설정 > 수정"}, widgets, include_action_response=include_action_response)


def build_scheduler_card(teams: list[dict[str, str]], *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {
            "textParagraph": {
                "text": "구글캘린더에 '<b>OO팀 주간회의</b>'로 반복일정을 만들고 AllMeet을 추가하세요.<br>"
                "복사용 예시: <b>PC2팀 주간회의</b>"
            }
        },
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"buttonList": {"buttons": [{"text": "연동테스트", "onClick": {"action": {"function": "wm_scheduler_test"}}}]}},
        _menu_back_button(),
    ]
    return _wrap_card("wm_scheduler", {"title": "AllMeet", "subtitle": "스케줄러"}, widgets, include_action_response=include_action_response)


def build_scheduler_result_card(message: str, *, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": html.escape(message)}},
        {"buttonList": {"buttons": [{"text": "스케줄러로 돌아가기", "onClick": {"action": {"function": "wm_open_scheduler"}}}]}},
        _menu_back_button(),
    ]
    return _wrap_card("wm_scheduler_result", {"title": "AllMeet", "subtitle": "스케줄러 연동테스트"}, widgets, include_action_response=include_action_response)
