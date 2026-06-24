"""하이브리드 액션 랜딩 — 도메인 카드 + 'AI에게 맡기기' CTA 를 한 메시지로 합친다.

액션 intent 결과(text + cardsV2)에 정적 프로세스 미리보기를 담은 CTA 카드를 append 해,
즉시 처리파와 AI 위임파를 동시에 만족시킨다. planner 호출 없이 정적이라 지연이 없다.
"""

from __future__ import annotations

from typing import Any

from domains.agent.cards import build_agent_cta_card

# intent 값 → (정적 미리보기 outline, CTA 버튼 라벨, 안내 문구)
ACTIONABLE_INTENTS: dict[str, dict[str, Any]] = {
    "schedule_management": {
        "outline": ["빈 시간 확인", "회의 생성", "참석자 초대/알림"],
        "label": "🤖 AI가 빈 시간 찾아 예약까지 맡기기",
        "hint": "직접 입력해서 예약하거나, AI가 빈 시간 확인부터 알아서 진행하도록 맡기실 수 있어요.",
    },
    "expert_finder": {
        "outline": ["사내 데이터 검색", "후보 스코어링", "추천 정리"],
        "label": "🤖 AI가 후속까지 알아서",
        "hint": "결과를 직접 보시거나, AI가 관련 후속 작업까지 이어서 진행하도록 맡기실 수 있어요.",
    },
    "weekly_report_draft": {
        "outline": ["데이터 수집", "초안 작성", "검토 요청"],
        "label": "🤖 AI에게 맡기기",
        "hint": "초안을 직접 받으시거나, AI가 수집·작성·검토까지 이어서 진행하도록 맡기실 수 있어요.",
    },
}


def is_actionable_intent(intent_value: str) -> bool:
    return (intent_value or "") in ACTIONABLE_INTENTS


def wrap_with_agent_cta(
    base_reply: Any,
    *,
    user_message: str,
    intent_value: str,
) -> Any:
    """도메인 핸들러 결과에 AI 위임 CTA 카드를 덧붙인다.

    - base_reply 가 dict 가 아니거나(예: 순수 문자열) 액션 intent 가 아니면 그대로 통과.
    - dict 면 cardsV2 리스트에 CTA 카드를 append 하고, text 안내를 보강한다.
    """
    meta = ACTIONABLE_INTENTS.get(intent_value or "")
    if meta is None or not isinstance(base_reply, dict):
        return base_reply

    out = dict(base_reply)
    cta = build_agent_cta_card(
        user_message=user_message,
        intent_value=intent_value,
        outline=meta["outline"],
        label=meta["label"],
    )
    cards = out.get("cardsV2")
    if isinstance(cards, list):
        out["cardsV2"] = list(cards) + [cta]
    else:
        out["cardsV2"] = [cta]

    hint = meta.get("hint") or ""
    existing = str(out.get("text") or "").strip()
    if hint:
        out["text"] = f"{existing}\n\n{hint}".strip() if existing else hint
    return out


def wrap_daily_chat_with_recovery(base_reply: Any, *, user_message: str) -> Any:
    """daily_chat 으로 떨어졌으나 사내 업무성 발화면 'AI에게 맡기기' 회수 CTA 를 덧붙인다.

    라우팅 false-negative 의 안전망 — daily_chat 이 "못 해요"로 막다른 길을 내는 대신
    1탭으로 agent 위임(ag_delegate)할 수 있게 한다. action-like 가 아니면 원본 그대로 통과.
    base_reply 가 문자열(일반 daily_chat 응답)이면 {text, cardsV2} dict 로 승격한다.
    """
    from domains.routing.patterns import looks_actionable_for_recovery

    if not looks_actionable_for_recovery(user_message):
        return base_reply

    cta = build_agent_cta_card(
        user_message=user_message,
        intent_value="agent",
        outline=["요청 파악", "필요한 사내 데이터 조회", "결과 정리·실행"],
        label="🤖 AI에게 맡기기",
    )
    hint = "원하시면 AI가 사내 데이터를 확인해 이어서 처리할 수 있어요."
    if isinstance(base_reply, dict):
        out = dict(base_reply)
        cards = out.get("cardsV2")
        out["cardsV2"] = (list(cards) + [cta]) if isinstance(cards, list) else [cta]
        existing = str(out.get("text") or "").strip()
        out["text"] = f"{existing}\n\n{hint}".strip() if existing else hint
        return out
    text = str(base_reply or "").strip()
    return {"text": f"{text}\n\n{hint}".strip() if text else hint, "cardsV2": [cta]}
