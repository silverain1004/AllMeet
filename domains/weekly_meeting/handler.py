"""주간보고 카드 액션 핸들러."""

from __future__ import annotations

import re
from typing import Any

from domains.weekly_meeting.cards import (
    build_confluence_edit_template_card,
    build_confluence_edit_root_card,
    build_confluence_edit_space_card,
    build_confluence_menu_card,
    build_confluence_view_result_card,
    build_confluence_view_select_card,
    build_schedule_menu_card,
    build_schedule_result_card,
    build_scheduler_card,
    build_scheduler_result_card,
    build_team_add_card,
    build_team_delete_card,
    build_team_edit_card,
    build_team_list_card,
    build_team_member_delete_card,
    build_team_member_edit_card,
    build_team_member_list_result_card,
    build_team_member_list_select_card,
    build_team_member_menu_card,
    build_team_member_register_card,
    build_team_member_reorder_card,
    build_team_setting_menu_card,
    build_weekly_meeting_menu_card,
)
from domains.weekly_meeting.schedule_lookup import lookup_member_vacation, lookup_weekly_meeting
from firestore.team_config import (
    delete_team,
    get_team_config,
    get_team_list,
    parse_confluence_space_key,
    parse_root_page_ids,
    parse_template_page_id,
    rename_team_in_list,
    update_team_name,
    upsert_team_config,
)


def _safe_form_value(form_inputs: dict[str, Any], key: str) -> str:
    raw = form_inputs.get(key)
    if isinstance(raw, dict):
        string_inputs = raw.get("stringInputs") or {}
        values = string_inputs.get("value") or []
        if isinstance(values, list) and values:
            return str(values[0]).strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _safe_team_id(form_inputs: dict[str, Any]) -> str:
    team_id = _safe_form_value(form_inputs, "team_id")
    return "" if team_id == "__none__" else team_id


def _normalize_nicknames(raw_value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n]", raw_value or "") if p.strip()]


