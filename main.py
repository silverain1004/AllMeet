"""Application entrypoint for Cloud Run (Functions Framework HTTP).

기존 services_all-meet-agent와 동일하게 `hello_http`를 타깃으로 두고,
`gcloud run deploy ... --source .` 로 빌드·배포할 수 있습니다.

진입점에서는 HTTP 파싱만 하고, 질문 유형에 따라 domains 쪽으로 분기합니다.
"""

from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from typing import Any

import functions_framework

from domains.daily_chat import (
    build_added_to_space_card,
    build_home_menu_card,
    build_settings_hub_card,
    handle_home_menu_action,
    handle_settings_action,
    reply_daily_chat,
    reply_with_home_menu,
)
from domains.agent import (
    handle_agent_action,
    handle_agent_clarification,
    handle_agent_request,
    handle_agent_revision,
)
from domains.agent import store as agent_store
from domains.agent.landing import wrap_daily_chat_with_recovery, wrap_with_agent_cta
from domains.routing.context import load_ctx_block
from domains.routing.intent import classify_intent, deterministic_intent, is_plan_revision
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
# 의도 분류 — LLM + Firestore 대화 맥락 (domains.routing.intent)
# ---------------------------------------------------------------------------


class UserIntent(str, Enum):
    """아래 match/case 와 1:1로 매핑되는 의도."""

    AGENT = "agent"
    DAILY_CHAT = "daily_chat"
    EXPERT_FINDER = "expert_finder"
    HOME_MENU = "home_menu"
    SCHEDULE_MANAGEMENT = "schedule_management"
    SETTINGS = "settings"
    WEEKLY_MEETING = "weekly_meeting"
    WEEKLY_REPORT_DRAFT = "weekly_report_draft"


def match_user_intent(
    user_message: str,
    ctx_block: str = "",
    *,
    active_plan: dict[str, Any] | None = None,
) -> UserIntent:
    """결정적 fast-path(설정·주간보고초안·agent·전문가·일정·주간회의) → 애매하면 pro LLM 분류."""
    label = classify_intent(user_message, ctx_block, active_plan=active_plan)
    try:
        return UserIntent(label)
    except ValueError:
        return UserIntent.DAILY_CHAT


def _clarification_expired(created_at_iso: str, *, minutes: int = 15) -> bool:
    """명료화 레코드가 만료됐는지(생성 후 N분 초과). 파싱 실패 시 만료 아님으로 본다."""
    if not (created_at_iso or "").strip():
        return False
    try:
        from datetime import datetime, timezone

        created = datetime.fromisoformat(created_at_iso)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age > minutes * 60
    except Exception:
        return False


