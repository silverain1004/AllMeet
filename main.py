"""Application entrypoint for Cloud Run (Functions Framework HTTP).

기존 services_all-meet-agent와 동일하게 `hello_http`를 타깃으로 두고,
`gcloud run deploy ... --source .` 로 빌드·배포할 수 있습니다.

진입점에서는 HTTP 파싱만 하고, 질문 유형에 따라 domains 쪽으로 분기합니다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from enum import Enum
from typing import Any

import functions_framework

from domains.daily_chat import (
    build_home_menu_card,
    build_settings_hub_card,
    handle_home_menu_action,
    handle_settings_action,
    reply_daily_chat,
)
from domains.expert_finder import handle_expert_finder
from domains.schedule_management import (
    handle_schedule_management,
    handle_schedule_management_action,
)
from domains.weekly_meeting import handle_weekly_meeting, handle_weekly_meeting_action
from domains.weekly_report import handle_weekly_report_draft

import json as _json


class _JsonFormatter(logging.Formatter):
    """Cloud Logging 구조화 로그 포맷 (severity 필드 포함)."""

    _LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return _json.dumps(
            {"severity": self._LEVEL_MAP.get(record.levelno, "DEFAULT"), "message": msg},
            ensure_ascii=False,
        )


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger(__name__)

# Vertex AI 모델을 앱 시작 시 미리 초기화 (첫 요청 지연 방지)
try:
    from domains.daily_chat.chat import _get_generative_model
    _get_generative_model()
    logger.info("Vertex AI 모델 사전 초기화 완료")
except Exception as _e:
    logger.warning("Vertex AI 모델 사전 초기화 실패 (첫 요청 시 재시도): %s", _e)


# ---------------------------------------------------------------------------
# 의도 분류 (진입점 전용) — 키워드·패턴 기준. 나중에 LLM 분류로 바꿀 수 있음.
# ---------------------------------------------------------------------------


class UserIntent(str, Enum):
    """아래 match/case 와 1:1로 매핑되는 의도."""

    DAILY_CHAT = "daily_chat"
    EXPERT_FINDER = "expert_finder"
    HOME_MENU = "home_menu"
    SCHEDULE_MANAGEMENT = "schedule_management"
    SETTINGS = "settings"
    WEEKLY_MEETING = "weekly_meeting"
    WEEKLY_REPORT_DRAFT = "weekly_report_draft"


def match_user_intent(user_message: str) -> UserIntent:
    """
    사용자 메시지를 보고 어느 도메인으로 보낼지 결정합니다.

    우선순위: 주간보고초안 > 설정(데이터 연결) > 주간 회의/등록 > 일정 > 전문가 > 일상.
    덜 흔한 키워드를 먼저 매칭해 오탐을 줄입니다.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return UserIntent.DAILY_CHAT

    if _weekly_report_draft_like(text):
        return UserIntent.WEEKLY_REPORT_DRAFT
    if _settings_like(text):
        return UserIntent.SETTINGS
    if _home_menu_like(text):
        return UserIntent.HOME_MENU
    if _weekly_meeting_like(text):
        return UserIntent.WEEKLY_MEETING
    if _schedule_like(text):
        return UserIntent.SCHEDULE_MANAGEMENT
    if _expert_finder_like(text):
        return UserIntent.EXPERT_FINDER

    return UserIntent.DAILY_CHAT


def _weekly_report_draft_like(text: str) -> bool:
    """주간보고초안 트리거 — `_weekly_meeting_like` 보다 먼저 매칭 (더 구체적)."""
    keywords = (
        "주간보고초안",
        "주간 보고 초안",
        "주간보고 초안",
        "a함수호출",
        "a 함수호출",
        "a 함수 호출",
    )
    return any(k in text for k in keywords)


def _settings_like(text: str) -> bool:
    """'설정' 단독 또는 '데이터 연결' 류 — OAuth 동의 카드 진입."""
    stripped = text.strip()
    if stripped in {"설정", "setting", "settings"}:
        return True
    keywords = (
        "내 데이터 연결",
        "데이터 연결",
        "내 데이터연결",
        "데이터연결",
        "내 정보 연결",
        "oauth 연결",
        "oauth연결",
    )
    return any(k in stripped for k in keywords)


