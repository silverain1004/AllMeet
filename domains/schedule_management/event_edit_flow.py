"""기존 일정 수정·삭제 / 되돌리기 진입점 (sm_evt_*).

단계: 자연어 → ① 편집 카드(sm_evt_open) → ② 최종 확인(sm_evt_review: 권한·회의실 확인)
     → ③ 실행(sm_evt_apply: 권한 재확인 → 원본 백업 → 변경) → 결과(되돌리기: sm_evt_restore_review → sm_evt_restore).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from domains.schedule_management.calendar_client import KST
from domains.schedule_management.cards import build_result_card
from domains.schedule_management.compose_state import _safe_form_value
from domains.schedule_management.event_auth import auth_error_message, resolve_write_auth
from domains.schedule_management.event_backup import (
    get_backup,
    mark_backup,
    restore_backup,
    save_backup,
    snapshot_events,
)
from domains.schedule_management.event_edit import (
    EditPlan,
    EditRequest,
    apply_edit,
    backup_ids,
    check_room_free,
    extract_edit_request,
    find_target_events,
    load_event,
)
from domains.schedule_management.event_edit_cards import (
    build_edit_card,
    build_restore_review_card,
    build_review_card,
)
from domains.schedule_management.oauth_calendar import get_user_access_token
from domains.schedule_management.rooms_store import get_rooms
from domains.schedule_management.series_cards import build_change_result_card
from domains.schedule_management.series_flow import _user, oauth_card, requester_token
from firestore.team_config import get_all_members

logger = logging.getLogger(__name__)


def _rooms_by_id(rooms: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(r.get("calendar_resource_id") or ""): str(r.get("display_name") or r.get("name") or "")
        for r in rooms
    }


def _people(event: dict[str, Any]) -> list[str]:
    return [str(a.get("email") or "") for a in event.get("attendees") or [] if not a.get("resource")]


def _auth(event: dict[str, Any], email: str, members: list[dict[str, Any]]):
    return resolve_write_auth(
        requester_email=email,
        organizer_email=str(event.get("organizer_email") or ""),
        organizer_self=bool(event.get("organizer_self")),
        attendee_emails=_people(event),
        members=members,
    )


def build_event_edit_from_message(user_message: str, chat_event: dict[str, Any] | None) -> dict[str, Any]:
    token = requester_token(chat_event)
    if not token:
        return oauth_card(chat_event, include_action_response=False)
    members = get_all_members()
    rooms = get_rooms()
    req = extract_edit_request(
        user_message,
        members=members,
        now=datetime.now(KST),
        room_names=[str(r.get("display_name") or "") for r in rooms if r.get("display_name")],
    )
    logger.info("event edit request=%s", req.to_json())
    found = find_target_events(req, access_token=token, rooms=rooms)
    if not found:
        return build_result_card(
            title="바꿀 일정을 찾지 못했어요",
            lines=[
                "날짜와 일정 제목의 핵심어를 같이 적어 주세요.",
                "예) \"내일 3시 ERP2 회의 4시로 옮겨줘\", \"주간회의 10/15만 취소해줘\"",
            ],
        )
    out = build_edit_card(
        req=req, event=found[0], others=found[1:], rooms=rooms, rooms_by_id=_rooms_by_id(rooms)
    )
    out["text"] = "일정 수정·삭제 화면입니다."
    return out


def _req_from_form(req: EditRequest, form_inputs: dict[str, Any]) -> EditRequest:
    """편집 카드에서 고친 값을 요청에 반영 — '뒤로' 눌러도 입력이 남도록."""
    for key in ("new_date", "new_start", "new_end", "new_title"):
        if key in form_inputs:
            setattr(req, key, _safe_form_value(form_inputs, key))
    if "scope" in form_inputs:
        req.scope = _safe_form_value(form_inputs, "scope")
    return req


def _plan_from(req: EditRequest, event: dict[str, Any], form_inputs: dict[str, Any]) -> tuple[EditPlan, list[str]]:
    errors: list[str] = []
    recurring = str(event.get("recurring_event_id") or "")
    scope = (req.scope or "this") if recurring else "single"
    plan = EditPlan(operation=req.operation, scope=scope, event_id=str(event.get("id") or ""), series_id=recurring)
    if req.operation == "modify":
        plan.new_date = req.new_date
        if plan.new_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan.new_date):
            errors.append("날짜는 YYYY-MM-DD 형식으로 적어 주세요.")
        plan.new_start, plan.new_end = req.new_start, req.new_end
        plan.new_room_id = _safe_form_value(form_inputs, "new_room_id") if "new_room_id" in form_inputs else ""
        plan.new_title = req.new_title
        if plan.new_start and plan.new_end and plan.new_end <= plan.new_start:
            errors.append("종료 시각은 시작보다 뒤여야 해요.")
    return plan, errors


def handle_event_edit_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    if invoked_function == "sm_evt_cancel":
        return build_result_card(title="변경하지 않았어요", lines=["일정은 그대로예요."], include_action_response=True)
    token = requester_token(chat_event)
    if not token:
        return oauth_card(chat_event, include_action_response=True)
    email, _ = _user(chat_event)
    members = get_all_members()

    if invoked_function in ("sm_evt_restore_review", "sm_evt_restore"):
        return _handle_restore(invoked_function, parameters.get("backup_id") or "", email=email, token=token)

    rooms = get_rooms()
    rooms_by_id = _rooms_by_id(rooms)
    req = _req_from_form(EditRequest.from_json(parameters.get("req") or ""), form_inputs)
    event = load_event(parameters.get("event_id") or "", access_token=token)
    if event is None:
        return build_result_card(
            title="일정을 찾지 못했어요", lines=["이미 삭제됐거나 바뀌었을 수 있어요."], include_action_response=True
        )

    if invoked_function == "sm_evt_open":
        return build_edit_card(req=req, event=event, others=[], rooms=rooms, rooms_by_id=rooms_by_id,
                               include_action_response=True)

    if invoked_function == "sm_evt_review":
        plan, errors = _plan_from(req, event, form_inputs)
        if plan.new_room_id:
            req.new_room = rooms_by_id.get(plan.new_room_id, "")  # '뒤로' 시 선택 유지
        if req.operation == "modify" and not errors and not (
            plan.new_room_id or plan.new_title
            or (plan.new_date and plan.new_date != str(event.get("start") or "")[:10])
            or (plan.new_start and plan.new_start != str(event.get("start") or "")[11:16])
            or (plan.new_end and plan.new_end != str(event.get("end") or "")[11:16])
        ):
            errors.append("바뀐 내용이 없어요. 날짜·시간·회의실·제목 중 하나를 바꿔 주세요.")
        if errors:
            return build_edit_card(req=req, event=event, others=[], rooms=rooms, rooms_by_id=rooms_by_id,
                                   errors=errors, include_action_response=True)
        auth = _auth(event, email, members)
        if not auth.ok:
            return build_result_card(title="실행할 수 없어요",
                                     lines=[auth_error_message(auth, str(event.get("organizer_email") or ""))],
                                     include_action_response=True)
        room_free = check_room_free(event, plan, access_token=token) if plan.scope in ("single", "this") else None
        return build_review_card(req=req, plan=plan, event=event, rooms_by_id=rooms_by_id, room_free=room_free,
                                 notice=auth.notice(str(event.get("organizer_email") or "")))

    if invoked_function == "sm_evt_apply":
        plan = EditPlan.from_params(parameters)
        auth = _auth(event, email, members)
        if not auth.ok:
            return build_result_card(title="실행할 수 없어요",
                                     lines=[auth_error_message(auth, str(event.get("organizer_email") or ""))],
                                     include_action_response=True)
        if plan.operation == "modify" and plan.scope in ("single", "this"):
            if check_room_free(event, plan, access_token=token) is False:
                return build_review_card(req=req, plan=plan, event=event, rooms_by_id=rooms_by_id,
                                         room_free=False, notice=auth.notice(str(event.get("organizer_email") or "")))
        try:
            snapshot = snapshot_events(backup_ids(plan), calendar_id="primary", access_token=auth.token)
            if not snapshot:
                raise RuntimeError("원본 이벤트를 읽지 못함")
            backup_id = save_backup(
                requester_email=email,
                acting_as=auth.acting_as,
                operation=f"edit_{plan.operation}",
                calendar_id="primary",
                events=snapshot,
                summary=str(event.get("summary") or ""),
            )
        except Exception:
            logger.exception("일정 수정 백업 실패 event=%s", plan.event_id)
            return build_result_card(
                title="백업에 실패해 변경하지 않았어요",
                lines=["원본을 저장하지 못해 안전을 위해 중단했어요. 잠시 후 다시 시도해 주세요."],
                include_action_response=True,
            )
        result = apply_edit(event, plan, rooms=rooms, access_token=auth.token)
        mark_backup(backup_id, status="applied" if result.done else "failed",
                    result_lines=result.done + result.failed, created_event_ids=result.created_event_ids)
        logger.info("event edit applied event=%s op=%s scope=%s by=%s as=%s via=%s ok=%s backup=%s",
                    plan.event_id, plan.operation, plan.scope, email, auth.acting_as, auth.via, result.ok, backup_id)
        notes = list(result.notes)
        if auth.on_behalf:
            notes.insert(0, f"주최자({auth.acting_as}) 권한으로 실행했어요.")
        if not result.ok and auth.via == "guest":
            notes.append("주최자가 참석자 수정을 막아 둔 일정일 수 있어요. 주최자가 AllMeet 캘린더를 연결하면 대신 실행할 수 있어요.")
        return build_change_result_card(
            done=result.done, failed=result.failed, notes=notes, subtitle="기존 일정",
            ok_title="일정을 삭제했어요" if plan.operation == "delete" else "일정을 바꿨어요",
            backup_id=backup_id, html_link=result.html_link, changed=bool(result.done),
        )

    return {"text": "알 수 없는 요청입니다."}


def _handle_restore(invoked_function: str, backup_id: str, *, email: str, token: str) -> dict[str, Any]:
    backup = get_backup(backup_id)
    if not backup:
        return build_result_card(title="백업을 찾지 못했어요", lines=[backup_id], include_action_response=True)
    allowed = {str(backup.get("requester_email") or "").lower(), str(backup.get("acting_as") or "").lower()}
    if email.lower() not in allowed:
        return build_result_card(title="되돌릴 수 없어요",
                                 lines=["이 변경을 실행한 사람(또는 대신 실행된 주최자)만 되돌릴 수 있어요."],
                                 include_action_response=True)
    if backup.get("status") == "restored":
        return build_result_card(title="이미 되돌렸어요", lines=[str(backup.get("summary") or "")],
                                 include_action_response=True)
    if invoked_function == "sm_evt_restore_review":
        return build_restore_review_card(backup, backup_id)
    acting_as = str(backup.get("acting_as") or "")
    write_token = token if acting_as.lower() == email.lower() else get_user_access_token(acting_as)
    if not write_token:
        return build_result_card(title="되돌릴 수 없어요",
                                 lines=[f"{acting_as} 의 캘린더 연결이 끊겨 원래 권한으로 복원할 수 없어요."],
                                 include_action_response=True)
    result = restore_backup(backup, access_token=write_token)
    mark_backup(backup_id, status="restored" if result.ok else "restore_failed",
                result_lines=result.done + result.failed)
    logger.info("event backup restore id=%s by=%s ok=%s", backup_id, email, result.ok)
    return build_change_result_card(
        done=result.done, failed=result.failed, notes=[], subtitle="되돌리기",
        ok_title="원래대로 되돌렸어요", changed=False,
    )