def _normalize_members(raw_members: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        return []
    out: list[dict[str, Any]] = []
    for member in raw_members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        raw_nick = member.get("nickname")
        if isinstance(raw_nick, list):
            nicknames = [str(n).strip() for n in raw_nick if str(n).strip()]
        else:
            nicknames = _normalize_nicknames(str(raw_nick or ""))
        out.append({"name": name, "nickname": nicknames})
    return out


def _team_required(team_id: str) -> tuple[dict[str, Any], str] | tuple[None, None]:
    if not team_id:
        return None, None
    existing = get_team_config(team_id) or {}
    team_name = str(existing.get("team_name") or team_id)
    return existing, team_name


def _user_context(chat_event: dict[str, Any]) -> dict[str, str]:
    user = (chat_event or {}).get("user") or {}
    return {
        "name": str(user.get("displayName") or ""),
        "email": str(user.get("email") or ""),
        "department": "미지정",
    }


def _space_id(chat_event: dict[str, Any]) -> str:
    return str(((chat_event or {}).get("space") or {}).get("name", ""))


def _calendar_id(existing: dict[str, Any]) -> str:
    return str(existing.get("calendar_id") or "primary")


def _schedule_lines(events: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for i, e in enumerate(events, start=1):
        summary = e.get("summary", "-")
        start = e.get("start", "-")
        end = e.get("end", "-")
        lines.append(f"{i}. {summary}<br>&nbsp;&nbsp;일자: {start} ~ {end}")
    return lines


def handle_weekly_meeting(user_message: str, chat_event: dict[str, Any] | None = None) -> dict[str, Any]:
    out = build_weekly_meeting_menu_card()
    out["text"] = "주간보고 메뉴를 선택해 주세요."
    return out


def handle_weekly_meeting_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    user_context = _user_context(chat_event)
    space_id = _space_id(chat_event)

    # 메인 분기
    if invoked_function == "wm_open_menu":
        return build_weekly_meeting_menu_card(include_action_response=True)
    if invoked_function == "wm_open_schedule_menu":
        return build_schedule_menu_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_open_team_menu":
        return build_team_setting_menu_card(include_action_response=True)
    if invoked_function == "wm_open_member_menu":
        return build_team_member_menu_card(include_action_response=True)
    if invoked_function == "wm_open_conf_menu":
        return build_confluence_menu_card(include_action_response=True)
    if invoked_function == "wm_open_scheduler":
        return build_scheduler_card(get_team_list(), include_action_response=True)

    # 일정 조회
    if invoked_function in {"wm_schedule_meeting_this", "wm_schedule_meeting_next"}:
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        result = lookup_weekly_meeting(
            team_name=team_name,
            is_next_week=(invoked_function == "wm_schedule_meeting_next"),
            calendar_id=_calendar_id(existing),
        )
        if not result.ok:
            if result.error_kind == "calendar_not_found":
                lines = ["캘린더를 찾을 수 없습니다."]
            else:
                lines = ["캘린더 조회 중 오류가 발생했습니다."]
            return build_schedule_result_card("주간회의 일정 조회", lines, include_action_response=True)
        title = "주간회의 일정 조회 (다음주)" if invoked_function.endswith("next") else "주간회의 일정 조회 (이번주)"
        lines = _schedule_lines(result.events)
        return build_schedule_result_card(title, lines, include_action_response=True)

    if invoked_function in {"wm_schedule_vacation_this", "wm_schedule_vacation_next"}:
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        members = _normalize_members(existing.get("team_members"))
        keywords: list[str] = []
        for m in members:
            keywords.append(str(m.get("name") or ""))
            for nick in m.get("nickname") or []:
                keywords.append(str(nick))
        result = lookup_member_vacation(
            member_keywords=keywords,
            is_next_week=(invoked_function == "wm_schedule_vacation_next"),
            calendar_id=_calendar_id(existing),
        )
        if not result.ok:
            if result.error_kind == "calendar_not_found":
                lines = ["캘린더를 찾을 수 없습니다."]
            else:
                lines = ["캘린더 조회 중 오류가 발생했습니다."]
            return build_schedule_result_card("팀원 휴가일정 조회", lines, include_action_response=True)
        title = f"{team_name} 팀원 휴가일정 (다음주)" if invoked_function.endswith("next") else f"{team_name} 팀원 휴가일정 (이번주)"
        lines = _schedule_lines(result.events)
        return build_schedule_result_card(title, lines, include_action_response=True)

    # 팀 설정
    if invoked_function == "wm_team_open_list":
        return build_team_list_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_team_open_add":
        return build_team_add_card(include_action_response=True)
    if invoked_function == "wm_team_open_edit":
        return build_team_edit_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_team_open_delete":
        return build_team_delete_card(get_team_list(), include_action_response=True)

    if invoked_function == "wm_team_do_add":
        team_id = _safe_form_value(form_inputs, "new_team_id").lower()
        team_name = _safe_form_value(form_inputs, "new_team_name")
        if not re.fullmatch(r"[a-z0-9_-]+", team_id or ""):
            return {"text": "팀 ID는 영문 소문자/숫자/_/- 형식으로 입력해 주세요."}
        if not team_name:
            return {"text": "팀 이름을 입력해 주세요."}
        if get_team_config(team_id):
            return {"text": "이미 존재하는 팀 ID입니다."}
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"team_members": [], "setup_completed": False},
        )
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 팀이 추가되었습니다: {team_name} (config/{team_id})"}

    if invoked_function == "wm_team_do_edit":
        team_id = _safe_team_id(form_inputs)
        new_team_name = _safe_form_value(form_inputs, "new_team_name")
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        if not new_team_name:
            return {"text": "새 팀 이름을 입력해 주세요."}
        update_team_name(team_id=team_id, new_team_name=new_team_name, space_id=space_id, user_context=user_context)
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 팀 이름이 수정되었습니다: {new_team_name}"}

    if invoked_function == "wm_team_do_delete":
        team_id = _safe_team_id(form_inputs)
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        existing = get_team_config(team_id) or {}
        team_name = str(existing.get("team_name") or team_id)
        if not delete_team(team_id):
            return {"text": "삭제할 팀을 찾지 못했습니다."}
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 팀이 삭제되었습니다: {team_name}"}

    # 팀원 설정
    if invoked_function == "wm_tm_open_list":
        return build_team_member_list_select_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_tm_open_register":
        return build_team_member_register_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_tm_open_edit":
        return build_team_member_edit_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_tm_open_delete":
        return build_team_member_delete_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_tm_open_reorder":
        return build_team_member_reorder_card(get_team_list(), include_action_response=True)

    if invoked_function == "wm_tm_do_list":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_team_member_list_result_card(team_name, _normalize_members(existing.get("team_members")), include_action_response=True)

    if invoked_function == "wm_tm_do_register_member":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        member_name = _safe_form_value(form_inputs, "member_name")
        if not member_name:
            return {"text": "팀원 이름을 입력해 주세요."}
        nicknames = _normalize_nicknames(_safe_form_value(form_inputs, "member_nicknames"))
        members = _normalize_members(existing.get("team_members"))
        members.append({"name": member_name, "nickname": nicknames})
        upsert_team_config(team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members})
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ {team_name} 팀에 팀원 {member_name} 님을 추가했습니다."}

    if invoked_function == "wm_tm_do_edit":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw_index = _safe_form_value(form_inputs, "member_index")
        if not raw_index.isdigit():
            return {"text": "수정할 팀원 번호를 숫자로 입력해 주세요."}
        idx = int(raw_index) - 1
        members = _normalize_members(existing.get("team_members"))
        if idx < 0 or idx >= len(members):
            return {"text": "팀원 번호 범위를 확인해 주세요."}
        new_name = _safe_form_value(form_inputs, "new_member_name")
        new_nicknames = _safe_form_value(form_inputs, "new_member_nicknames")
        if new_name:
            members[idx]["name"] = new_name
        if new_nicknames:
            members[idx]["nickname"] = _normalize_nicknames(new_nicknames)
        upsert_team_config(team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members})
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": "✅ 팀원 정보가 수정되었습니다."}

    if invoked_function == "wm_tm_do_delete":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw_index = _safe_form_value(form_inputs, "member_index")
        if not raw_index.isdigit():
            return {"text": "삭제할 팀원 번호를 숫자로 입력해 주세요."}
        idx = int(raw_index) - 1
        members = _normalize_members(existing.get("team_members"))
        if idx < 0 or idx >= len(members):
            return {"text": "팀원 번호 범위를 확인해 주세요."}
        removed_name = str(members[idx].get("name") or "")
        members.pop(idx)
        upsert_team_config(team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members})
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 팀원을 삭제했습니다: {removed_name}"}

    if invoked_function == "wm_tm_do_reorder":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        members = _normalize_members(existing.get("team_members"))
        tokens = [t for t in re.split(r"[\s,]+", _safe_form_value(form_inputs, "member_order")) if t]
        if len(tokens) != len(members) or not all(t.isdigit() for t in tokens):
            return {"text": "팀원 수와 동일한 번호를 입력해 주세요."}
        order = [int(t) for t in tokens]
        if sorted(order) != list(range(1, len(members) + 1)):
            return {"text": "번호는 1부터 팀원 수까지 중복 없이 입력해 주세요."}
        reordered = [members[i - 1] for i in order]
        upsert_team_config(team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": reordered})
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": "✅ 팀원 순서가 변경되었습니다."}

    # 컨플루언스 설정
    if invoked_function == "wm_conf_open_view":
        return build_confluence_view_select_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_open_edit_space":
        return build_confluence_edit_space_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_open_edit_root":
        return build_confluence_edit_root_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_open_edit_template":
        return build_confluence_edit_template_card(get_team_list(), include_action_response=True)

    if invoked_function == "wm_conf_do_view":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_confluence_view_result_card(team_name, existing, include_action_response=True)

    if invoked_function == "wm_conf_do_edit_space":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        space_key = parse_confluence_space_key(_safe_form_value(form_inputs, "confluence_space_key"))
        if not space_key:
            return {"text": "유효한 스페이스 키를 입력해 주세요."}
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"confluence_space_key": space_key},
        )
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 스페이스가 수정되었습니다: {space_key}"}

    if invoked_function == "wm_conf_do_edit_root":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        try:
            root_pages = parse_root_page_ids(_safe_form_value(form_inputs, "root_page_ids"))
        except ValueError as e:
            return {"text": f"루트 페이지 ID 형식이 올바르지 않습니다: {e}"}
        if not root_pages:
            return {"text": "루트 페이지 ID를 1개 이상 입력해 주세요."}
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"root_pages": root_pages},
        )
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": "✅ 폴더 구조가 수정되었습니다."}

    if invoked_function == "wm_conf_do_edit_template":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        template_page_url = _safe_form_value(form_inputs, "template_page_url")
        if not template_page_url:
            return {"text": "템플릿 URL 또는 Page ID를 입력해 주세요."}
        template_page_id = parse_template_page_id(template_page_url)
        if template_page_id is None:
            return {"text": "템플릿 URL 형식을 확인해 주세요. (/pages/{id} 또는 숫자 ID)"}
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={
                "template_page_url": template_page_url,
                "template_page_id": template_page_id,
            },
        )
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 템플릿이 수정되었습니다. (Page ID: {template_page_id})"}

    # 스케줄러
    if invoked_function == "wm_scheduler_test":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        result = lookup_weekly_meeting(team_name=team_name, is_next_week=False, calendar_id=_calendar_id(existing))
        if not result.ok and result.error_kind == "calendar_not_found":
            return build_scheduler_result_card("캘린더를 찾을 수 없습니다.", include_action_response=True)
        if not result.ok:
            return build_scheduler_result_card("캘린더 연동 중 오류가 발생했습니다.", include_action_response=True)
        if not result.events:
            return build_scheduler_result_card(f"{team_name} 주간회의일자를 찾을 수 없습니다.", include_action_response=True)
        first = result.events[0]
        start = str(first.get("start") or "")
        date_text = start[:10] if len(start) >= 10 else start
        return build_scheduler_result_card(f"{team_name} 주간회의 일자는 {date_text}입니다.", include_action_response=True)

    return {"text": "지원하지 않는 카드 액션입니다."}