def _should_consume_clarification(
    user_message: str,
    clarification: dict[str, Any],
    ctx_block: str,
) -> bool:
    """후속 발화를 명료화 답변으로 소비할지 판정.

    만료됐거나, 새 발화가 독립적으로 결정적 라우팅(설정·agent·도메인 fast-path)을 트리거하면
    답변이 아니라 '새 작업'으로 보고 소비하지 않는다(하이재킹·누수 방지).
    """
    if _clarification_expired(str(clarification.get("created_at") or "")):
        return False
    if deterministic_intent(user_message, ctx_block) is not None:
        return False
    return True


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
    *,
    ctx_block: str = "",
) -> str | dict[str, Any]:
    """
    의도에 따라 해당 도메인 핸들러만 호출합니다.

    - 일상 대화: 문자열 (텍스트 답변)
    - 그 외 도메인: 구글 챗용 dict (`text` + `cardsV2` 샘플) — 분기 확인용

    Python 3.10+ 의 structural pattern matching 으로 분기해,
    새 도메인을 추가할 때 case 한 줄과 핸들러만 넣으면 되게 했습니다.
    """
    match intent:
        case UserIntent.AGENT:
            return handle_agent_request(
                user_message, chat_event=payload, ctx_block=ctx_block
            )
        case UserIntent.DAILY_CHAT:
            # 일상 대화: Vertex Gemini + Firestore 맥락 (domains.daily_chat.reply_daily_chat)
            # action-like 발화면 'AI에게 맡기기' 회수 CTA 부착(라우팅 false-negative 안전망).
            reply = reply_daily_chat(user_message, chat_event=payload)
            return wrap_daily_chat_with_recovery(reply, user_message=user_message)
        case UserIntent.HOME_MENU:
            return reply_with_home_menu(user_message, chat_event=payload)
        case UserIntent.EXPERT_FINDER:
            # 사내 전문가 찾기: 키워드 추출 → 즉시 응답 + 백그라운드 검색 thread (domains.expert_finder)
            reply = handle_expert_finder(user_message, chat_event=payload)
            return wrap_with_agent_cta(reply, user_message=user_message, intent_value="expert_finder")
        case UserIntent.SCHEDULE_MANAGEMENT:
            reply = handle_schedule_management(user_message, chat_event=payload)
            return wrap_with_agent_cta(
                reply, user_message=user_message, intent_value="schedule_management"
            )
        case UserIntent.SETTINGS:
            return build_settings_hub_card()
        case UserIntent.WEEKLY_MEETING:
            # 주간 회의·팀/인원 등록: 샘플 cardsV2 (domains.weekly_meeting)
            return handle_weekly_meeting(user_message, chat_event=payload)
        case UserIntent.WEEKLY_REPORT_DRAFT:
            # 주간보고초안: 즉시 응답 + 백그라운드 thread 가 데이터 수집·Vertex 분석 후 push
            reply = handle_weekly_report_draft(user_message, chat_event=payload)
            return wrap_with_agent_cta(
                reply, user_message=user_message, intent_value="weekly_report_draft"
            )
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

    if path == "/trigger/weekly-draft-notify":
        def _run_notify() -> None:
            try:
                from domains.weekly_report.auto_notify import send_weekly_draft_to_all
                result = send_weekly_draft_to_all()
                logger.info("weekly_draft_notify 완료: %s", result)
            except Exception:
                logger.exception("weekly_draft_notify 실패")

        threading.Thread(target=_run_notify, daemon=False).start()
        return (
            json.dumps({"status": "accepted"}, ensure_ascii=False),
            202,
            {"Content-Type": "application/json; charset=utf-8"},
        )

    if path == "/trigger/weekly-confluence-reminder":
        def _run_confluence_reminder() -> None:
            try:
                from domains.weekly_report.auto_notify import send_confluence_reminder_to_all
                result = send_confluence_reminder_to_all()
                logger.info("weekly_confluence_reminder 완료: %s", result)
            except Exception:
                logger.exception("weekly_confluence_reminder 실패")

        threading.Thread(target=_run_confluence_reminder, daemon=False).start()
        return (
            json.dumps({"status": "accepted"}, ensure_ascii=False),
            202,
            {"Content-Type": "application/json; charset=utf-8"},
        )

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

    if path == "/trigger/daily-briefing":
        def _run_daily_briefing() -> None:
            try:
                from domains.daily_brief.auto_notify import send_daily_briefing_to_all
                result = send_daily_briefing_to_all()
                logger.info("daily_briefing 완료: %s", result)
            except Exception:
                logger.exception("daily_briefing 실패")

        threading.Thread(target=_run_daily_briefing, daemon=False).start()
        return (
            json.dumps({"status": "accepted"}, ensure_ascii=False),
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

    # 봇이 스페이스에 추가/재추가될 때 — 미연결이면 데이터 연결 안내, 연결됐으면 홈 메뉴.
    if payload.get("type") == "ADDED_TO_SPACE":
        return (
            json.dumps(build_added_to_space_card(chat_event=payload), ensure_ascii=False),
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
        if invoked_function and invoked_function.startswith("ag_"):
            try:
                reply = handle_agent_action(
                    invoked_function=invoked_function,
                    parameters=parameters,
                    chat_event=payload,
                )
            except Exception:
                logger.exception("agent action failed: invoked_function=%s", invoked_function)
                reply = {"text": "에이전트 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
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
        if invoked_function and invoked_function.startswith("wr_"):
            try:
                user_email = parameters.get("user_email", "")
                draft_ref_id = parameters.get("draft_ref_id", "")
                if invoked_function == "wr_insert_to_confluence":
                    from domains.weekly_report.confluence_insert import handle_insert_to_confluence
                    reply = handle_insert_to_confluence(
                        user_email=user_email, draft_ref_id=draft_ref_id
                    )
                elif invoked_function == "wr_open_edit_dialog":
                    from domains.weekly_report.edit_dialog import build_edit_dialog
                    reply = build_edit_dialog(
                        draft_ref_id=draft_ref_id, user_email=user_email
                    )
                elif invoked_function == "wr_save_and_insert":
                    from domains.weekly_report.edit_dialog import handle_save_and_insert
                    space_name = (payload.get("space") or {}).get("name") or ""
                    reply = handle_save_and_insert(
                        form_inputs=form_inputs,
                        user_email=user_email,
                        draft_ref_id=draft_ref_id,
                        space_name=space_name,
                    )
                else:
                    reply = {"text": f"알 수 없는 액션입니다: {invoked_function}"}
            except Exception:
                logger.exception("weekly report action failed: invoked_function=%s", invoked_function)
                # dialog-opening / dialog-submit 함수는 반드시 dialog 형식으로 응답해야 한다.
                # text 형식으로 응답하면 Google Chat 이 "대화상자를 로드하지 못했습니다" 오류를 표시함.
                if invoked_function in ("wr_open_edit_dialog", "wr_save_and_insert"):
                    reply = {
                        "action_response": {
                            "type": "DIALOG",
                            "dialog_action": {
                                "dialog": {
                                    "body": {
                                        "sections": [
                                            {
                                                "widgets": [
                                                    {
                                                        "textParagraph": {
                                                            "text": "오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                }
                            },
                        }
                    }
                else:
                    reply = {"text": "초안 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}
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

    # 질문/발화 → 맥락 로드 → intent → 도메인 핸들러
    ctx_block = load_ctx_block(payload)
    user_email = str(((payload.get("user") or {}).get("email") or "")).strip()
    space_name = str(((payload.get("space") or {}).get("name") or "")).strip()

    active_plan = (
        agent_store.find_active_plan(user_email, space_name)
        if user_email and space_name
        else None
    )
    pending_clarification = (
        agent_store.find_pending_clarification(user_email, space_name)
        if user_email and space_name and not active_plan
        else None
    )
    if active_plan and is_plan_revision(user_message, active_plan, ctx_block):
        reply = handle_agent_revision(
            user_message,
            chat_event=payload,
            existing_plan=active_plan,
            ctx_block=ctx_block,
        )
    elif pending_clarification and _should_consume_clarification(
        user_message, pending_clarification, ctx_block
    ):
        # 플래너 되물음에 대한 후속 답변 — 원 요청과 병합해 재계획.
        reply = handle_agent_clarification(
            user_message,
            chat_event=payload,
            existing=pending_clarification,
            ctx_block=ctx_block,
        )
    else:
        if pending_clarification:
            # 새 발화가 답변이 아니라 새 작업/만료 → 명료화 레코드 정리(누수 방지).
            agent_store.clear_clarification(str(pending_clarification.get("plan_id") or ""))
        intent = match_user_intent(user_message, ctx_block, active_plan=active_plan)
        logger.info("intent=%s message=%s", intent.value, user_message[:200])
        reply = _dispatch_by_intent(
            intent, user_message, payload, ctx_block=ctx_block
        )

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
