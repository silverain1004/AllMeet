"""
일상 대화 응답 생성.

- ‘너 뭐 할 수 있어’ 류 질문 → 고정 문구 (Gemini 호출 없음)
- 그 외 → Vertex Gemini (참고 bot/ai_handler CHAT)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config.settings import LOCATION, PROJECT_ID

logger = logging.getLogger(__name__)

_model = None

# ---------------------------------------------------------------------------
# 잘하는 업무 (능력 소개 질문용) — 문구는 여기만 수정
# ---------------------------------------------------------------------------

WHAT_I_CAN_DO_TEXT = (
    "제가 잘하는 업무는 이런 게 있어요.\n\n"
    "• 💬 일상 대화 · 질문 답변\n"
    "• 🔍 사내 전문가 찾기 (예: 전문가, 추천해줘)\n"
    "• 🗓️ 캘린더·일정 관리 (예: 일정, 미팅 예약)\n"
    "• 👥 주간 회의·팀/인원 등록 (예: 주간 회의, 팀 등록)\n\n"
    "그 외에도 필요하시면 편하게 말씀해 주세요."
)


def is_capability_question(user_message: str) -> bool:
    """뭐 할 수 있어 / 할 줄 아는 게 뭐야 / 뭐하는 봇 등 능력·기능 문의."""
    t = (user_message or "").strip().lower()
    if not t:
        return False
    keys = (
        "뭐할 수",
        "뭐 할 수",
        "뭐해줄",
        "뭐 해줄",
        "할 줄 아는",
        "할줄아는",
        "할 줄 아는게",
        "뭐하는",
        "뭐 하는",
        "뭐하는 봇",
        "기능 뭐",
        "뭐하는지",
        "도움",
        "help",
        "what can",
    )
    return any(k in t for k in keys)


def welcome_with_capabilities_text() -> str:
    """봇 첫 추가(ADDED_TO_SPACE)·빈 메시지 시 인사 + 잘하는 업무 (main.py에서 사용)."""
    return (
        "All-Meet 입니다.\n\n"
        "메시지를 보내 주시면 맞춰 도와드릴게요.\n\n"
        + WHAT_I_CAN_DO_TEXT
    )


def _get_generative_model():
    """Vertex GenerativeModel 지연 로딩 (Cold start 시에도 HTTP 리스닝 우선)."""
    global _model
    if _model is not None:
        return _model
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    gen_cfg = GenerationConfig(
        temperature=0.4,
        max_output_tokens=1024,
    )
    _model = GenerativeModel(
        os.environ.get("ALLMEET_CHAT_MODEL", "gemini-2.0-flash-001"),
        generation_config=gen_cfg,
    )
    return _model


def reply_daily_chat(user_message: str, conversation_context: dict[str, Any] | None = None) -> str:
    """
    일상 대화 한 턴 응답.

    conversation_context: 나중에 요약·최근 메시지 등을 넣을 수 있게 확장용 훅.
    """
    msg = (user_message or "").strip()
    if not msg:
        return "메시지를 입력해 주시면 대화할 수 있어요."

    if is_capability_question(msg):
        return WHAT_I_CAN_DO_TEXT

    ctx_block = ""
    if conversation_context:
        summary = (conversation_context.get("summary") or "").strip()
        if summary:
            ctx_block = f"\n[이전 대화 요약]\n{summary}\n"

    prompt = f"""당신은 기업용 업무 에이전트 'All-Meet'입니다.
                아래 사용자 메시지에 대해 친근하고 자연스러운 한국어로 답하세요.
                업무 도구를 쓰라고 강요하지 말고, 일상 대화·질문에는 직접 답변하세요.
                응답은 2~6문장 정도로 적당히 짧게 유지하고, 의미 단위마다 빈 줄로 단락을 나누어 읽기 쉽게 쓰세요.{ctx_block}
                사용자 메시지: "{msg}"
              """
    try:
        res = _get_generative_model().generate_content(prompt)
        out = (res.text or "").strip()
        if out:
            return out
        return "지금은 답변을 만들지 못했어요. 조금 다시 말씀해 주시겠어요?"
    except Exception as e:
        logger.warning("daily_chat Gemini 호출 실패, 폴백 사용: %s", e)
        return (
            "[데모 모드] 일상 대화로 받았어요.\n\n"
            "Vertex AI 자격 증명(GOOGLE_APPLICATION_CREDENTIALS 또는 Cloud Run 기본 서비스 계정)과 "
            "API 활성화를 확인하면 AI 답변이 붙습니다.\n\n"
            f"원문: {msg}"
        )
