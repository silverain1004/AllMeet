"""설정 트리 액션 핸들러."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from domains.settings.cards import (
    build_personal_settings_card,
    build_room_list_card,
    build_room_region_card,
    build_settings_hub_card,
    build_team_meta_card,
    build_team_settings_card,
)
from domains.settings.state import _safe_form_value, _safe_team_id, parse_team_context
from api.drive.permissions import check_sa_member, grant_sa_reader
from config.settings import BOT_SA_EMAIL
from domains.weekly_meeting.schedule_lookup import lookup_weekly_meeting
from firestore.team_config import (
    delete_team,
    get_team_config,
    get_team_list,
    normalize_team_id,
    parse_confluence_space_key,
    parse_root_page_ids,
    parse_shared_drive_ids,
    parse_template_page_id,
    rename_team_in_list,
    update_team_calendar_id,
    update_team_shared_drive_ids,
    upsert_team_config,
)


def _user_context(chat_event: dict[str, Any]) -> dict[str, str]:
    user = (chat_event or {}).get("user") or {}
    return {
        "name": str(user.get("displayName") or ""),
        "email": str(user.get("email") or ""),
        "department": "미지정",
    }


def _space_id(chat_event: dict[str, Any]) -> str:
    return str(((chat_event or {}).get("space") or {}).get("name", ""))


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
        email = str(member.get("email") or "").strip()
        out.append({"name": name, "nickname": nicknames, "email": email})
    return out


def _team_required(team_id: str) -> tuple[dict[str, Any], str] | tuple[None, None]:
    if not team_id:
        return None, None
    existing = get_team_config(team_id) or {}
    team_name = str(existing.get("team_name") or team_id)
    return existing, team_name


def _render_team_card(
    *,
    teams: list[dict[str, str]],
    ctx_team_id: str,
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
    team_config = get_team_config(ctx_team_id) if ctx_team_id else None
    return build_team_settings_card(
        teams,
        team_config,
        team_id=ctx_team_id,
        member_mode=member_mode,
        member_edit_index=member_edit_index,
        member_edit_name=member_edit_name,
        member_edit_nicknames=member_edit_nicknames,
        member_edit_email=member_edit_email,
        member_reorder_index=member_reorder_index,
        calendar_test_message=calendar_test_message,
        status_message=status_message,
        include_action_response=include_action_response,
    )


def _calendar_test_message(team_name: str, calendar_id: str) -> str:
    if not calendar_id:
        return "Calendar ID가 없습니다. 아래 입력란에 등록해 주세요."
    result = lookup_weekly_meeting(team_name=team_name, is_next_week=False, calendar_id=calendar_id)
    if not result.ok and result.error_kind == "calendar_not_found":
        return "캘린더를 찾을 수 없습니다."
    if not result.ok:
        return "캘린더 연동 중 오류가 발생했습니다."
    if not result.events:
        return f"{team_name} 주간회의일자를 찾을 수 없습니다."
    first = result.events[0]
    start = str(first.get("start") or "").strip()
    date_text = start[:10] if len(start) >= 10 else start
    try:
        if start.endswith("Z"):
            start = start[:-1] + "+00:00"
        dt = datetime.fromisoformat(start)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
        dt = dt.astimezone(timezone(timedelta(hours=9)))
        date_text = dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    return f"정상적으로 연결되었습니다.<br>{team_name} 주간회의 일자는 {date_text}입니다."


def handle_settings_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    fn = (invoked_function or "").strip()
    teams = get_team_list()
    user_context = _user_context(chat_event)
    space_id = _space_id(chat_event)

    if fn in {"st_open_hub", "hm_open_settings"}:
        return build_settings_hub_card(include_action_response=True)

    if fn == "st_open_personal":
        return build_personal_settings_card(include_action_response=True)

    if fn == "st_oauth_link":
        from domains.weekly_meeting.handler import handle_weekly_meeting_action

        return handle_weekly_meeting_action(
            invoked_function="wm_oauth_link",
            parameters=parameters,
            form_inputs=form_inputs,
            chat_event=chat_event,
        )

    if fn == "st_open_rooms":
        return build_room_region_card(include_action_response=True)

    if fn == "st_rooms_view_gunsan":
        from domains.schedule_management.rooms_store import get_rooms

        return build_room_list_card(get_rooms(), region_label="군산", include_action_response=True)

    if fn == "st_open_team":
        ctx = parse_team_context(parameters=parameters, form_inputs=form_inputs, teams=teams)
        return _render_team_card(
            teams=teams,
            ctx_team_id=ctx.team_id,
            member_mode=ctx.member_mode,
            calendar_test_message=ctx.calendar_test_message,
            include_action_response=True,
        )

    if fn == "st_team_apply":
        ctx = parse_team_context(parameters=parameters, form_inputs=form_inputs, teams=teams)
        return _render_team_card(
            teams=teams,
            ctx_team_id=ctx.team_id,
            include_action_response=True,
        )

    if fn == "st_members_mode":
        ctx = parse_team_context(parameters=parameters, form_inputs=form_inputs, teams=teams)
        return _render_team_card(
            teams=teams,
            ctx_team_id=ctx.team_id,
            member_mode=parameters.get("member_mode") or ctx.member_mode,
            include_action_response=True,
        )

    if fn == "st_members_cancel":
        team_id = _safe_team_id(form_inputs, parameters)
        return _render_team_card(
            teams=teams,
            ctx_team_id=team_id,
            member_mode="list",
            include_action_response=True,
        )

    # 팀 메타
    if fn == "st_team_meta_open":
        team_id = parameters.get("team_id") or _safe_team_id(form_inputs, parameters)
        return build_team_meta_card(teams, mode="menu", team_id=team_id, include_action_response=True)

    if fn == "st_team_meta_mode":
        mode = parameters.get("team_meta_mode") or "menu"
        team_id = parameters.get("team_id") or _safe_team_id(form_inputs, parameters)
        return build_team_meta_card(teams, mode=mode, team_id=team_id, include_action_response=True)

    if fn == "st_team_do_add":
        team_id = normalize_team_id(_safe_form_value(form_inputs, "new_team_id"))
        team_name = _safe_form_value(form_inputs, "new_team_name")
        if not re.fullmatch(r"[A-Z0-9]+", team_id or ""):
            return {"text": "팀 ID는 영문/숫자 형식으로 입력해 주세요. (예: PC2, MES2)"}
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
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_team_load_edit":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_team_meta_card(
            teams,
            mode="edit",
            team_id=team_id,
            include_action_response=True,
        )

    if fn == "st_team_do_edit":
        team_id = _safe_team_id(form_inputs, parameters)
        new_team_name = _safe_form_value(form_inputs, "new_team_name")
        calendar_id = _safe_form_value(form_inputs, "calendar_id")
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        existing = get_team_config(team_id) or {}
        if not existing:
            return {"text": "수정할 팀을 찾지 못했습니다."}
        target_team_name = new_team_name or str(existing.get("team_name") or team_id)
        if not new_team_name and not calendar_id:
            return {"text": "수정할 값(팀 이름 또는 Calendar ID)을 입력해 주세요."}
        updates: dict[str, Any] = {}
        if calendar_id:
            updates["calendar_id"] = calendar_id
        upsert_team_config(
            team_id=team_id,
            team_name=target_team_name,
            space_id=space_id,
            user_context=user_context,
            updates=updates,
        )
        if new_team_name:
            rename_team_in_list(team_id, new_team_name)
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_team_do_delete":
        team_id = _safe_team_id(form_inputs, parameters)
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        existing = get_team_config(team_id) or {}
        if not delete_team(team_id):
            return {"text": "삭제할 팀을 찾지 못했습니다."}
        teams = get_team_list()
        next_id = str(teams[0]["id"]) if teams else ""
        return _render_team_card(teams=teams, ctx_team_id=next_id, include_action_response=True)

    # 팀원 CRUD
    if fn == "st_members_add":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        member_name = _safe_form_value(form_inputs, "member_name")
        if not member_name:
            return {"text": "팀원 이름을 입력해 주세요."}
        nicknames = _normalize_nicknames(_safe_form_value(form_inputs, "member_nicknames"))
        member_email = _safe_form_value(form_inputs, "member_email")
        members = _normalize_members(existing.get("team_members"))
        members.append({"name": member_name, "nickname": nicknames, "email": member_email})
        upsert_team_config(
            team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members}
        )
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_members_load_edit":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw_index = _safe_form_value(form_inputs, "member_index")
        if not raw_index.isdigit():
            return {"text": "팀원 번호를 숫자로 입력해 주세요."}
        idx = int(raw_index) - 1
        members = _normalize_members(existing.get("team_members"))
        if idx < 0 or idx >= len(members):
            return {"text": "팀원 번호 범위를 확인해 주세요."}
        selected = members[idx]
        nicks = ", ".join(selected.get("nickname") or [])
        return _render_team_card(
            teams=teams,
            ctx_team_id=team_id,
            member_mode="edit",
            member_edit_index=raw_index,
            member_edit_name=str(selected.get("name") or ""),
            member_edit_nicknames=nicks,
            member_edit_email=str(selected.get("email") or ""),
            include_action_response=True,
        )

    if fn == "st_members_edit":
        team_id = _safe_team_id(form_inputs, parameters)
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
        new_email = _safe_form_value(form_inputs, "new_member_email")
        if new_name:
            members[idx]["name"] = new_name
        if new_nicknames:
            members[idx]["nickname"] = _normalize_nicknames(new_nicknames)
        if new_email:
            members[idx]["email"] = new_email
        upsert_team_config(
            team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members}
        )
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_members_delete":
        team_id = _safe_team_id(form_inputs, parameters)
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
        members.pop(idx)
        upsert_team_config(
            team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members}
        )
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_members_move":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw_index = _safe_form_value(form_inputs, "member_index")
        if not raw_index.isdigit():
            return {"text": "팀원 번호를 숫자로 입력해 주세요."}
        idx = int(raw_index) - 1
        members = _normalize_members(existing.get("team_members"))
        if idx < 0 or idx >= len(members):
            return {"text": "팀원 번호 범위를 확인해 주세요."}
        direction = str(parameters.get("direction") or "").strip()
        new_index = raw_index
        status = ""
        if direction == "up":
            if idx == 0:
                status = "이미 맨 위입니다."
            else:
                members[idx], members[idx - 1] = members[idx - 1], members[idx]
                new_index = str(idx)
        elif direction == "down":
            if idx >= len(members) - 1:
                status = "이미 맨 아래입니다."
            else:
                members[idx], members[idx + 1] = members[idx + 1], members[idx]
                new_index = str(idx + 2)
        else:
            return {"text": "이동 방향을 확인해 주세요."}
        if status:
            return _render_team_card(
                teams=teams,
                ctx_team_id=team_id,
                member_mode="reorder",
                member_reorder_index=raw_index,
                status_message=status,
                include_action_response=True,
            )
        upsert_team_config(
            team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members}
        )
        teams = get_team_list()
        return _render_team_card(
            teams=teams,
            ctx_team_id=team_id,
            member_mode="reorder",
            member_reorder_index=new_index,
            include_action_response=True,
        )

    # 컨플루언스 저장
    if fn == "st_conf_save":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        updates: dict[str, Any] = {}
        space_raw = _safe_form_value(form_inputs, "confluence_space_key")
        if space_raw:
            space_key = parse_confluence_space_key(space_raw)
            if not space_key:
                return {"text": "유효한 스페이스 키를 입력해 주세요."}
            updates["confluence_space_key"] = space_key
        root_raw = _safe_form_value(form_inputs, "root_page_ids")
        if root_raw:
            try:
                root_pages = parse_root_page_ids(root_raw)
            except ValueError as e:
                return {"text": f"루트 페이지 ID 형식이 올바르지 않습니다: {e}"}
            if not root_pages:
                return {"text": "루트 페이지 ID를 1개 이상 입력해 주세요."}
            updates["root_pages"] = root_pages
        template_raw = _safe_form_value(form_inputs, "template_page_url")
        if template_raw:
            template_page_id = parse_template_page_id(template_raw)
            if template_page_id is None:
                return {"text": "템플릿 URL 형식을 확인해 주세요."}
            updates["template_page_url"] = template_raw
            updates["template_page_id"] = template_page_id
        if not updates:
            return {"text": "수정할 값을 하나 이상 입력해 주세요."}
        upsert_team_config(
            team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates=updates
        )
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    # 드라이브 저장
    if fn == "st_drive_save":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw = _safe_form_value(form_inputs, "shared_drive_ids")
        ids = parse_shared_drive_ids(raw)
        granter_email = user_context.get("email") or ""
        status = ""
        if not ids:
            update_team_shared_drive_ids(
                team_id=team_id, shared_drive_ids=[], space_id=space_id, user_context=user_context
            )
            status = f"✅ {team_name} 팀 Shared Drive ID가 비워졌습니다."
        else:
            per_id_lines: list[str] = []
            accessible: list[str] = []
            for d_id in ids:
                check = check_sa_member(d_id)
                if check["ok"]:
                    accessible.append(d_id)
                    name = check.get("name") or "(이름 없음)"
                    per_id_lines.append(f"✅ {d_id} — 이미 멤버 ({name})")
                    continue
                grant = grant_sa_reader(drive_id=d_id, granter_email=granter_email, sa_email=BOT_SA_EMAIL)
                if grant["ok"]:
                    accessible.append(d_id)
                    per_id_lines.append(f"✅ {d_id} — 봇 SA 자동 등록 완료")
                    continue
                ek = grant.get("error_kind") or ""
                if ek == "oauth_required":
                    per_id_lines.append(f"🔒 {d_id} — OAuth 동의 필요")
                elif ek == "forbidden":
                    per_id_lines.append(f"❌ {d_id} — Manager 권한 필요")
                else:
                    per_id_lines.append(f"❌ {d_id} — 실패: {ek or 'unknown'}")
            update_team_shared_drive_ids(
                team_id=team_id, shared_drive_ids=accessible, space_id=space_id, user_context=user_context
            )
            status = (
                f"<b>{team_name} 팀 드라이브 저장</b> — 접근 가능 {len(accessible)}/{len(ids)}건<br>"
                + "<br>".join(html.escape(line) for line in per_id_lines)
            )
        teams = get_team_list()
        return _render_team_card(
            teams=teams,
            ctx_team_id=team_id,
            status_message=status,
            include_action_response=True,
        )

    # 캘린더 저장
    if fn == "st_calendar_save":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        calendar_id = _safe_form_value(form_inputs, "calendar_id")
        if not calendar_id:
            return {"text": "Calendar ID를 입력해 주세요."}
        update_team_calendar_id(
            team_id=team_id,
            calendar_id=calendar_id,
            space_id=space_id,
            user_context=user_context,
        )
        teams = get_team_list()
        return _render_team_card(teams=teams, ctx_team_id=team_id, include_action_response=True)

    if fn == "st_calendar_test":
        team_id = _safe_team_id(form_inputs, parameters)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        calendar_id = str(existing.get("calendar_id") or "").strip()
        msg = _calendar_test_message(team_name, calendar_id)
        teams = get_team_list()
        return _render_team_card(
            teams=teams,
            ctx_team_id=team_id,
            calendar_test_message=msg,
            include_action_response=True,
        )

    return {"text": "지원하지 않는 설정 액션입니다."}
