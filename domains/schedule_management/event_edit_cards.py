"""기존 일정 수정·삭제 카드 — ① 편집(아무것도 안 바뀜) → ② 최종 확인 → ③ 결과(되돌리기)."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from domains.schedule_management.cards import _time_options
from domains.schedule_management.event_edit import EditPlan, EditRequest, _parse, new_times
from domains.schedule_management.series_spec import WEEKDAYS_KO

_GRAY = "#5f6368"
_RED = "#d93025"
_GREEN = "#188038"
_SCOPE_LABELS = {"this": "이 회차만", "following": "이 회차부터 이후 전체", "all": "반복 일정 전체"}


def _font(text: str, color: str) -> str:
    return f'<font color="{color}">{text}</font>'


def _params(raw: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "value": str(v)} for k, v in raw.items()]


def _button(text: str, function: str, params: dict[str, str] | None = None, *, filled: bool = False) -> dict[str, Any]:
    btn: dict[str, Any] = {
        "text": text[:80],
        "onClick": {"action": {"function": function, "parameters": _params(params or {})}},
    }
    if filled:
        btn["color"] = {"red": 0.1, "green": 0.45, "blue": 0.91, "alpha": 1}
    return btn


def _wrap(card_id: str, title: str, subtitle: str, sections: list[dict[str, Any]], *, update: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cardsV2": [{"cardId": card_id, "card": {"header": {"title": title, "subtitle": subtitle}, "sections": sections}}]
    }
    if update:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def _range_text(start: datetime, end: datetime) -> str:
    return f"{start.month}/{start.day}({WEEKDAYS_KO[start.weekday()]}) {start:%H:%M}~{end:%H:%M}"


def event_line(event: dict[str, Any], rooms_by_id: dict[str, str]) -> str:
    s, e = _parse(event.get("start")), _parse(event.get("end"))
    when = _range_text(s, e) if s and e else ""
    room = ", ".join(
        rooms_by_id.get(str(a.get("email") or ""), "") or str(a.get("displayName") or "회의실")
        for a in event.get("attendees") or [] if a.get("resource")
    ) or "회의실 없음"
    people = sum(1 for a in event.get("attendees") or [] if not a.get("resource"))
    recur = " · 🔁 반복 일정" if event.get("recurring_event_id") else ""
    org = str(event.get("organizer_email") or "")
    return (
        f"<b>{html.escape(str(event.get('summary') or '(제목 없음)'))}</b>{recur}<br>"
        f"{when} · {html.escape(room)} · 참석 {people}명" + (f" · 주최 {html.escape(org)}" if org else "")
    )


def _dropdown(name: str, label: str, items: list[dict[str, Any]], selected: str) -> dict[str, Any]:
    out = []
    for it in items:
        row = dict(it)
        if row.get("value") == selected:
            row["selected"] = True
        out.append(row)
    if selected and not any(r.get("value") == selected for r in out):
        out.append({"text": selected, "value": selected, "selected": True})
    return {"selectionInput": {"name": name, "label": label, "type": "DROPDOWN", "items": out}}


def build_edit_card(
    *,
    req: EditRequest,
    event: dict[str, Any],
    others: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    rooms_by_id: dict[str, str],
    errors: list[str] | None = None,
    include_action_response: bool = False,
) -> dict[str, Any]:
    s0, e0 = _parse(event.get("start")), _parse(event.get("end"))
    assert s0 and e0
    base = {"req": req.to_json(), "event_id": str(event.get("id") or "")}
    sections: list[dict[str, Any]] = []

    found: list[dict[str, Any]] = [{"textParagraph": {"text": event_line(event, rooms_by_id)}}]
    if others:
        found.append({"buttonList": {"buttons": [
            _button(f"다른 일정: {o.get('summary') or ''} {str(o.get('start') or '')[5:16].replace('T', ' ')}",
                    "sm_evt_open", {"req": req.to_json(), "event_id": str(o.get("id") or "")})
            for o in others[:4]
        ]}})
    sections.append({"header": "찾은 일정", "widgets": found})

    if event.get("recurring_event_id"):
        selected = req.scope or "this"
        sections.append({"header": "적용 범위", "widgets": [{"selectionInput": {
            "name": "scope", "label": "반복 일정 중 어디까지", "type": "RADIO_BUTTON",
            "items": [{"text": lbl, "value": k, "selected": k == selected} for k, lbl in _SCOPE_LABELS.items()],
        }}]})

    if req.operation == "modify":
        start = req.new_start or s0.strftime("%H:%M")
        if req.new_end:
            end = req.new_end
        else:
            mins = req.duration_minutes or int((e0 - s0).total_seconds() // 60)
            h, m = map(int, start.split(":"))
            total = h * 60 + m + mins
            end = f"{total // 60:02d}:{total % 60:02d}"
        times = [i for i in _time_options(step_min=30) if i.get("value")]
        current_rooms = [str(a.get("email") or "") for a in event.get("attendees") or [] if a.get("resource")]
        office = next((str(r.get("office") or "") for r in rooms
                       if str(r.get("calendar_resource_id") or "") in current_rooms), "")
        room_items = [{"text": "(그대로)", "value": ""}] + [
            {"text": f"{r.get('display_name') or r.get('name')} ({r.get('capacity') or '?'}인)",
             "value": str(r.get("calendar_resource_id") or "")}
            for r in rooms
            if r.get("calendar_resource_id") and (not office or str(r.get("office") or "") == office)
        ]
        new_room_id = next((str(r.get("calendar_resource_id") or "") for r in rooms
                            if req.new_room and req.new_room in (r.get("display_name"), r.get("name"))), "")
        form = [
            {"textInput": {"name": "new_date", "label": "날짜 (YYYY-MM-DD)",
                           "value": req.new_date or s0.strftime("%Y-%m-%d")}},
            _dropdown("new_start", "시작", times, start),
            _dropdown("new_end", "종료", times, end),
            _dropdown("new_room_id", "회의실", room_items, new_room_id),
            {"textInput": {"name": "new_title", "label": "제목 (비우면 그대로)", "value": req.new_title}},
        ]
        sections.append({"header": "바꿀 내용", "widgets": form})

    widgets: list[dict[str, Any]] = []
    if errors:
        widgets.append({"textParagraph": {"text": _font("<br>".join(html.escape(e) for e in errors), _RED)}})
    widgets.append({"textParagraph": {"text": _font(
        "아직 아무것도 바뀌지 않았어요. 다음 화면에서 한 번 더 확인한 뒤 실행돼요.", _GRAY)}})
    if req.operation == "modify":
        other = EditRequest.from_json(req.to_json())
        other.operation = "delete"
        buttons = [_button("변경 내용 검토", "sm_evt_review", base, filled=True),
                   _button("삭제하기로 바꾸기", "sm_evt_open", {**base, "req": other.to_json()})]
    else:
        other = EditRequest.from_json(req.to_json())
        other.operation = "modify"
        buttons = [_button("삭제 검토", "sm_evt_review", base, filled=True),
                   _button("수정하기로 바꾸기", "sm_evt_open", {**base, "req": other.to_json()})]
    buttons.append(_button("취소", "sm_evt_cancel"))
    widgets.append({"buttonList": {"buttons": buttons}})
    sections.append({"widgets": widgets})
    title = "일정 수정" if req.operation == "modify" else "일정 삭제"
    return _wrap("sm_evt_edit", title, "기존 일정", sections, update=include_action_response)


def build_review_card(
    *,
    req: EditRequest,
    plan: EditPlan,
    event: dict[str, Any],
    rooms_by_id: dict[str, str],
    room_free: bool | None,
    notice: str,
) -> dict[str, Any]:
    s0, e0 = _parse(event.get("start")), _parse(event.get("end"))
    assert s0 and e0
    lines = [f"<b>{html.escape(str(event.get('summary') or ''))}</b>"]
    scope_txt = _SCOPE_LABELS.get(plan.scope, "")
    if scope_txt:
        lines.append(f"범위: {scope_txt}")
    blocked = False
    if plan.operation == "delete":
        lines.append(_font(f"삭제: {_range_text(s0, e0)}" + (f" ({scope_txt})" if scope_txt else ""), _RED))
    else:
        start, end = new_times(event, plan)
        old_room = ", ".join(rooms_by_id.get(str(a.get("email") or ""), "회의실")
                             for a in event.get("attendees") or [] if a.get("resource")) or "없음"
        new_room = rooms_by_id.get(plan.new_room_id, "") if plan.new_room_id else old_room
        lines.append(f"변경 전: {_range_text(s0, e0)} · {html.escape(old_room)}")
        lines.append(f"변경 후: <b>{_range_text(start, end)} · {html.escape(new_room)}</b>")
        if plan.new_title:
            lines.append(f"제목: {html.escape(plan.new_title)}")
        if plan.scope in ("following", "all") and start.date() != s0.date():
            lines.append(_font(f"반복 요일도 {WEEKDAYS_KO[s0.weekday()]} → {WEEKDAYS_KO[start.weekday()]}요일로 바뀌어요.", _GRAY))
        if room_free is True:
            lines.append(_font("✓ 이 시간에 회의실이 비어 있어요", _GREEN))
        elif room_free is False:
            blocked = True
            lines.append(_font("✗ 이 시간에 회의실이 이미 예약돼 있어요. 뒤로 가서 시간이나 회의실을 바꿔 주세요.", _RED))
        if plan.scope in ("following", "all"):
            lines.append(_font("반복 회차별 회의실 가용성은 '정기회의 옮기기'로 주별 확인할 수 있어요.", _GRAY))
    cautions = [notice, "참석자 전원에게 변경 메일이 가요.",
                "실행 전에 원본을 백업해 두고, 결과 화면의 '되돌리기'로 복원할 수 있어요."]
    params = {"req": req.to_json(), **plan.params()}
    buttons = []
    if not blocked:
        buttons.append(_button("최종 확인" if plan.operation == "modify" else "최종 확인(삭제)", "sm_evt_apply",
                               params, filled=True))
    buttons += [_button("뒤로", "sm_evt_open", {"req": req.to_json(), "event_id": plan.event_id}),
                _button("취소", "sm_evt_cancel")]
    widgets = [
        {"textParagraph": {"text": "<br>".join(lines)}},
        {"textParagraph": {"text": _font("<br>".join("· " + html.escape(c) for c in cautions), _GRAY)}},
        {"buttonList": {"buttons": buttons}},
    ]
    return _wrap("sm_evt_review", "최종 확인", "아직 바뀌지 않았어요", [{"widgets": widgets}], update=True)


def build_restore_review_card(backup: dict[str, Any], backup_id: str) -> dict[str, Any]:
    created = backup.get("created_at")
    when = created.strftime("%Y-%m-%d %H:%M") if hasattr(created, "strftime") else ""
    op = {"series_rebook": "정기회의 옮기기", "edit_modify": "일정 수정", "edit_delete": "일정 삭제"}.get(
        str(backup.get("operation") or ""), str(backup.get("operation") or ""))
    lines = [
        f"<b>{html.escape(str(backup.get('summary') or ''))}</b>",
        f"작업: {html.escape(op)} · {when} UTC",
        f"복원할 원본 {len(backup.get('event_ids') or [])}건"
        + (f" · 새로 만든 일정 {len(backup.get('created_event_ids') or [])}건 삭제" if backup.get("created_event_ids") else ""),
    ]
    for line in (backup.get("result_lines") or [])[:8]:
        lines.append(_font(html.escape(str(line)), _GRAY))
    widgets = [
        {"textParagraph": {"text": "<br>".join(lines)}},
        {"textParagraph": {"text": _font("변경 전 상태로 되돌리고 참석자에게 변경 메일이 가요. "
                                         "그 사이 다른 사람이 고친 내용도 원본으로 덮어써요.", _GRAY)}},
        {"buttonList": {"buttons": [
            _button("되돌리기 최종 확인", "sm_evt_restore", {"backup_id": backup_id}, filled=True),
            _button("취소", "sm_evt_cancel"),
        ]}},
    ]
    return _wrap("sm_evt_restore", "되돌리기 확인", "아직 바뀌지 않았어요", [{"widgets": widgets}], update=True)
