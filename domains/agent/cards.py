"""에이전트 카드 빌더 — Google Chat cardsV2.

승인 게이트 카드의 버튼은 main.py CARD_CLICKED 가 파싱하는 형식
(action.function + action.parameters[{key,value}])을 따른다.
"""

from __future__ import annotations

import html
from typing import Any


def _action_button(text: str, function: str, *, parameters: dict[str, str] | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"function": function}
    if parameters:
        action["parameters"] = [{"key": k, "value": v} for k, v in parameters.items()]
    return {"text": text, "onClick": {"action": action}}


def _wrap_card(card_id: str, header: dict[str, Any], widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cardsV2": [
            {"cardId": card_id, "card": {"header": header, "sections": [{"widgets": widgets}]}}
        ]
    }


def _step_badge(step: dict[str, Any]) -> str:
    """단계의 성격을 보여주는 뱃지 — 에이전트의 도구 선택 의도를 드러낸다."""
    if step.get("side_effect"):
        return "[쓰기·승인]"
    if str(step.get("tool") or "") == "generate_content":
        return "[생성]"
    return "[조회]"


def _steps_text(steps: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for s in steps:
        n = s.get("n")
        why = html.escape(str(s.get("why") or s.get("tool") or ""))
        badge = _step_badge(s)
        tool = html.escape(str(s.get("tool") or ""))
        tool_tag = f" · <font color=\"#9aa0a6\">{tool}</font>" if tool else ""
        lines.append(f"<b>{n}.</b> {badge} {why}{tool_tag}")
    return "<br>".join(lines) if lines else "(단계 없음)"


def build_plan_approval_card(
    *,
    plan_id: str,
    goal: str,
    steps: list[dict[str, Any]],
    rationale: str = "",
    critique_note: str = "",
) -> dict[str, Any]:
    """Phase B — 계획 검토 카드. 승인해야만 실행된다."""
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": f"<b>목표</b><br>{html.escape(goal or '(미정)')}"}},
    ]
    if (rationale or "").strip():
        widgets.append(
            {"textParagraph": {"text": f"<b>설계 노트</b><br>{html.escape(rationale)}"}}
        )
    if (critique_note or "").strip():
        widgets.append(
            {"textParagraph": {"text": f"<b>검토</b><br>{html.escape(critique_note)}"}}
        )
    widgets += [
        {"textParagraph": {"text": f"<b>실행 계획</b><br>{_steps_text(steps)}"}},
        {
            "buttonList": {
                "buttons": [
                    _action_button("✅ 승인하고 실행", "ag_approve", parameters={"plan_id": plan_id}),
                    _action_button("✏️ 다시 계획", "ag_edit", parameters={"plan_id": plan_id}),
                    _action_button("❌ 취소", "ag_reject", parameters={"plan_id": plan_id}),
                ]
            }
        },
    ]
    out = _wrap_card("ag_plan_approval", {"title": "AllMeet", "subtitle": "계획 검토"}, widgets)
    out["text"] = "AI가 계획을 세웠어요. 검토 후 승인해 주세요. (승인 전에는 실행되지 않습니다.)"
    return out


def build_reapproval_card(*, plan_id: str, message: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """자가복구 중 새 쓰기 작업이 필요해 다시 승인을 받는 카드."""
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": html.escape(message)}},
        {"textParagraph": {"text": f"<b>변경된 계획</b><br>{_steps_text(steps)}"}},
        {
            "buttonList": {
                "buttons": [
                    _action_button("✅ 승인하고 계속", "ag_approve", parameters={"plan_id": plan_id}),
                    _action_button("❌ 중단", "ag_reject", parameters={"plan_id": plan_id}),
                ]
            }
        },
    ]
    out = _wrap_card("ag_reapproval", {"title": "AllMeet", "subtitle": "재승인 필요"}, widgets)
    out["text"] = "진행 중 계획을 변경해야 해요. 승인해 주세요."
    return out


def build_agent_cta_card(
    *,
    user_message: str,
    intent_value: str,
    outline: list[str],
    label: str = "🤖 AI에게 맡기기",
) -> dict[str, Any]:
    """하이브리드 랜딩의 'AI 위임' 카드 — 정적 프로세스 미리보기 + ag_delegate 버튼.

    단일 카드(dict)를 반환하며, 호출 측이 base 응답의 cardsV2 리스트에 합친다.
    """
    steps = "<br>".join(f"{i}. {html.escape(s)}" for i, s in enumerate(outline, start=1)) or "AI가 알아서 처리해요."
    widgets: list[dict[str, Any]] = [
        {"textParagraph": {"text": f"<b>AI에게 맡기면 이렇게 진행해요</b><br>{steps}"}},
        {
            "buttonList": {
                "buttons": [
                    _action_button(
                        label,
                        "ag_delegate",
                        parameters={"user_message": user_message, "intent": intent_value},
                    )
                ]
            }
        },
    ]
    return {
        "cardId": "ag_cta",
        "card": {
            "header": {"title": "AllMeet", "subtitle": "AI에게 맡기기"},
            "sections": [{"widgets": widgets}],
        },
    }


def build_ask_user_card(message: str) -> dict[str, Any]:
    """계획을 못 세웠거나 정보가 부족할 때 되묻는 메시지."""
    return {"text": f"🤔 {message}"}


def build_progress_text(message: str) -> dict[str, Any]:
    return {"text": message}
