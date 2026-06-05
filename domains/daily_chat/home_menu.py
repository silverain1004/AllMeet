"""홈 메뉴 카드 — 능력 안내·랜덤 질문·도메인 진입·설정 허브."""

from __future__ import annotations

import html
import random
import re
from typing import Any

from domains.daily_chat.chat import reply_daily_chat
from firestore.oauth_tokens import get_token

DAILY_PROMPTS: tuple[str, ...] = (
    "점심 메뉴 추천해줘",
    "퇴근 후 가볍게 할 수 있는 스트레칭 루틴 알려줘",
    "오늘 기분 전환에 좋은 음악 장르 추천해줘",
    "회의 전에 집중력 올리는 방법 알려줘",
    "간단한 커피 레시피 추천해줘",
    "주말에 가기 좋은 서울 근교 여행지 추천해줘",
    "재미있는 두뇌 퀴즈 하나 내줘",
    "스트레스 해소에 도움 되는 짧은 명상 방법 알려줘",
)

INFO_PROMPTS: tuple[str, ...] = (
    "오늘 서울 날씨 알려줘",
    "삼성전자 주가 알려줘",
    "오늘 주요 IT 뉴스 요약해줘",
    "원·달러 환율 알려줘",
    "코스피 지수 오늘 현황 알려줘",
    "비트코인 시세 알려줘",
    "오늘 미국 증시 마감 요약해줘",
    "내일 서울 강수 확률 알려줘",
)

_LABEL_SUFFIX_RE = re.compile(
    r"(알려줘|알려 주세요|해줘|해 주세요|추천해줘|추천해 주세요|내줘|내 주세요)$"
)


def _wrap_card(
    card_id: str,
    header: dict[str, Any],
    widgets: list[dict[str, Any]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cardsV2": [
            {
                "cardId": card_id,
                "card": {
                    "header": header,
                    "sections": [{"widgets": widgets}],
                },
            }
        ],
    }
    if include_action_response:
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def _short_label(prompt: str, *, max_len: int = 18) -> str:
    label = _LABEL_SUFFIX_RE.sub("", (prompt or "").strip()).strip()
    if len(label) > max_len:
        label = label[: max_len - 1] + "…"
    return label or "질문"


