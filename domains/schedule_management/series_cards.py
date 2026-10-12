"""정기회의 재배치 카드 — '이렇게 이해했어요' + 공통 시간 후보 + 주별 배정표 + 승인."""

from __future__ import annotations

import html
from datetime import timedelta
from typing import Any

from domains.schedule_management.series_rebook import (
    ApplyResult,
    CommonOption,
    RebookPlan,
    SeriesCandidate,
    WeekAssignment,
    _at,
)
from domains.schedule_management.series_spec import WEEKDAYS_KO, SeriesRebookSpec

_ICON_EVENT = "https://www.gstatic.com/images/icons/material/system/2x/event_repeat_gm_blue_24dp.png"
_GRAY = "#5f6368"
_ORANGE = "#e37400"
_RED = "#d93025"


def _params(spec: SeriesRebookSpec, series_id: str, choice: str = "") -> list[dict[str, str]]:
    raw = {"spec": spec.to_json(), "series_id": series_id, "choice": choice}
    return [{"key": k, "value": v} for k, v in raw.items()]


def _button(text: str, function: str, params: list[dict[str, str]], *, filled: bool = False) -> dict[str, Any]:
    btn: dict[str, Any] = {"text": text[:80], "onClick": {"action": {"function": function, "parameters": params}}}
    if filled:
        btn["color"] = {"red": 0.1, "green": 0.45, "blue": 0.91, "alpha": 1}
    return btn


def _font(text: str, color: str) -> str:
    return f'<font color="{color}">{text}</font>'


def _series_line(series: SeriesCandidate, rooms_by_id: dict[str, str]) -> str:
    occ = series.occurrences[0]
    room = ", ".join(rooms_by_id.get(r, "") for r in series.room_ids if rooms_by_id.get(r)) or "회의실 없음"
    return (
        f"<b>{html.escape(series.summary)}</b><br>"
        f"매주 {WEEKDAYS_KO[series.weekday]} {occ.start.strftime('%H:%M')}~{occ.end.strftime('%H:%M')}"
        f" · {html.escape(room)} · 참석 {len(series.people)}명"
        + (f" · 주최 {html.escape(series.organizer_email)}" if series.organizer_email else "")
    )


def _option_label(opt: CommonOption, duration: int) -> str:
    h, m = map(int, opt.key.start.split(":"))
    end_min = h * 60 + m + duration
    end = f"{end_min // 60:02d}:{end_min % 60:02d}"
    cap = f"({opt.capacity}인)" if opt.capacity else ""
    return (
        f"매주 {WEEKDAYS_KO[opt.key.weekday]} {opt.key.start}~{end} · {opt.room_name}{cap}"
        f" — {opt.available_weeks}/{opt.total_weeks}주 가능"
    )


def _week_line(w: WeekAssignment, duration: int, people_count: int) -> str:
    orig = w.original_start
    head = f"{orig.month}/{orig.day}({WEEKDAYS_KO[orig.weekday()]})"
    if w.key is None:
        return _font(f"{head} 배정 불가 — 비는 회의실이 없어요(회의실 없이 유지, 직접 확인 필요)", _RED)
    d = w.new_date()
    assert d is not None
    end = (_at(d, w.key.start) + timedelta(minutes=duration)).strftime("%H:%M")
    when = f"{w.key.start}~{end}"
    if d != orig.date():
        when = f"→ {d.month}/{d.day}({WEEKDAYS_KO[d.weekday()]}) {when}"
    text = f"{head} {when} {html.escape(w.room_name)}"
    if w.capacity and w.capacity < people_count:
        text += f"({w.capacity}인)"
    if w.is_exception:
        text = _font(f"{text} · 이 주만 다르게", _ORANGE)
    if w.conflicts:
        names = ", ".join(html.escape(n) for n in w.conflicts[:4])
        more = f" 외 {len(w.conflicts) - 4}명" if len(w.conflicts) > 4 else ""
        text += _font(f" · ⚠ 일정 겹침: {names}{more}", _GRAY)
    return text