def _home_menu_like(text: str) -> bool:
    """인사·도움말·홈 메뉴 진입."""
    stripped = text.strip()
    if stripped in {
        "안녕",
        "안녕하세요",
        "하이",
        "hi",
        "hello",
        "홈",
        "홈 메뉴",
        "홈메뉴",
        "메뉴",
        "처음으로",
        "도움말",
        "help",
    }:
        return True
    keywords = (
        "뭐할수있어",
        "뭐 할 수 있어",
        "뭘 할 수 있어",
        "무엇을 할 수 있",
        "너 뭐할수있어",
        "너 뭐 할 수 있어",
        "할 수 있는 것",
        "기능 알려",
    )
    return any(k in stripped for k in keywords)


def _weekly_meeting_like(text: str) -> bool:
    """주간 회의 / 팀·인원 등록 관련 표현."""
    patterns = (
        r"주간\s*회의",
        r"주간회의",
        r"주간\s*보고",
        r"주간보고",
        r"주간\s*업무",
        r"주간업무",
        r"주간\s*업무\s*보고",
        r"주간업무보고",
        r"세팅",
        r"팀\s*등록",
        r"인원\s*등록",
        r"회의\s*실\s*등록",
        r"주간\s*미팅",
    )
    return any(re.search(p, text) for p in patterns)


def _schedule_like(text: str) -> bool:
    """캘린더·미팅 예약·일정 관리."""
    keywords = (
        "캘린더",
        "일정",
        "예약",
        "미팅 예약",
        "회의 예약",
        "예약해",
        "스케줄",
        "회의실",
        "회의 잡",
        "잡아줘",
        "잡아 줘",
        "빈 시간",
        "가능 시간",
        "가능한 시간",
        "참석자",
        "초대",
        "calendar",
        "schedule",
        "meeting",
    )
    return any(k in text for k in keywords)


def _expert_finder_like(text: str) -> bool:
    """사내 전문가 추천·검색."""
    keywords = (
        "전문가",
        "잘 아는 사람",
        "잘 아는 분",
        "잘 하는 사람",
        "추천해",
        "추천 좀",
        "누가 잘해",
        "누가 잘 알아",
        "누가 알아",
        "알려줄 사람",
        "알려줄 분",
        "담당자",
        "고수",
        "베테랑",
        "마스터",
        "expert",
    )
    return any(k in text for k in keywords)


def _extract_user_message(payload: dict[str, Any]) -> str | None:
    """
    요청 본문에서 사용자 발화 한 줄을 꺼냅니다.

    - 구글 챗 봇 형식(type=MESSAGE, message.text)과
    - 단순 API용 { "text": "..." }, { "user_message": "..." } 를 모두 지원합니다.
    """
    if not payload:
        return None

    # 구글 챗 (참고 에이전트 main.py 와 동일한 이벤트 형태)
    if payload.get("type") == "MESSAGE":
        text = (payload.get("message") or {}).get("text")
        if text is not None:
            return str(text).strip()

    for key in ("user_message", "text", "query", "message"):
        if key in payload and payload[key] is not None:
            return str(payload[key]).strip()

    return None


def _dispatch_by_intent(
    intent: UserIntent,
    user_message: str,
    # 구글 챗 POST 본문 전체. type=MESSAGE 이면 Firestore conversations 로드·저장 (main 은 payload 만 넘김)
    payload: dict[str, Any],
) -> str | dict[str, Any]:
    """
    의도에 따라 해당 도메인 핸들러만 호출합니다.

    - 일상 대화: 문자열 (텍스트 답변)
    - 그 외 도메인: 구글 챗용 dict (`text` + `cardsV2` 샘플) — 분기 확인용

    Python 3.10+ 의 structural pattern matching 으로 분기해,
    새 도메인을 추가할 때 case 한 줄과 핸들러만 넣으면 되게 했습니다.
    """
    match intent:
        case UserIntent.DAILY_CHAT:
            # 일상 대화: Vertex Gemini + Firestore 맥락 (domains.daily_chat.reply_daily_chat)
            return reply_daily_chat(user_message, chat_event=payload)
        case UserIntent.HOME_MENU:
            return build_home_menu_card(chat_event=payload)
        case UserIntent.EXPERT_FINDER:
            # 사내 전문가 찾기: 키워드 추출 → 즉시 응답 + 백그라운드 검색 thread (domains.expert_finder)
            return handle_expert_finder(user_message, chat_event=payload)
        case UserIntent.SCHEDULE_MANAGEMENT:
            return handle_schedule_management(user_message, chat_event=payload)
        case UserIntent.SETTINGS:
            return build_settings_hub_card()
        case UserIntent.WEEKLY_MEETING:
            # 주간 회의·팀/인원 등록: 샘플 cardsV2 (domains.weekly_meeting)
            return handle_weekly_meeting(user_message, chat_event=payload)
        case UserIntent.WEEKLY_REPORT_DRAFT:
            # 주간보고초안: 즉시 응답 + 백그라운드 thread 가 데이터 수집·Vertex 분석 후 push
            return handle_weekly_report_draft(user_message, chat_event=payload)
        case _:
            # Enum 전수 매칭이므로 이론상 도달하지 않음 — 폴백으로 일상 대화
            return reply_daily_chat(user_message, chat_event=payload)