def _action_button(
    text: str,
    function: str,
    *,
    parameters: dict[str, str] | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {"function": function}
    if parameters:
        action["parameters"] = [{"key": k, "value": v} for k, v in parameters.items()]
    return {"text": text, "onClick": {"action": action}}


def _user_email(chat_event: dict[str, Any] | None) -> str:
    return str(((chat_event or {}).get("user") or {}).get("email") or "").strip()


def is_oauth_linked(user_email: str) -> bool:
    """주간보고 초안 등 개인 데이터 연동 여부."""
    if not user_email:
        return False
    doc = get_token(user_email)
    if not doc:
        return False
    return str(doc.get("status") or "") == "linked" and bool(doc.get("refresh_token"))


def _message_chat_event(chat_event: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(chat_event or {})
    base["type"] = "MESSAGE"
    return base


def build_home_menu_card(
    *,
    chat_event: dict[str, Any] | None = None,
    daily_prompt: str | None = None,
    info_prompt: str | None = None,
    include_action_response: bool = False,
) -> dict[str, Any]:
    """6버튼 홈 메뉴. 1·2번은 표시 시점의 랜덤 예시(버튼 라벨·prompt 동일)."""
    _ = chat_event  # 향후 사용자별 맞춤 프롬프트용
    daily = daily_prompt or random.choice(DAILY_PROMPTS)
    info = info_prompt or random.choice(INFO_PROMPTS)

    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": (
                    "<b>제가 잘하는 업무를 골라 보세요</b><br>"
                    "일상 질문·최신 정보·사내 검색·회의실·주간보고까지 버튼으로 바로 시작할 수 있어요."
                )
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button(
                        f"🎲 {_short_label(daily)}",
                        "hm_run_daily",
                        parameters={"prompt": daily},
                    ),
                    _action_button(
                        f"🎲 {_short_label(info)}",
                        "hm_run_info",
                        parameters={"prompt": info},
                    ),
                    _action_button("🔎 사내 전문가 찾기", "hm_expert"),
                    _action_button("📅 최적의 회의실 검색", "hm_schedule"),
                    _action_button("📄 주간보고 초안 받기", "hm_weekly_draft"),
                    _action_button("⚙️ 설정", "hm_open_settings"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "hm_home_menu",
        {"title": "AllMeet", "subtitle": "홈 메뉴"},
        widgets,
        include_action_response=include_action_response,
    )
    out["text"] = "All-Meet 홈 메뉴입니다. 원하는 기능을 선택해 주세요."
    return out


def build_settings_hub_card(*, include_action_response: bool = False) -> dict[str, Any]:
    """팀·컨플루언스·캘린더·OAuth 등 설정 진입 허브."""
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": "<b>설정</b><br>팀·데이터 연결·캘린더·회의실 관련 설정을 선택해 주세요."
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button("🔗 내 데이터 연결", "wm_oauth_link"),
                    _action_button("팀 설정", "wm_open_team_menu"),
                    _action_button("팀원 설정", "wm_open_member_menu"),
                    _action_button("컨플루언스 설정", "wm_open_conf_menu"),
                    _action_button("주간회의 일정", "wm_open_schedule_menu"),
                    _action_button("스케줄러", "wm_open_scheduler"),
                    _action_button("캘린더·예약 설정", "sm_open_settings"),
                    _action_button("회의실 동기화", "sm_sync_rooms"),
                    _action_button("홈으로", "hm_open_menu"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "hm_settings_hub",
        {"title": "AllMeet", "subtitle": "설정"},
        widgets,
        include_action_response=include_action_response,
    )
    out["text"] = "설정 메뉴입니다."
    return out


def build_oauth_required_card(
    feature_label: str,
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    """OAuth 미연동 시 기능 차단 안내."""
    safe = html.escape(feature_label)
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": (
                    f"<b>{safe}</b> 기능을 쓰려면 <b>내 데이터 연결</b>이 필요해요.<br>"
                    "Gmail · 개인 Calendar · 내 Drive 동의 후 다시 시도해 주세요."
                )
            }
        },
        {
            "buttonList": {
                "buttons": [
                    _action_button("⚙️ 설정에서 연결", "hm_open_settings"),
                    _action_button("홈으로", "hm_open_menu"),
                ]
            }
        },
    ]
    out = _wrap_card(
        "hm_oauth_required",
        {"title": "AllMeet", "subtitle": "연동 필요"},
        widgets,
        include_action_response=include_action_response,
    )
    out["text"] = f"{feature_label} — 내 데이터 연결이 필요합니다."
    return out


def _run_prompt(prompt: str, chat_event: dict[str, Any] | None) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"text": "실행할 질문이 없습니다. 홈 메뉴에서 다시 선택해 주세요."}
    answer = reply_daily_chat(text, chat_event=_message_chat_event(chat_event))
    if isinstance(answer, dict):
        return answer
    return {"text": answer}


def handle_home_menu_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    """CARD_CLICKED hm_* 액션 처리."""
    fn = (invoked_function or "").strip()
    email = _user_email(chat_event)

    if fn == "hm_open_menu":
        return build_home_menu_card(chat_event=chat_event, include_action_response=True)

    if fn == "hm_open_settings":
        return build_settings_hub_card(include_action_response=True)

    if fn in {"hm_run_daily", "hm_run_info"}:
        return _run_prompt(parameters.get("prompt", ""), chat_event)

    if fn == "hm_expert":
        from domains.expert_finder import handle_expert_finder

        reply = handle_expert_finder("전문가 찾기", chat_event=chat_event)
        if isinstance(reply, dict) and not reply.get("text"):
            reply = dict(reply)
            reply.setdefault("text", "전문가 찾기를 시작했어요.")
        return reply

    if fn == "hm_schedule":
        from domains.schedule_management import handle_schedule_management

        reply = handle_schedule_management("회의실 예약", chat_event=chat_event)
        if isinstance(reply, dict):
            reply = dict(reply)
            reply.setdefault("text", "회의 예약 화면입니다. 항목을 확인한 뒤 확정해 주세요.")
            reply.setdefault("actionResponse", {"type": "UPDATE_MESSAGE"})
        return reply

    if fn == "hm_weekly_draft":
        if not email or not is_oauth_linked(email):
            return build_oauth_required_card("주간보고 초안")
        from domains.weekly_report import handle_weekly_report_draft

        return handle_weekly_report_draft("주간보고초안", chat_event=chat_event)

    return {"text": "알 수 없는 홈 메뉴 동작입니다."}
