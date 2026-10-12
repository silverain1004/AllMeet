"""정기회의 재배치 진입점 — 자연어 요청과 카드 클릭(sm_series_*)을 받아 카드를 돌려준다.

단계: ① 검토 카드(아무것도 바뀌지 않음) → ② 최종 확인 카드(sm_series_review, 변경 요약·실행 계정·백업 안내)
     → ③ 실행(sm_series_confirm: 권한 확인 → 원본 백업 → 변경) → 결과 카드(되돌리기).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from domains.schedule_management.calendar_client import KST
from domains.schedule_management.cards import build_result_card
from domains.schedule_management.event_auth import (
    WriteAuth,
    auth_error_message,
    is_permitted,
    resolve_write_auth,
)
from domains.schedule_management.event_backup import mark_backup, save_backup, snapshot_events
from domains.schedule_management.oauth_calendar import get_user_access_token, is_oauth_linked
from domains.schedule_management.rooms_store import get_rooms
from domains.schedule_management.series_cards import (
    build_series_rebook_card,
    build_series_result_card,
    build_series_review_card,
)
from domains.schedule_management.series_rebook import (
    RebookPlan,
    SeriesCandidate,
    SlotKey,
    apply_rebook,
    candidate_rooms,
    fetch_week_busy,
    find_series_candidates,
    solve,
)
from domains.schedule_management.series_spec import SeriesRebookSpec, extract_series_spec
from firestore.team_config import get_all_members

logger = logging.getLogger(__name__)


def _user(chat_event: dict[str, Any] | None) -> tuple[str, str]:
    user = (chat_event or {}).get("user") or {}
    return str(user.get("email") or ""), str(user.get("displayName") or "")


def oauth_card(chat_event: dict[str, Any] | None, *, include_action_response: bool) -> dict[str, Any]:
    from domains.schedule_management.handler import _oauth_url

    out = build_result_card(
        title="내 캘린더 연결이 필요해요",
        lines=["기존 일정을 찾고 수정하려면 내 Google 캘린더를 먼저 연결해 주세요."],
        include_action_response=include_action_response,
    )
    url = _oauth_url(chat_event)
    if url:
        widgets = out["cardsV2"][0]["card"]["sections"][0]["widgets"]
        widgets[-1]["buttonList"]["buttons"].insert(
            0, {"text": "내 캘린더 연결", "onClick": {"openLink": {"url": url}}}
        )
    return out


def requester_token(chat_event: dict[str, Any] | None) -> str | None:
    email, _ = _user(chat_event)
    if not email or not is_oauth_linked(email):
        return None
    return get_user_access_token(email)


def build_series_rebook_from_message(
    user_message: str,
    chat_event: dict[str, Any] | None,
) -> dict[str, Any]:
    token = requester_token(chat_event)
    if not token:
        return oauth_card(chat_event, include_action_response=False)
    email, _ = _user(chat_event)
    members = get_all_members()
    rooms = get_rooms()
    room_names = [str(r.get("display_name") or r.get("name") or "") for r in rooms]
    spec = extract_series_spec(
        user_message,
        members=members,
        sender_email=email,
        today=datetime.now(KST).date(),
        room_names=[n for n in room_names if n],
    )
    logger.info("series rebook spec=%s", spec.to_json())
    out = _render(spec, chat_event=chat_event, token=token, members=members, rooms=rooms)
    out.setdefault("text", "정기회의 옮기기 화면입니다.")
    return out


def _not_found_card(spec: SeriesRebookSpec, *, include_action_response: bool) -> dict[str, Any]:
    return build_result_card(
        title="기존 반복 일정을 찾지 못했어요",
        lines=[
            f"'{spec.title_keyword}'이(가) 들어간 반복 일정이 {spec.effective_from} ~ {spec.horizon_end} 사이 "
            "내 캘린더에 없어요.",
            "반복 일정 제목의 핵심어를 넣어 다시 요청해 주세요. 예) \"ERP2팀 주간회의 10월부터 다른 회의실로 옮겨줘\"",
        ],
        include_action_response=include_action_response,
    )


def _plan_for(
    spec: SeriesRebookSpec,
    series: SeriesCandidate,
    *,
    token: str,
    members: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    choice: SlotKey | None,
    fresh: bool = False,
) -> RebookPlan:
    usable = candidate_rooms(spec, series, rooms)
    names = {str(m.get("email") or "").lower(): str(m.get("name") or "") for m in members}
    busy = fetch_week_busy(
        sorted({o.monday for o in series.occurrences}),
        room_ids=[str(r.get("calendar_resource_id") or "") for r in usable],
        people=series.people,
        rooms=rooms,
        access_token=token,
        fresh=fresh,
    )
    return solve(spec, series, usable, busy, choice=choice, name_of=names)


def _rooms_by_id(rooms: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(r.get("calendar_resource_id") or ""): str(r.get("display_name") or r.get("name") or "")
        for r in rooms
    }


def _auth_for(series: SeriesCandidate, email: str, members: list[dict[str, Any]]) -> WriteAuth:
    return resolve_write_auth(
        requester_email=email,
        organizer_email=series.organizer_email,
        organizer_self=series.organizer_self,
        attendee_emails=series.people,
        members=members,
    )


def _render(
    spec: SeriesRebookSpec,
    *,
    chat_event: dict[str, Any] | None,
    token: str,
    members: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    series_id: str = "",
    choice: SlotKey | None = None,
    include_action_response: bool = False,
) -> dict[str, Any]:
    email, _ = _user(chat_event)
    candidates = find_series_candidates(spec, access_token=token, rooms=rooms)
    if not candidates:
        return _not_found_card(spec, include_action_response=include_action_response)
    series = next((c for c in candidates if c.series_id == series_id), candidates[0])
    others = [c for c in candidates if c.series_id != series.series_id]
    plan = _plan_for(spec, series, token=token, members=members, rooms=rooms, choice=choice)
    permitted = series.organizer_self or is_permitted(
        requester_email=email,
        organizer_email=series.organizer_email,
        attendee_emails=series.people,
        members=members,
    )
    return build_series_rebook_card(
        spec=spec,
        series=series,
        plan=plan,
        rooms_by_id=_rooms_by_id(rooms),
        others=others,
        can_edit=permitted,
        include_action_response=include_action_response,
    )


def handle_series_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    if invoked_function == "sm_series_cancel":
        return build_result_card(
            title="변경하지 않았어요", lines=["기존 일정은 그대로예요."], include_action_response=True
        )
    token = requester_token(chat_event)
    if not token:
        return oauth_card(chat_event, include_action_response=True)
    spec = SeriesRebookSpec.from_json(parameters.get("spec") or "")
    if not spec.effective_from or not spec.horizon_end:
        return build_result_card(
            title="요청 정보가 만료됐어요", lines=["처음부터 다시 요청해 주세요."], include_action_response=True
        )
    series_id = parameters.get("series_id") or ""
    choice = SlotKey.decode(parameters.get("choice") or "")
    members = get_all_members()
    rooms = get_rooms()
    email, _ = _user(chat_event)

    if invoked_function == "sm_series_pick":
        return _render(
            spec, chat_event=chat_event, token=token, members=members, rooms=rooms,
            series_id=series_id, choice=choice, include_action_response=True,
        )

    if invoked_function not in ("sm_series_review", "sm_series_confirm"):
        return {"text": "알 수 없는 요청입니다."}

    candidates = find_series_candidates(spec, access_token=token, rooms=rooms)
    series = next((c for c in candidates if c.series_id == series_id), None)
    if series is None:
        return build_result_card(
            title="일정을 다시 찾지 못했어요",
            lines=["그 사이 일정이 바뀌었을 수 있어요. 처음부터 다시 요청해 주세요."],
            include_action_response=True,
        )
    auth = _auth_for(series, email, members)
    if not auth.ok:
        return build_result_card(
            title="실행할 수 없어요",
            lines=[auth_error_message(auth, series.organizer_email)],
            include_action_response=True,
        )
    # 최종 확인 직전·실행 직전 모두 캐시 없이 다시 조회 — 카드를 보는 사이 예약이 생겼을 수 있다.
    plan = _plan_for(spec, series, token=token, members=members, rooms=rooms, choice=choice, fresh=True)
    if plan.chosen is None or (choice is not None and plan.chosen.key != choice):
        # 고른 시간이 그 사이 한 주도 안 되게 됐다 — 임의 대안으로 진행하지 않고 새 후보를 다시 보여 준다.
        return _render(
            spec, chat_event=chat_event, token=token, members=members, rooms=rooms,
            series_id=series_id, include_action_response=True,
        )

    if invoked_function == "sm_series_review":
        return build_series_review_card(
            spec=spec, series=series, plan=plan, notice=auth.notice(series.organizer_email),
        )

    # sm_series_confirm — 원본 백업이 저장돼야만 실행한다.
    occurrence_ids = [w.instance_id for w in plan.weeks]
    try:
        snapshot = snapshot_events(
            [series.series_id, *occurrence_ids], calendar_id="primary", access_token=auth.token
        )
        backup_id = save_backup(
            requester_email=email,
            acting_as=auth.acting_as,
            operation="series_rebook",
            calendar_id="primary",
            events=snapshot,
            summary=series.summary,
        )
    except Exception:
        logger.exception("정기회의 재배치 백업 실패 series=%s", series.series_id)
        return build_result_card(
            title="백업에 실패해 변경하지 않았어요",
            lines=["원본을 저장하지 못해 안전을 위해 중단했어요. 잠시 후 다시 시도해 주세요."],
            include_action_response=True,
        )
    result = apply_rebook(series, plan, rooms=rooms, access_token=auth.token)
    mark_backup(
        backup_id,
        status="applied" if result.done or result.ok else "failed",
        result_lines=result.done + result.failed,
        created_event_ids=result.created_event_ids,
    )
    logger.info(
        "series rebook applied series=%s by=%s as=%s via=%s mode=%s ok=%s backup=%s",
        series.series_id, email, auth.acting_as, auth.via, result.mode, result.ok, backup_id,
    )
    if auth.on_behalf:
        result.notes.insert(0, f"주최자({auth.acting_as}) 권한으로 실행했어요.")
    return build_series_result_card(result, backup_id=backup_id)
