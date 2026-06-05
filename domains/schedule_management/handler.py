"""캘린더 예약 핸들러 (compose 카드 v2, 2단계)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from domains.schedule_management.cards import (
    build_compose_card,
    build_reservation_menu_card,
    build_result_card,
    build_settings_card,
)
from domains.schedule_management.calendar_client import (
    calendar_error_message,
    create_event,
    delete_event,
    extract_meet_link,
    is_dry_run,
    patch_event,
    to_kst_iso,
)
from domains.schedule_management.compose_state import (
    apply_duration_mode,
    compose_state_from,
    empty_compose_state,
    resolve_end_time,
    state_to_button_params,
)
from domains.schedule_management.conversation import extract_compose_state_with_llm_fallback
from domains.schedule_management.oauth_calendar import (
    decode_calendar_selection,
    get_user_access_token,
    is_oauth_linked,
    list_user_calendars,
)
from domains.schedule_management.reservations_store import save_reservation
from domains.schedule_management.rooms import recommend_rooms
from domains.schedule_management.rooms_sync import sync_resource_rooms_from_calendar_list
from domains.weekly_meeting.oauth_callback import build_authorization_url, encode_state
from firestore.team_config import (
    get_all_members,
    get_team_config,
    get_team_list,
    normalize_team_id,
    update_team_calendar_id,
)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_SUGGESTION_RE = re.compile(r"^(.+?)\s*\(([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\)\s*$")


def _safe_team_id(form_inputs: dict[str, Any]) -> str:
    from domains.schedule_management.compose_state import _safe_form_value

    team_id = _safe_form_value(form_inputs, "team_id")
    if team_id == "__none__":
        return ""
    return normalize_team_id(team_id) if team_id else ""


def _user_context(chat_event: dict[str, Any] | None) -> dict[str, str]:
    user = (chat_event or {}).get("user") or {}
    return {
        "name": str(user.get("displayName") or ""),
        "email": str(user.get("email") or ""),
        "department": "미지정",
    }


def _space_id(chat_event: dict[str, Any] | None) -> str:
    return str(((chat_event or {}).get("space") or {}).get("name", ""))


def _oauth_url(chat_event: dict[str, Any] | None) -> str:
    ctx = _user_context(chat_event)
    return build_authorization_url(
        user_email_hint=ctx.get("email", ""),
        state=encode_state(user_email=ctx.get("email", ""), space_name=_space_id(chat_event)),
    )


def _calendar_options(chat_event: dict[str, Any] | None) -> list[dict[str, str]]:
    """OAuth 연결 시 사용자 캘린더만, 미연결 시 팀 캘린더 폴백."""
    email = _user_context(chat_event).get("email", "")
    if email and is_oauth_linked(email):
        return list_user_calendars(email)

    options: list[dict[str, str]] = [{"id": "primary", "label": "내 캘린더 (primary, OAuth 필요)"}]
    seen = {"primary"}
    for team in get_team_list():
        tid = team["id"]
        cfg = get_team_config(tid) or {}
        cal_id = str(cfg.get("calendar_id") or "").strip()
        if cal_id and cal_id not in seen:
            label = f"{team['name']} ({cal_id[:24]}…)" if len(cal_id) > 24 else f"{team['name']} ({cal_id})"
            options.append({"id": cal_id, "label": label})
            seen.add(cal_id)
    return options


def _resolve_calendar_auth(
    selected: str,
    chat_event: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """(api_calendar_id, access_token or None for SA)."""
    api_id, token_email = decode_calendar_selection(selected)
    if token_email:
        token = get_user_access_token(token_email)
        return api_id, token
    return api_id or selected, None


def _is_email(text: str) -> bool:
    return bool(_EMAIL_RE.match((text or "").strip()))


def _parse_attendee_raw(raw: str) -> tuple[str, str]:
    """suggestion '이름 (email)' 또는 이메일/이름 문자열 파싱."""
    text = (raw or "").strip()
    m = _SUGGESTION_RE.match(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if _is_email(text):
        return "", text
    return text, ""


def _find_member_matches(query: str, members: list[dict[str, Any]]) -> list[dict[str, str]]:
    name_hint, email_hint = _parse_attendee_raw(query)
    if email_hint:
        for m in members:
            if str(m.get("email") or "").strip().lower() == email_hint.lower():
                return [{"name": str(m.get("name") or "").strip(), "email": email_hint}]
        return [{"name": name_hint, "email": email_hint}]

    q = name_hint or (query or "").strip()
    if not q:
        return []
    q_lower = q.lower()
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        name = str(m.get("name") or "").strip()
        email = str(m.get("email") or "").strip()
        nicks = m.get("nickname") or []
        names = [name] + [str(n).strip() for n in nicks if str(n).strip()]
        for n in names:
            if q == n or q_lower == n.lower() or (len(q) >= 2 and q in n):
                if email and email not in seen:
                    matches.append({"name": name, "email": email})
                    seen.add(email)
                break
        if email and (q_lower in email.lower()) and email not in seen:
            matches.append({"name": name, "email": email})
            seen.add(email)
    return matches


def _ensure_calendar_id(state: dict[str, Any], chat_event: dict[str, Any] | None) -> None:
    if state.get("calendar_id"):
        return
    opts = _calendar_options(chat_event)
    if opts:
        state["calendar_id"] = opts[0]["id"]


def _render_compose(
    state: dict[str, Any],
    *,
    chat_event: dict[str, Any] | None = None,
    pending_candidates: list[dict[str, str]] | None = None,
    include_action_response: bool = False,
    members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ctx = _user_context(chat_event)
    email = ctx.get("email", "")
    _ensure_calendar_id(state, chat_event)
    apply_duration_mode(state)
    api_cal, token = _resolve_calendar_auth(str(state.get("calendar_id") or ""), chat_event)
    rooms = recommend_rooms(
        state,
        max_n=3,
        duration_minutes=int(state.get("duration_minutes") or 60),
        access_token=token or (get_user_access_token(email) if email else None),
    )
    linked = bool(email and is_oauth_linked(email))
    return build_compose_card(
        state,
        calendar_options=_calendar_options(chat_event),
        recommended_rooms=rooms,
        members=members if members is not None else get_all_members(),
        pending_candidates=pending_candidates,
        oauth_linked=linked,
        oauth_url=_oauth_url(chat_event),
        include_action_response=include_action_response,
    )


def _merge_extracted_state(base: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key in ("meeting_date", "meeting_time", "title"):
        if extracted.get(key) and not out.get(key):
            out[key] = extracted[key]
    if extracted.get("attendees"):
        existing_emails = {a.get("email") for a in out.get("attendees") or []}
        for a in extracted["attendees"]:
            if a.get("email") not in existing_emails:
                out.setdefault("attendees", []).append(a)
    if extracted.get("equipment_keywords"):
        out["equipment_keywords"] = list(
            set((out.get("equipment_keywords") or []) + extracted["equipment_keywords"])
        )
    for k in ("location_keyword", "room_name_keyword"):
        if extracted.get(k) and not out.get(k):
            out[k] = extracted[k]
    if extracted.get("auto_meet"):
        out["want_meet"] = True
    if extracted.get("duration_minutes") and not out.get("duration_minutes"):
        out["duration_minutes"] = extracted["duration_minutes"]
        dm = extracted["duration_minutes"]
        if dm == 120:
            out["duration_mode"] = "2h"
        elif dm != 60:
            out["duration_mode"] = "custom"
    return out


def _validate_time_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not state.get("meeting_date"):
        errors.append("날짜를 선택해 주세요.")
    if not state.get("meeting_time"):
        errors.append("시작 시간을 선택해 주세요.")
    mode = str(state.get("duration_mode") or "1h")
    if mode == "custom":
        end = str(state.get("meeting_end_time") or "").strip()
        if not end:
            errors.append("종료 시간을 선택해 주세요.")
        else:
            try:
                date = str(state["meeting_date"])
                start_dt = datetime.strptime(f"{date} {state['meeting_time']}", "%Y-%m-%d %H:%M")
                end_dt = datetime.strptime(f"{date} {end}", "%Y-%m-%d %H:%M")
                if end_dt <= start_dt:
                    errors.append("종료 시간은 시작 시간보다 뒤여야 합니다.")
            except ValueError:
                errors.append("시간 형식이 올바르지 않습니다.")
    return errors


def handle_schedule_management(
    user_message: str,
    chat_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    members = get_all_members()
    extracted = extract_compose_state_with_llm_fallback(user_message, members=members)
    state = _merge_extracted_state(empty_compose_state(), extracted)
    state["compose_step"] = "quick"
    _ensure_calendar_id(state, chat_event)
    out = _render_compose(state, chat_event=chat_event, members=members)
    out["text"] = "간편 예약 화면입니다."
    return out


def handle_schedule_management_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    from domains.schedule_management.compose_state import _safe_form_value

    user_context = _user_context(chat_event)
    space_id = _space_id(chat_event)
    members = get_all_members()

    if invoked_function == "sm_open_menu":
        return build_reservation_menu_card(include_action_response=True)

    if invoked_function == "sm_open_settings":
        return build_settings_card(get_team_list(), include_action_response=True)

    if invoked_function == "sm_sync_rooms":
        count, msg = sync_resource_rooms_from_calendar_list()
        return build_result_card(
            title="회의실 동기화",
            lines=[msg, f"동기화 건수: {count}"],
            include_action_response=True,
        )

    if invoked_function == "sm_settings_save_calendar":
        team_id = _safe_team_id(form_inputs)
        if not team_id:
            return {"text": "팀을 선택해 주세요."}
        existing = get_team_config(team_id)
        if not existing:
            return {"text": "팀을 찾지 못했습니다."}
        calendar_id = _safe_form_value(form_inputs, "calendar_id")
        if not calendar_id:
            return {"text": "Calendar ID를 입력해 주세요."}
        team_name = str(existing.get("team_name") or team_id)
        update_team_calendar_id(
            team_id=team_id,
            calendar_id=calendar_id,
            space_id=space_id,
            user_context=user_context,
        )
        return {
            "actionResponse": {"type": "UPDATE_MESSAGE"},
            "text": f"✅ {team_name} 팀 캘린더 ID 저장: {calendar_id}",
        }

    if invoked_function == "sm_open_compose":
        state = compose_state_from(parameters, form_inputs)
        if not state.get("compose_step"):
            state["compose_step"] = "quick"
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    state = compose_state_from(parameters, form_inputs)
    pending: list[dict[str, str]] | None = None

    if invoked_function in ("sm_compose_refresh", "sm_compose_quick_update"):
        state["compose_step"] = "quick"
        state["errors"] = []
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_back_quick":
        state["compose_step"] = "quick"
        state["errors"] = []
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_add_attendee":
        state["compose_step"] = "full"
        raw = _safe_form_value(form_inputs, "attendee_input")
        state["errors"] = []
        if not raw:
            state["errors"] = ["참석자 이름 또는 이메일을 입력해 주세요."]
            return _render_compose(
                state, chat_event=chat_event, include_action_response=True, members=members
            )
        name_hint, email_hint = _parse_attendee_raw(raw)
        if email_hint:
            entry = {"name": name_hint, "email": email_hint}
            if not any(a.get("email") == email_hint for a in state.get("attendees") or []):
                state.setdefault("attendees", []).append(entry)
        else:
            matches = _find_member_matches(raw, members)
            if len(matches) == 1:
                m = matches[0]
                if not any(a.get("email") == m["email"] for a in state.get("attendees") or []):
                    state.setdefault("attendees", []).append(m)
            elif len(matches) > 1:
                pending = matches
            else:
                state["errors"] = ["등록된 이름이 없습니다. 이메일 형식으로 입력해 주세요."]
        return _render_compose(
            state,
            chat_event=chat_event,
            pending_candidates=pending,
            include_action_response=True,
            members=members,
        )

    if invoked_function == "sm_compose_pick_attendee":
        state["compose_step"] = "full"
        email = parameters.get("pick_email", "").strip()
        name = parameters.get("pick_name", "").strip()
        if email and not any(a.get("email") == email for a in state.get("attendees") or []):
            state.setdefault("attendees", []).append({"name": name, "email": email})
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_remove_attendee":
        state["compose_step"] = "full"
        try:
            idx = int(parameters.get("remove_index", "-1"))
        except ValueError:
            idx = -1
        attendees = list(state.get("attendees") or [])
        if 0 <= idx < len(attendees):
            attendees.pop(idx)
            state["attendees"] = attendees
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_create_meet":
        state["compose_step"] = "full"
        state["want_meet"] = True
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_remove_meet":
        state["compose_step"] = "full"
        state["want_meet"] = False
        state["meet_url"] = ""
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_pick_room":
        state["picked_room_id"] = parameters.get("picked_room_id", "")
        state["picked_room_name"] = parameters.get("picked_room_name", "")
        state["compose_step"] = "full"
        state["errors"] = []
        _ensure_calendar_id(state, chat_event)
        return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

    if invoked_function == "sm_compose_confirm":
        state["compose_step"] = "full"
        errors: list[str] = []
        if str(state.get("compose_step") or "") != "full":
            errors.append("회의실을 선택한 뒤 본 예약에서 확정해 주세요.")
        if not state.get("picked_room_id"):
            errors.append("회의실을 선택해 주세요.")
        if not state.get("calendar_id"):
            errors.append("캘린더를 선택해 주세요.")
        errors.extend(_validate_time_state(state))
        if not state.get("title"):
            errors.append("제목을 입력해 주세요.")
        if errors:
            state["errors"] = errors
            return _render_compose(state, chat_event=chat_event, include_action_response=True, members=members)

        api_cal, access_token = _resolve_calendar_auth(str(state["calendar_id"]), chat_event)
        apply_duration_mode(state)
        duration = int(state.get("duration_minutes") or 60)
        end_time = resolve_end_time(state)
        if not end_time:
            end_time = _end_time_from_start(
                str(state["meeting_date"]), str(state["meeting_time"]), duration
            )
        start_iso = to_kst_iso(str(state["meeting_date"]), str(state["meeting_time"]))
        end_iso = to_kst_iso(str(state["meeting_date"]), end_time)

        attendee_emails = [
            str(a.get("email") or "").strip()
            for a in (state.get("attendees") or [])
            if str(a.get("email") or "").strip()
        ]
        location = str(state.get("picked_room_name") or "").strip()
        want_meet = bool(state.get("want_meet") or state.get("auto_meet"))
        existing_event_id = str(state.get("last_event_id") or "").strip()
        existing_api_cal = str(state.get("last_api_calendar_id") or "").strip()

        if existing_event_id and existing_api_cal:
            result = patch_event(
                calendar_id=existing_api_cal,
                event_id=existing_event_id,
                summary=str(state.get("title")),
                start_iso=start_iso,
                end_iso=end_iso,
                location=location,
                access_token=access_token,
            )
        else:
            result = create_event(
                calendar_id=api_cal,
                summary=str(state.get("title")),
                start_iso=start_iso,
                end_iso=end_iso,
                attendees=attendee_emails,
                location=location,
                description="AllMeet 예약",
                add_google_meet=want_meet,
                access_token=access_token,
            )
        if not result.ok:
            return build_result_card(
                title="예약 실패",
                lines=[calendar_error_message(result)],
                include_action_response=True,
            )

        event = result.created_event or {}
        meet_link = extract_meet_link(event) or str(state.get("meet_url") or "")
        html_link = str(event.get("htmlLink") or "")
        event_id = str(event.get("id") or "") or existing_event_id

        lines = []
        is_update = bool(existing_event_id and existing_api_cal)
        if is_dry_run():
            lines.append("(드라이런) 실제 캘린더에는 저장되지 않았습니다.")
        elif is_update:
            lines.append("예약이 변경되었습니다.")
        else:
            lines.append("예약이 생성되었습니다.")
        lines.extend(
            [
                f"캘린더: {state.get('calendar_id')}",
                f"제목: {state.get('title')}",
                f"일시: {state.get('meeting_date')} {state.get('meeting_time')} ~ {end_time}",
            ]
        )
        ac = state.get("attendee_count")
        if ac:
            lines.append(f"참석 인원: {ac}명")
        if attendee_emails:
            lines.append(f"참석자: {', '.join(attendee_emails)}")
        if meet_link:
            lines.append(f"Meet: {meet_link}")
        if html_link:
            lines.append(f"일정 링크: {html_link}")
        if location:
            lines.append(f"회의실: {location}")

        cancel_params: dict[str, str] = {}
        store_cal = existing_api_cal if is_update else api_cal
        if event_id and not is_dry_run():
            if not is_update:
                res_id = save_reservation(
                    user_email=user_context.get("email", ""),
                    calendar_id=store_cal,
                    event_id=event_id,
                    summary=str(state.get("title")),
                    start_iso=start_iso,
                    end_iso=end_iso,
                    html_link=html_link,
                    extra={"meet_link": meet_link},
                )
                cancel_params["last_reservation_id"] = res_id
            cancel_params.update(
                {
                    "last_event_id": event_id,
                    "last_api_calendar_id": store_cal,
                    "calendar_id": str(state.get("calendar_id")),
                }
            )

        return build_result_card(
            title="예약 확정",
            lines=lines,
            include_action_response=True,
            cancel_params=cancel_params,
        )

    if invoked_function == "sm_cancel_reservation":
        api_cal = parameters.get("last_api_calendar_id") or ""
        event_id = parameters.get("last_event_id") or ""
        if not api_cal or not event_id:
            return build_result_card(
                title="취소 실패",
                lines=["취소할 예약 정보가 없습니다."],
                include_action_response=True,
            )
        _, access_token = _resolve_calendar_auth(
            parameters.get("calendar_id", ""), chat_event
        )
        del_result = delete_event(
            calendar_id=api_cal,
            event_id=event_id,
            access_token=access_token,
        )
        if not del_result.ok:
            return build_result_card(
                title="취소 실패",
                lines=[calendar_error_message(del_result)],
                include_action_response=True,
            )
        msg = "(드라이런) 취소 시뮬레이션" if is_dry_run() else "예약이 취소되었습니다."
        return build_result_card(title="예약 취소", lines=[msg], include_action_response=True)

    return build_result_card(
        title="알 수 없는 요청",
        lines=[f"액션: {invoked_function}"],
        include_action_response=True,
    )


def _end_time_from_start(date: str, start_time: str, duration_minutes: int) -> str:
    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=max(duration_minutes, 10))
    return end_dt.strftime("%H:%M")
