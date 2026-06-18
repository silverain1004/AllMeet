"""주간보고 카드 액션 핸들러."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
from typing import Any

from domains.weekly_meeting.cards import (
    build_confluence_edit_card,
    build_confluence_edit_template_card,
    build_confluence_edit_root_card,
    build_confluence_edit_space_card,
    build_confluence_menu_card,
    build_confluence_view_result_card,
    build_confluence_view_select_card,
    build_oauth_link_card,
    build_schedule_menu_card,
    build_schedule_calendar_id_card,
    build_schedule_drive_ids_card,
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
from api.drive.permissions import check_sa_member, grant_sa_reader
from config.settings import BOT_SA_EMAIL
from domains.weekly_meeting.schedule_lookup import lookup_member_vacation, lookup_weekly_meeting
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
    update_team_name,
    update_team_calendar_id,
    update_team_shared_drive_ids,
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
    if team_id == "__none__":
        return ""
    return normalize_team_id(team_id)


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
    return str(existing.get("calendar_id") or "").strip()


def _schedule_lines(events: list[dict[str, str]]) -> list[str]:
    kst = timezone(timedelta(hours=9))

    def _parse_calendar_dt(raw: str) -> datetime | None:
        text = (raw or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d")
                dt = dt.replace(tzinfo=kst)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=kst)
        return dt.astimezone(kst)

    lines: list[str] = []
    for i, e in enumerate(events, start=1):
        summary = e.get("summary", "-")
        start = e.get("start", "-")
        end = e.get("end", "-")
        start_dt = _parse_calendar_dt(start)
        end_dt = _parse_calendar_dt(end)
        if start_dt and end_dt:
            start_date = start_dt.strftime("%Y-%m-%d")
            start_time = start_dt.strftime("%H:%M")
            end_date = end_dt.strftime("%Y-%m-%d")
            end_time = end_dt.strftime("%H:%M")
            if start_date == end_date:
                when = f"{start_date} {start_time} - {end_time}"
            else:
                when = f"{start_date} {start_time} - {end_date} {end_time}"
        else:
            when = f"{start} ~ {end}"
        lines.append(f"{i}. {summary}<br>&nbsp;&nbsp;일자: {when}")
    return lines


def _schedule_error_lines(error_kind: str, team_name: str) -> list[str]:
    if error_kind == "calendar_not_found":
        return [f"{team_name} 팀 캘린더를 찾을 수 없습니다."]
    if error_kind == "calendar_auth_error":
        return ["캘린더 인증/권한 오류가 발생했습니다."]
    if error_kind == "calendar_http_error":
        return ["캘린더 API 호출에 실패했습니다."]
    return ["캘린더 조회 중 오류가 발생했습니다."]


def handle_weekly_meeting(user_message: str, chat_event: dict[str, Any] | None = None) -> dict[str, Any]:
    out = build_weekly_meeting_menu_card()
    out["text"] = "주간보고 메뉴를 선택해 주세요."
    return out


def handle_settings_request(
    user_message: str, chat_event: dict[str, Any] | None = None
) -> dict[str, Any]:
    """'설정' 키워드 진입 — 설정 허브 카드."""
    from domains.settings.cards import build_settings_hub_card

    _ = user_message
    return build_settings_hub_card()


def build_added_to_space_reply() -> dict[str, Any]:
    """봇이 스페이스에 처음 추가됐을 때 — 홈 메뉴 카드 (하위 호환)."""
    from domains.daily_chat.home_menu import build_home_menu_card

    return build_home_menu_card()


def handle_weekly_meeting_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    user_context = _user_context(chat_event)
    space_id = _space_id(chat_event)

    # 메인 분기 — 구 메뉴는 설정 트리로 리다이렉트
    _legacy_to_settings = {
        "wm_open_team_menu",
        "wm_open_member_menu",
        "wm_open_conf_menu",
        "wm_open_schedule_menu",
        "wm_open_scheduler",
    }
    if invoked_function in _legacy_to_settings:
        from domains.settings.handler import handle_settings_action

        return handle_settings_action(
            invoked_function="st_open_team",
            parameters=parameters,
            form_inputs=form_inputs,
            chat_event=chat_event,
        )

    if invoked_function == "wm_open_menu":
        return build_weekly_meeting_menu_card(include_action_response=True)
    if invoked_function == "wm_open_schedule_menu":
        return build_schedule_menu_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_schedule_open_calendar_id":
        return build_schedule_calendar_id_card(get_team_list(), include_action_response=True)
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
        calendar_id = _calendar_id(existing)
        if not calendar_id:
            return build_schedule_result_card(
                "주간회의 일정 조회",
                [f"{team_name} 팀의 Calendar ID가 없습니다. '캘린더 ID 설정'에서 먼저 저장해 주세요."],
                include_action_response=True,
            )
        result = lookup_weekly_meeting(
            team_name=team_name,
            is_next_week=(invoked_function == "wm_schedule_meeting_next"),
            calendar_id=calendar_id,
        )
        if not result.ok:
            lines = _schedule_error_lines(result.error_kind, team_name)
            return build_schedule_result_card("주간회의 일정 조회", lines, include_action_response=True)
        title = "주간회의 일정 조회 (다음주)" if invoked_function.endswith("next") else "주간회의 일정 조회 (이번주)"
        lines = _schedule_lines(result.events)
        if not lines:
            lines = [f"{team_name} 팀 주간회의 일정이 없습니다."]
        return build_schedule_result_card(title, lines, include_action_response=True)

    if invoked_function in {"wm_schedule_vacation_this", "wm_schedule_vacation_next"}:
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        calendar_id = _calendar_id(existing)
        if not calendar_id:
            return build_schedule_result_card(
                "팀원 휴가일정 조회",
                [f"{team_name} 팀의 Calendar ID가 없습니다. '캘린더 ID 설정'에서 먼저 저장해 주세요."],
                include_action_response=True,
            )
        members = _normalize_members(existing.get("team_members"))
        keywords: list[str] = []
        for m in members:
            keywords.append(str(m.get("name") or ""))
            for nick in m.get("nickname") or []:
                keywords.append(str(nick))
            email = str(m.get("email") or "").strip()
            if email:
                keywords.append(email)
        result = lookup_member_vacation(
            member_keywords=keywords,
            is_next_week=(invoked_function == "wm_schedule_vacation_next"),
            calendar_id=calendar_id,
        )
        if not result.ok:
            lines = _schedule_error_lines(result.error_kind, team_name)
            return build_schedule_result_card("팀원 휴가일정 조회", lines, include_action_response=True)
        title = f"{team_name} 팀원 휴가일정 (다음주)" if invoked_function.endswith("next") else f"{team_name} 팀원 휴가일정 (이번주)"
        lines = _schedule_lines(result.events)
        if not lines:
            lines = [f"{team_name} 팀원 휴가일정이 없습니다."]
        return build_schedule_result_card(title, lines, include_action_response=True)

    if invoked_function == "wm_schedule_load_calendar_id":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_schedule_calendar_id_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_calendar_id=str(existing.get("calendar_id") or ""),
            current_vacation_calendar_id=str(existing.get("vacation_calendar_id") or ""),
        )

    if invoked_function == "wm_schedule_save_calendar_id":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        calendar_id = _safe_form_value(form_inputs, "calendar_id")
        vacation_calendar_id = _safe_form_value(form_inputs, "vacation_calendar_id")
        if not calendar_id and not vacation_calendar_id:
            return {"text": "Calendar ID를 하나 이상 입력해 주세요."}
        update_team_calendar_id(
            team_id=team_id,
            calendar_id=calendar_id,
            vacation_calendar_id=vacation_calendar_id,
            space_id=space_id,
            user_context=user_context,
        )
        parts = []
        if calendar_id:
            parts.append(f"주간회의: {calendar_id}")
        if vacation_calendar_id:
            parts.append(f"휴가: {vacation_calendar_id}")
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ {team_name} 팀 Calendar ID 저장: {' / '.join(parts)}"}

    if invoked_function == "wm_schedule_open_drive_ids":
        return build_schedule_drive_ids_card(get_team_list(), include_action_response=True)

    if invoked_function == "wm_schedule_load_drive_ids":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        current = existing.get("shared_drive_ids") or []
        if not isinstance(current, list):
            current = []
        return build_schedule_drive_ids_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_drive_ids=[str(x) for x in current if str(x).strip()],
        )

    if invoked_function == "wm_schedule_save_drive_ids":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw = _safe_form_value(form_inputs, "shared_drive_ids")
        ids = parse_shared_drive_ids(raw)
        granter_email = (user_context or {}).get("email") or ""

        if not ids:
            update_team_shared_drive_ids(
                team_id=team_id, shared_drive_ids=[], space_id=space_id, user_context=user_context
            )
            return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ {team_name} 팀 Shared Drive ID가 비워졌습니다."}

        per_id_lines: list[str] = []
        accessible: list[str] = []
        for d_id in ids:
            check = check_sa_member(d_id)
            if check["ok"]:
                accessible.append(d_id)
                name = check.get("name") or "(이름 없음)"
                per_id_lines.append(f"✅ {d_id} — 이미 멤버 ({name})")
                continue

            # SA 미멤버 → granter OAuth 로 자동 부여 시도
            grant = grant_sa_reader(drive_id=d_id, granter_email=granter_email, sa_email=BOT_SA_EMAIL)
            if grant["ok"]:
                accessible.append(d_id)
                per_id_lines.append(f"✅ {d_id} — 봇 SA 자동 등록 완료")
                continue

            ek = grant.get("error_kind") or ""
            if ek == "oauth_required":
                per_id_lines.append(
                    f"🔒 {d_id} — 권한 부여 실패: 본인 OAuth 동의 필요. "
                    f"메뉴의 '내 데이터 연결' 을 먼저 클릭해 권한을 부여한 뒤 다시 저장해 주세요."
                )
            elif ek == "forbidden":
                per_id_lines.append(
                    f"❌ {d_id} — 본인이 이 드라이브의 Manager 가 아니에요. "
                    f"수동으로 SA 추가가 필요합니다: {BOT_SA_EMAIL}"
                )
            elif ek == "not_found":
                per_id_lines.append(
                    f"❌ {d_id} — 드라이브 미존재 또는 본인도 멤버 아님 (ID 확인 필요)"
                )
            else:
                per_id_lines.append(f"❌ {d_id} — 권한 부여 실패: {ek or 'unknown'}")

        update_team_shared_drive_ids(
            team_id=team_id, shared_drive_ids=accessible, space_id=space_id, user_context=user_context
        )

        header = f"*{team_name} 팀 Shared Drive 저장 결과* — 접근 가능 {len(accessible)}/{len(ids)}건"
        body = "\n".join(per_id_lines)
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"{header}\n{body}"}

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
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ 팀이 추가되었습니다: {team_name} (config/{team_id})"}

    if invoked_function == "wm_team_load_edit":
        team_id = _safe_team_id(form_inputs)
        existing, team_name = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_team_edit_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_team_name=team_name,
            current_calendar_id=str(existing.get("calendar_id") or ""),
        )

    if invoked_function == "wm_team_do_edit":
        team_id = _safe_team_id(form_inputs)
        new_team_name = _safe_form_value(form_inputs, "new_team_name")
        calendar_id = _safe_form_value(form_inputs, "calendar_id")
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        existing = get_team_config(team_id) or {}
        if not existing:
            return {"text": "수정할 팀을 찾지 못했습니다."}
        current_team_name = str(existing.get("team_name") or team_id)
        target_team_name = new_team_name or current_team_name
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

        changed_fields: list[str] = []
        if new_team_name:
            changed_fields.append(f"팀 이름: {new_team_name}")
        if calendar_id:
            changed_fields.append(f"Calendar ID: {calendar_id}")
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": "✅ 팀 설정이 수정되었습니다. " + ", ".join(changed_fields)}

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
        member_email = _safe_form_value(form_inputs, "member_email")
        members = _normalize_members(existing.get("team_members"))
        members.append({"name": member_name, "nickname": nicknames, "email": member_email})
        upsert_team_config(team_id=team_id, team_name=team_name, space_id=space_id, user_context=user_context, updates={"team_members": members})
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": f"✅ {team_name} 팀에 팀원 {member_name} 님을 추가했습니다."}

    if invoked_function == "wm_tm_load_edit":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        raw_index = _safe_form_value(form_inputs, "member_index")
        if not raw_index.isdigit():
            return {"text": "팀원 번호를 입력해 주세요."}
        idx = int(raw_index) - 1
        members = _normalize_members(existing.get("team_members"))
        if idx < 0 or idx >= len(members):
            return {"text": "팀원 번호 범위를 확인해 주세요."}
        selected = members[idx]
        nicks = ", ".join(selected.get("nickname") or [])
        email = str(selected.get("email") or "").strip()
        snapshot = f"현재값: {selected.get('name','')} (닉네임: {nicks or '-'} / 이메일: {email or '-'})"
        return build_team_member_edit_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            selected_member_index=raw_index,
            current_member_name=str(selected.get("name") or ""),
            current_member_nicknames=nicks,
            current_member_email=str(selected.get("email") or ""),
            member_snapshot_text=snapshot,
        )

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
        new_email = _safe_form_value(form_inputs, "new_member_email")
        if new_name:
            members[idx]["name"] = new_name
        if new_nicknames:
            members[idx]["nickname"] = _normalize_nicknames(new_nicknames)
        if new_email:
            members[idx]["email"] = new_email
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
    if invoked_function == "wm_conf_open_edit":
        return build_confluence_edit_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_open_edit_space":
        return build_confluence_edit_space_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_load_edit":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        root_pages = existing.get("root_pages") or []
        root_text_rows: list[str] = []
        if isinstance(root_pages, list):
            for row in root_pages:
                if isinstance(row, dict):
                    page_id = str(row.get("page_id") or "").strip()
                    if page_id:
                        root_text_rows.append(page_id)
        return build_confluence_edit_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_space_key=str(existing.get("confluence_space_key") or ""),
            current_root_page_ids="\n".join(root_text_rows),
            current_template_page_url=str(existing.get("template_page_url") or ""),
        )

    if invoked_function == "wm_conf_open_edit_root":
        return build_confluence_edit_root_card(get_team_list(), include_action_response=True)
    if invoked_function == "wm_conf_open_edit_template":
        return build_confluence_edit_template_card(get_team_list(), include_action_response=True)

    if invoked_function == "wm_conf_load_edit_space":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_confluence_edit_space_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_space_key=str(existing.get("confluence_space_key") or ""),
        )

    if invoked_function == "wm_conf_load_edit_root":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        root_pages = existing.get("root_pages") or []
        root_text_rows: list[str] = []
        if isinstance(root_pages, list):
            for row in root_pages:
                if isinstance(row, dict):
                    page_id = str(row.get("page_id") or "").strip()
                    if page_id:
                        root_text_rows.append(page_id)
        return build_confluence_edit_root_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_root_page_ids="\n".join(root_text_rows),
        )

    if invoked_function == "wm_conf_load_edit_template":
        team_id = _safe_team_id(form_inputs)
        existing, _ = _team_required(team_id)
        if not existing:
            return {"text": "팀을 선택해 주세요."}
        return build_confluence_edit_template_card(
            get_team_list(),
            include_action_response=True,
            selected_team_id=team_id,
            current_template_page_url=str(existing.get("template_page_url") or ""),
        )

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

    if invoked_function == "wm_conf_do_edit":
        team_id = _safe_team_id(form_inputs)
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
                return {"text": "템플릿 URL 형식을 확인해 주세요. (/pages/{id} 또는 숫자 ID)"}
            updates["template_page_url"] = template_raw
            updates["template_page_id"] = template_page_id

        if not updates:
            return {"text": "수정할 값을 하나 이상 입력해 주세요."}

        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates=updates,
        )
        return {"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": "✅ 컨플루언스 설정이 수정되었습니다."}

    # OAuth 연결 (Phase 2 — Gmail / 개인 Calendar / 내 Drive 권한 동의)
    if invoked_function == "wm_oauth_link":
        from domains.weekly_meeting.oauth_callback import build_authorization_url, encode_state

        user_email = str((chat_event.get("user") or {}).get("email") or "")
        if not user_email:
            return {"text": "사용자 이메일을 확인하지 못했어요."}
        url = build_authorization_url(
            user_email_hint=user_email,
            state=encode_state(user_email=user_email, space_name=_space_id(chat_event)),
        )
        return build_oauth_link_card(user_email=user_email, auth_url=url, include_action_response=True)

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
        return build_scheduler_result_card(f"{team_name} 주간회의 일자는 {date_text}입니다.", include_action_response=True)

    return {"text": "지원하지 않는 카드 액션입니다."}