def _parse_card_parameters(payload: dict[str, Any]) -> dict[str, str]:
    common = payload.get("common") or {}
    action = payload.get("action") or {}
    parameters = common.get("parameters")
    if parameters is None:
        parameters = action.get("parameters")
    if isinstance(parameters, list):
        return {
            str(p.get("key")): str(p.get("value", ""))
            for p in parameters
            if isinstance(p, dict) and p.get("key") is not None
        }
    if isinstance(parameters, dict):
        return {str(k): str(v) for k, v in parameters.items()}
    return {}


def _parse_form_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    common = payload.get("common") or {}
    common_event = common.get("commonEventObject") or {}
    form_inputs = common_event.get("formInputs") or common.get("formInputs") or {}
    if not isinstance(form_inputs, dict):
        return {}
    return form_inputs


@functions_framework.http
def hello_http(request):
    """Cloud Run / Functions Framework HTTP 엔드포인트."""
    if request.method == "GET":
        # OAuth 동의 callback (Phase 2): /oauth/callback?code=...&state=...
        try:
            path = (request.path or "").rstrip("/")
        except Exception:
            path = ""
        if path == "/oauth/callback":
            from domains.weekly_meeting.oauth_callback import handle_oauth_callback

            return handle_oauth_callback(request)

        body = {
            "status": "ok",
            "service": "all-meet-agent",
            "hint": "POST JSON으로 text 또는 구글 챗 MESSAGE 이벤트를 보내면 의도별로 분기합니다.",
        }
        return (
            json.dumps(body, ensure_ascii=False),
            200,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    if request.method != "POST":
        return ("Method Not Allowed", 405, {"Allow": "GET, POST"})

    # ------------------------------------------------------------------
    # Cloud Scheduler 전용 트리거 — Google Chat 이벤트와 분리
    # ------------------------------------------------------------------
    try:
        path = (request.path or "").rstrip("/")
    except Exception:
        path = ""

    if path == "/trigger/weekly-page":
        body = request.get_json(silent=True) or {}
        team_id = str(body.get("team_id") or "").strip()

        def _run_safe(tid: str) -> None:
            try:
                from domains.weekly_meeting.page_creator import (
                    run_weekly_page_job,
                    run_weekly_page_jobs_all,
                )
                result = run_weekly_page_job(tid) if tid else run_weekly_page_jobs_all()
                logger.info("weekly_page_job 완료 [%s]: %s", tid or "all", result)
            except Exception:
                logger.exception("weekly_page_job 실패 [%s]", tid or "all")

        threading.Thread(target=_run_safe, args=(team_id,), daemon=True).start()
        return (
            json.dumps(
                {"status": "accepted", "team_id": team_id or "all"},
                ensure_ascii=False,
            ),
            202,
            {"Content-Type": "application/json; charset=utf-8"},
        )
    # ------------------------------------------------------------------

    payload = request.get_json(silent=True)
    if payload is None:
        return (
            json.dumps({"error": "invalid_json"}, ensure_ascii=False),
            400,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    # 봇이 스페이스에 추가될 때 — 홈 메뉴(설정·OAuth는 6번에서).
    if payload.get("type") == "ADDED_TO_SPACE":
        return (
            json.dumps(build_home_menu_card(chat_event=payload), ensure_ascii=False),
            200,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    if payload.get("type") == "CARD_CLICKED":
        common = payload.get("common") or {}
        action = payload.get("action") or {}
        invoked_function = common.get("invokedFunction") or action.get("function")
        parameters = _parse_card_parameters(payload)
        form_inputs = _parse_form_inputs(payload)
        if invoked_function and invoked_function.startswith("hm_"):
            try:
                reply = handle_home_menu_action(
                    invoked_function=invoked_function,
                    parameters=parameters,
                    chat_event=payload,
                )
            except Exception:
                logger.exception("home menu action failed: invoked_function=%s", invoked_function)
                reply = {"text": "메뉴 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
            return (
                json.dumps(reply, ensure_ascii=False),
                200,
                {"Content-Type": "application/json; charset=utf-8"},
            )
        if invoked_function and invoked_function.startswith("st_"):
            try:
                reply = handle_settings_action(
                    invoked_function=invoked_function,
                    parameters=parameters,
                    form_inputs=form_inputs,
                    chat_event=payload,
                )
            except Exception:
                logger.exception("settings action failed: invoked_function=%s", invoked_function)
                reply = {"text": "설정 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
            return (
                json.dumps(reply, ensure_ascii=False),
                200,
                {"Content-Type": "application/json; charset=utf-8"},
            )
        if invoked_function and invoked_function.startswith("wm_"):
            try:
                reply = handle_weekly_meeting_action(
                    invoked_function=invoked_function,
                    parameters=parameters,
                    form_inputs=form_inputs,
                    chat_event=payload,
                )
            except Exception:
                logger.exception("weekly meeting action failed: invoked_function=%s", invoked_function)
                reply = {"text": "주간업무보고 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
            return (
                json.dumps(reply, ensure_ascii=False),
                200,
                {"Content-Type": "application/json; charset=utf-8"},
            )
        if invoked_function and invoked_function.startswith("sm_"):
            try:
                reply = handle_schedule_management_action(
                    invoked_function=invoked_function,
                    parameters=parameters,
                    form_inputs=form_inputs,
                    chat_event=payload,
                )
            except Exception:
                logger.exception("schedule management action failed: invoked_function=%s", invoked_function)
                reply = {"text": "캘린더 예약 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
            return (
                json.dumps(reply, ensure_ascii=False),
                200,
                {"Content-Type": "application/json; charset=utf-8"},
            )
        return (
            json.dumps({"text": "처리할 카드 액션이 없습니다."}, ensure_ascii=False),
            200,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    user_message = _extract_user_message(payload)
    if payload.get("type") == "MESSAGE" and not user_message:
        return (
            json.dumps(build_home_menu_card(chat_event=payload), ensure_ascii=False),
            200,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    if not user_message:
        return (
            json.dumps(
                {"error": "missing_user_message", "hint": 'body에 text 또는 user_message, 또는 챗 MESSAGE를 넣어 주세요.'},
                ensure_ascii=False,
            ),
            400,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    # 질문/발화 → 의도 → 도메인 핸들러
    intent = match_user_intent(user_message)
    logger.info("intent=%s message=%s", intent.value, user_message[:200])
    reply = _dispatch_by_intent(intent, user_message, payload)

    # Google Chat 동기 응답은 REST Message 스키마만 인정 (text, cardsV2, accessoryWidgets 등).
    # 스키마에 없는 키(예: intent)를 넣으면 HTTP 200이어도 "무효한 메시지 페이로드"로 처리되어
    # 클라이언트에 "응답하지 않음"이 뜰 수 있음 — intent는 위 logger.info 로만 남김.
    out: dict[str, Any] = {}
    if isinstance(reply, dict):
        out.update(reply)
    else:
        out["text"] = reply
    return (
        json.dumps(out, ensure_ascii=False),
        200,
        {"Content-Type": "application/json; charset=utf-8"},
    )


def run() -> None:
    """로컬에서 모듈 실행 시 안내."""
    print("all-meet-agent: 로컬 HTTP는 아래처럼 실행하세요.")
    print("  functions-framework --target=hello_http --debug --port=8080")


if __name__ == "__main__":
    run()