def build_series_rebook_card(
    *,
    spec: SeriesRebookSpec,
    series: SeriesCandidate,
    plan: RebookPlan,
    rooms_by_id: dict[str, str],
    others: list[SeriesCandidate],
    can_edit: bool,
    include_action_response: bool = False,
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []

    understood = [_series_line(series, rooms_by_id), ""] + [html.escape(x) for x in spec.summary_lines()]
    widgets: list[dict[str, Any]] = [{"textParagraph": {"text": "<br>".join(understood)}}]
    if others:
        widgets.append(
            {
                "buttonList": {
                    "buttons": [
                        _button(f"다른 일정: {o.summary}", "sm_series_pick", _params(spec, o.series_id))
                        for o in others[:3]
                    ]
                }
            }
        )
    sections.append({"header": "이렇게 이해했어요", "widgets": widgets})

    if not plan.options:
        sections.append(
            {
                "header": "추천 시간",
                "widgets": [
                    {"textParagraph": {"text": _font("조건에 맞는 회의실·시간을 한 주도 찾지 못했어요. "
                                                     "최소 정원이나 지역 조건을 바꿔 다시 요청해 주세요.", _RED)}}
                ],
            }
        )
    else:
        chosen_key = plan.chosen.key if plan.chosen else None
        buttons = []
        for opt in plan.options:
            label = _option_label(opt, plan.duration)
            mark = "✓ " if opt.key == chosen_key else ""
            buttons.append(
                _button(mark + label, "sm_series_pick", _params(spec, series.series_id, opt.key.encode()),
                        filled=opt.key == chosen_key)
            )
        sections.append({"header": "고정 시간 후보 (눌러서 바꾸기)", "widgets": [{"buttonList": {"buttons": buttons}}]})

        lines = [_week_line(w, plan.duration, plan.people_count) for w in plan.weeks]
        summary_bits = [f"{len(plan.weeks)}주"]
        if plan.exceptions:
            summary_bits.append(f"이 주만 다르게 {len(plan.exceptions)}주")
        if plan.unassigned:
            summary_bits.append(f"배정 불가 {len(plan.unassigned)}주")
        week_widgets: list[dict[str, Any]] = [
            {"textParagraph": {"text": _font(" · ".join(summary_bits), _GRAY) + "<br>" + "<br>".join(lines)}}
        ]
        if plan.chosen and plan.chosen.capacity and plan.chosen.capacity < plan.people_count:
            week_widgets.append(
                {"textParagraph": {"text": _font(
                    f"기존 참석 {plan.people_count}명 / 회의실 {plan.chosen.capacity}인 — 요청하신 대로 작은 회의실도 포함했어요.",
                    _GRAY)}}
            )
        sections.append({"header": "주별 배정", "widgets": week_widgets})

    choice = plan.chosen.key.encode() if plan.chosen else ""
    action_widgets: list[dict[str, Any]] = []
    if not can_edit:
        action_widgets.append(
            {"textParagraph": {"text": _font(
                f"이 일정의 참석자이거나 주최자({html.escape(series.organizer_email or '다른 사람')})와 같은 팀원만 "
                "변경할 수 있어요. 위 결과를 주최자에게 전달해 주세요.", _RED)}}
        )
    elif plan.options:
        action_widgets.append(
            {"textParagraph": {"text": _font(
                "아직 아무것도 바뀌지 않았어요. 다음 화면에서 변경 내용을 한 번 더 확인한 뒤 실행돼요.", _GRAY)}}
        )
    buttons = []
    if can_edit and plan.options:
        buttons.append(_button("변경 내용 검토", "sm_series_review", _params(spec, series.series_id, choice), filled=True))
    buttons.append(_button("취소", "sm_series_cancel", []))
    action_widgets.append({"buttonList": {"buttons": buttons}})
    sections.append({"widgets": action_widgets})

    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": "sm_series_rebook",
                "card": {
                    "header": {
                        "title": "정기회의 옮기기",
                        "subtitle": series.summary[:60],
                        "imageUrl": _ICON_EVENT,
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


def build_series_review_card(
    *,
    spec: SeriesRebookSpec,
    series: SeriesCandidate,
    plan: RebookPlan,
    notice: str,
) -> dict[str, Any]:
    """최종 확인 — 이 화면의 '최종 확인'을 눌러야 캘린더가 바뀐다."""
    assert plan.chosen is not None
    opt = plan.chosen
    occ = series.occurrences[0]
    lines = [
        f"<b>{html.escape(series.summary)}</b>",
        f"변경 전: 매주 {WEEKDAYS_KO[series.weekday]} {occ.start.strftime('%H:%M')}~{occ.end.strftime('%H:%M')}",
        f"변경 후: {html.escape(_option_label(opt, plan.duration))}",
        f"대상: {len(plan.weeks)}회차 ({plan.weeks[0].original_start:%m/%d} ~ {plan.weeks[-1].original_start:%m/%d})",
    ]
    if plan.exceptions:
        lines.append(_font(f"이 주만 다르게: {len(plan.exceptions)}회차", _ORANGE))
        lines += [_week_line(w, plan.duration, plan.people_count) for w in plan.exceptions]
    if plan.unassigned:
        lines.append(_font(f"배정 불가(회의실 없이 유지): {len(plan.unassigned)}회차", _RED))
    cautions = [
        notice,
        "참석자 전원에게 일정 변경 메일이 가요.",
        "반복 규칙이 허용되면 기존 반복 일정을 끊고 새 반복 일정을 만들어 이후 회차 전체를 옮겨요.",
        "변경 전 원본을 백업해 두고, 결과 화면의 '되돌리기'로 복원할 수 있어요.",
    ]
    params = _params(spec, series.series_id, opt.key.encode())
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<br>".join(lines)}},
        {"textParagraph": {"text": _font("<br>".join("· " + html.escape(c) for c in cautions), _GRAY)}},
        {"buttonList": {"buttons": [
            _button("최종 확인", "sm_series_confirm", params, filled=True),
            _button("뒤로", "sm_series_pick", params),
            _button("취소", "sm_series_cancel", []),
        ]}},
    ]
    return {
        "actionResponse": {"type": "UPDATE_MESSAGE"},
        "cardsV2": [
            {
                "cardId": "sm_series_review",
                "card": {
                    "header": {"title": "최종 확인", "subtitle": "정기회의 옮기기 — 아직 바뀌지 않았어요"},
                    "sections": [{"widgets": widgets}],
                },
            }
        ],
    }


def restore_button(backup_id: str) -> dict[str, Any]:
    return _button("되돌리기", "sm_evt_restore_review", [{"key": "backup_id", "value": backup_id}])


def build_change_result_card(
    *,
    done: list[str],
    failed: list[str],
    notes: list[str],
    subtitle: str,
    ok_title: str,
    backup_id: str = "",
    html_link: str = "",
    changed: bool = True,
    include_action_response: bool = True,
) -> dict[str, Any]:
    """변경·삭제·되돌리기 결과 — 무언가 바뀌었고 백업이 있으면 '되돌리기'를 단다."""
    title = ok_title if not failed else "일부만 처리했어요" if done else "처리하지 못했어요"
    lines: list[str] = []
    if done:
        lines.append("<b>완료</b><br>" + "<br>".join(html.escape(x) for x in done))
    if failed:
        lines.append(_font("<b>실패</b><br>" + "<br>".join(html.escape(x) for x in failed), _RED))
    if notes:
        lines.append(_font("<br>".join(html.escape(x) for x in notes if x), _GRAY))
    buttons: list[dict[str, Any]] = [{"text": "홈으로", "onClick": {"action": {"function": "hm_open_menu"}}}]
    if html_link:
        buttons.insert(0, {"text": "캘린더에서 보기", "onClick": {"openLink": {"url": html_link}}})
    if backup_id and changed:
        buttons.insert(0, restore_button(backup_id))
        lines.append(_font(f"원본 백업 ID: {html.escape(backup_id)}", _GRAY))
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": "<br><br>".join(lines) or "-"}},
        {"buttonList": {"buttons": buttons}},
    ]
    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": "sm_change_result",
                "card": {"header": {"title": title, "subtitle": subtitle}, "sections": [{"widgets": widgets}]},
            }
        ]
    }
    if include_action_response:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def build_series_result_card(
    result: ApplyResult,
    *,
    backup_id: str = "",
    include_action_response: bool = True,
) -> dict[str, Any]:
    return build_change_result_card(
        done=result.done,
        failed=result.failed,
        notes=result.notes,
        subtitle="정기회의 옮기기",
        ok_title="정기회의를 옮겼어요",
        backup_id=backup_id,
        html_link=result.html_link,
        changed=bool(result.done or result.created_event_ids),
        include_action_response=include_action_response,
    )
