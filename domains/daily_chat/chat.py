"""
일상 대화:
- 능력·기능 질문 → 고정 WHAT_I_CAN_DO_TEXT
- 최신 정보 필요(주가·뉴스 등) → Google Search + Gemini (google-genai)
- 그 외 → Vertex Gemini
구글 챗 MESSAGE + 유효 space 이면 Firestore 연동.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config.settings import LOCATION, PROJECT_ID

from firestore.conversation import ensure_conversation, list_recent_messages, record_messages

logger = logging.getLogger(__name__)

_model = None

WHAT_I_CAN_DO_TEXT = (
    "제가 잘하는 업무는 이런 게 있어요.\n\n"
    "• 💬 일상 대화 · 질문 답변\n"
    "• 🌐 최신 정보 (주가, 뉴스, 날씨 등 웹 검색)\n"
    "• 🔍 사내 전문가 찾기 (예: 전문가, 추천해줘)\n"
    "• 🗓️ 캘린더·일정 관리 (예: 일정, 미팅 예약)\n"
    "• 👥 주간 회의·팀/인원 등록 (예: 주간 회의, 팀 등록)\n\n"
    "그 외에도 필요하시면 편하게 말씀해 주세요."
)

# 1. 맥락을 보고 All-Meet 능력·기능 안내가 적절한지 LLM 으로 분류한다.
def _user_asks_capabilities(user_message: str, ctx_block: str) -> bool:
    """봇이 뭘 할 수 있냐는 질문이면 True. 실패 시 False(일반 대화로 처리)."""
    msg = (user_message or "").strip()
    if not msg:
        return False
    ctx = (ctx_block or "").strip()
    try:
        from vertexai.generative_models import GenerationConfig

        body = (
            "역할: 분류기. 출력은 yes 또는 no 한 단어(영문 소문자)만 쓰세요.\n\n"
            "사용자가 All-Meet(봇)이 무엇을 할 수 있는지·어떤 기능이 있는지·업무로 무엇을 도와줄 수 있는지 묻는 의도이면 yes.\n"
            "그 외(일상 대화, 이야기, 다른 주제, 봇 기능과 무관한 말)면 no.\n\n"
        )
        body += f"{ctx}\n\n" if ctx else "(이전 대화 없음)\n\n"
        body += f'사용자 마지막 발화: "{msg}"\n\n분류:'
        res = _get_generative_model().generate_content(
            body,
            generation_config=GenerationConfig(temperature=0, max_output_tokens=8),
        )
        raw = (res.text or "").strip().lower()
        if not raw:
            return False
        first = raw.split()[0].rstrip(".,!?")
        return first == "yes"
    except Exception as e:
        logger.warning("_user_asks_capabilities 실패, 일반 대화로 처리: %s", e)
        return False


def _needs_web_search(user_message: str, ctx_block: str) -> bool:
    """주가·뉴스·실시간 정보 등 웹 검색이 필요하면 True (참고 KNOWLEDGE_SEARCH 판단)."""
    msg = (user_message or "").strip()
    if not msg:
        return False
    ctx = (ctx_block or "").strip()
    try:
        from vertexai.generative_models import GenerationConfig

        body = (
            "역할: 분류기. 출력은 yes 또는 no 한 단어(영문 소문자)만 쓰세요.\n\n"
            "사용자 질문에 답하려면 **최신 웹 검색**(실시간 주가·시세, 환율, 최신 뉴스, 오늘 날씨, 최근 지표 등 지금 시점의 정보)이 필요하면 yes.\n"
            "일상 잡담, 감정 대화, 또는 모델 일반 지식만으로 충분하면 no.\n\n"
        )
        body += f"{ctx}\n\n" if ctx else "(이전 대화 없음)\n\n"
        body += f'사용자 마지막 발화: "{msg}"\n\n분류:'
        res = _get_generative_model().generate_content(
            body,
            generation_config=GenerationConfig(temperature=0, max_output_tokens=8),
        )
        raw = (res.text or "").strip().lower()
        if not raw:
            return False
        first = raw.split()[0].rstrip(".,!?")
        return first == "yes"
    except Exception as e:
        logger.warning("_needs_web_search 실패: %s", e)
        return False


def _answer_with_google_search(user_message: str) -> str:
    """Google Search 도구로 실시간 정보 검색 후 답변 (참고 bot/ai_handler.answer_with_web_search)."""
    try:
        from google import genai
        from google.genai.types import GenerateContentConfig, GoogleSearch, Tool

        os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
        os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

        model_name = os.environ.get("ALLMEET_CHAT_MODEL", "gemini-2.0-flash-001")
        client = genai.Client()
        response = client.models.generate_content(
            model=model_name,
            contents=(
                "당신은 All-Meet 업무 에이전트입니다. 아래 질문에 대해 검색 결과를 바탕으로 "
                "한국어로 간결하고 정확하게 답하세요. 수치·시점은 검색에 나온 내용을 우선하세요.\n\n"
                f"질문: {user_message}"
            ),
            config=GenerateContentConfig(
                tools=[Tool(google_search=GoogleSearch())],
                temperature=0.3,
            ),
        )
        text = (response.text or "").strip()
        if text:
            return text
        return "검색 결과를 정리할 수 없었습니다. 질문을 조금 다르게 해 보시겠어요?"
    except Exception as e:
        logger.warning("Google Search 답변 실패: %s", e)
        return (
            "실시간 검색 중 오류가 발생했습니다. 잠시 후 다시 시도하거나 질문을 바꿔 주세요."
        )


# 2. 봇 입장·빈 메시지 시 인사 + 잘하는 업무 안내 (main.py ADDED_TO_SPACE / 빈 MESSAGE).
def welcome_with_capabilities_text() -> str:
    """봇 인사와 잘하는 업무 소개 문구."""
    return (
        "All-Meet 입니다.\n\n"
        "메시지를 보내 주시면 맞춰 도와드릴게요.\n\n"
        + WHAT_I_CAN_DO_TEXT
    )


# 3. 구글 챗 POST JSON에서 방 ID·표시 이름·user_context 를 뽑는다 (Firestore 키·문서 필드용).
def _parse_google_chat_payload(payload: dict[str, Any]) -> tuple[str, str | None, dict[str, str]]:
    """구글 챗 이벤트 JSON에서 space_id, 표시명, user_context 튜플로 추출."""
    space_name = (payload.get("space") or {}).get("name") or ""
    space_id = space_name.replace("spaces/", "").strip() if space_name else ""
    space_id = space_id or "unknown"

    user = payload.get("user") or {}
    display = (user.get("displayName") or "").strip() if isinstance(user.get("displayName"), str) else ""
    name = user.get("displayName") or user.get("name") or "알 수 없음"
    ctx = {
        "name": name,
        "department": (user.get("department") or "").strip() or "미지정",
        "email": (user.get("email") or "").strip() or "",
    }
    return space_id, (display or None), ctx


# 4. Vertex Gemini 를 첫 호출 시 한 번만 초기화해 재사용한다.
def _get_generative_model():
    """Vertex GenerativeModel 싱글톤(최초 호출 시 초기화)."""
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


def _format_recent_dialogue(messages: list[dict[str, str]]) -> str:
    """프롬프트에 넣을 최근 대화 블록(한 줄 한 턴)."""
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages:
        role = "사용자" if (m.get("role") == "user") else "All-Meet"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


# 5. 이번 턴을 Firestore messages 에 동기 기록(다음 요청에서 list_recent_messages 로 조회).
def _persist_turn(space_id: str, user_name: str | None, user_message: str, assistant_message: str) -> None:
    try:
        record_messages(space_id, user_name, user_message, assistant_message)
    except Exception:
        logger.exception("Firestore 메시지 기록 실패")


# 6. 한 턴: 맥락 로드 → 답 생성 → (조건 맞으면) 저장.
def reply_daily_chat(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
) -> str:
    """일상 대화 한 턴 응답. chat_event는 main POST JSON; MESSAGE+space면 Firestore 연동."""
    msg = (user_message or "").strip()
    if not msg:
        return "메시지를 입력해 주시면 대화할 수 있어요."

    conversation_context: dict[str, Any] | None = None
    space_id: str | None = None
    user_display_name: str | None = None

    if chat_event and chat_event.get("type") == "MESSAGE":
        space_id, user_display_name, user_ctx = _parse_google_chat_payload(chat_event)
        if space_id != "unknown":
            conversation_context = ensure_conversation(space_id, user_display_name, user_ctx)

    ctx_block = ""
    if conversation_context is not None and space_id and space_id != "unknown":
        try:
            recent_msgs = list_recent_messages(space_id, user_display_name)
        except Exception:
            logger.warning("list_recent_messages 실패", exc_info=True)
            recent_msgs = []
        block = _format_recent_dialogue(recent_msgs)
        if block:
            ctx_block = f"\n[최근 대화]\n{block}\n"

    if _user_asks_capabilities(msg, ctx_block):
        out = WHAT_I_CAN_DO_TEXT
    elif _needs_web_search(msg, ctx_block):
        out = _answer_with_google_search(msg)
    else:
        if ctx_block.strip():
            recent_hint = (
                "[최근 대화]가 있으면 그 전체 흐름을 먼저 읽고, 이번 사용자 메시지가 그 맥락 안에서 무엇을 요구하는지 추론하세요. "
                "짧거나 끊긴 말도 앞 대화와 묶어서 이해하고, 이어지던 주제·이야기면 흐름을 유지하세요.\n\n"
            )
        else:
            recent_hint = ""

        prompt = f"""당신은 기업용 업무 에이전트 'All-Meet'입니다.
                아래 사용자 메시지에 대해 친근하고 자연스러운 한국어로 답하세요.
                업무 도구를 쓰라고 강요하지 말고, 일상 대화·질문에는 직접 답변하세요.
                응답은 2~6문장 정도로 적당히 짧게 유지하고, 의미 단위마다 빈 줄로 단락을 나누어 읽기 쉽게 쓰세요.
                {recent_hint}{ctx_block}
                사용자 메시지: "{msg}"
              """
        try:
            res = _get_generative_model().generate_content(prompt)
            out = (res.text or "").strip()
            if not out:
                out = "지금은 답변을 만들지 못했어요. 조금 다시 말씀해 주시겠어요?"
        except Exception as e:
            logger.warning("daily_chat Gemini 호출 실패, 폴백 사용: %s", e)
            out = (
                "[데모 모드] 일상 대화로 받았어요.\n\n"
                "Vertex AI 자격 증명(GOOGLE_APPLICATION_CREDENTIALS 또는 Cloud Run 기본 서비스 계정)과 "
                "API 활성화를 확인하면 AI 답변이 붙습니다.\n\n"
                f"원문: {msg}"
            )

    if (
        chat_event
        and chat_event.get("type") == "MESSAGE"
        and space_id
        and space_id != "unknown"
    ):
        _persist_turn(space_id, user_display_name, msg, out)

    return out
