"""기존 함수들을 도구 어댑터로 감싸 TOOL_REGISTRY 에 등록한다.

원본 함수 시그니처는 절대 바꾸지 않고, 여기서 **args 를 받아 호출 + 결과를
``{"ok", "error_kind", "detail", ...}`` 로 정규화하는 얇은 래퍼만 둔다.

import 시점에 ``register_default_tools()`` 가 자동 호출되어 도구가 등록된다.
"""

from __future__ import annotations

import logging
from typing import Any

from domains.agent.config import is_dry_run
from domains.agent.tools import Tool, register

logger = logging.getLogger(__name__)

_registered = False


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[str]:
    """문자열/리스트/None 을 문자열 리스트로 정규화."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _err(error_kind: str, detail: str = "") -> dict[str, Any]:
    return {"ok": False, "error_kind": error_kind, "detail": detail}


# ---------------------------------------------------------------------------
# 읽기 전용 도구 (Phase 0 / Phase C 검증용)
# ---------------------------------------------------------------------------


def _run_find_free_slots(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import freebusy_query

    calendar_ids = _as_list(kwargs.get("calendar_ids") or kwargs.get("calendar_id"))
    time_min = str(kwargs.get("time_min") or "")
    time_max = str(kwargs.get("time_max") or "")
    if not (time_min and time_max):
        return _err("missing_args", "time_min/time_max 필요")
    res = freebusy_query(calendar_ids=calendar_ids, time_min=time_min, time_max=time_max)
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "busy": res.busy}


def _run_list_calendar_events(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import list_events

    calendar_id = str(kwargs.get("calendar_id") or "")
    time_min = str(kwargs.get("time_min") or "")
    time_max = str(kwargs.get("time_max") or "")
    if not (calendar_id and time_min and time_max):
        return _err("missing_args", "calendar_id/time_min/time_max 필요")
    res = list_events(
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        q=str(kwargs.get("q") or ""),
        max_results=int(kwargs.get("max_results") or 30),
    )
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "events": res.events}


def _run_search_emails(**kwargs: Any) -> dict[str, Any]:
    from api.gmail.messages import list_messages_by_query

    user_email = str(kwargs.get("user_email") or "")
    query = str(kwargs.get("query") or "")
    if not user_email:
        return _err("missing_args", "user_email 필요")
    if not query.strip():
        return _err("empty_query", "query 필요")
    res = list_messages_by_query(
        user_email=user_email,
        query=query,
        time_min=kwargs.get("time_min"),
        time_max=kwargs.get("time_max"),
        max_results=int(kwargs.get("max_results") or 30),
    )
    if not res.ok:
        return _err(res.error_kind or "http_error")
    messages = res.messages or []
    return {"ok": True, "messages": messages, "count": len(messages), "found": len(messages) > 0}


def _run_search_confluence(**kwargs: Any) -> dict[str, Any]:
    from api.confluence.pages import list_pages_by_query

    query = str(kwargs.get("query") or "")
    if not query.strip():
        return _err("empty_query", "query 필요")
    res = list_pages_by_query(
        query=query,
        space_key=str(kwargs.get("space_key") or ""),
        auth_user_email=str(kwargs.get("auth_user_email") or ""),
        limit=int(kwargs.get("limit") or 30),
    )
    if not res.ok:
        return _err(res.error_kind or "http_error")
    pages = res.pages or []
    return {"ok": True, "pages": pages, "count": len(pages), "found": len(pages) > 0}


def _run_find_expert(**kwargs: Any) -> dict[str, Any]:
    from domains.expert_finder.handler import _build_result_card

    keyword = str(kwargs.get("keyword") or kwargs.get("query") or "")
    if not keyword.strip():
        return _err("empty_query", "keyword 필요")
    try:
        card = _build_result_card(keyword, str(kwargs.get("requester_email") or ""))
    except Exception as e:  # noqa: BLE001
        logger.warning("find_expert 실패: %s", e)
        return _err("http_error", str(e))
    return {"ok": True, "card": card}


def _run_get_confluence_page_body(**kwargs: Any) -> dict[str, Any]:
    from api.confluence.pages import get_page_body

    page_id = str(kwargs.get("page_id") or "")
    if not page_id:
        return _err("missing_args", "page_id 필요")
    body = get_page_body(page_id=page_id, max_chars=int(kwargs.get("max_chars") or 1500))
    if not body:
        return _err("not_found", "본문을 읽지 못함")
    return {"ok": True, "body": body}


def _run_get_my_recent_artifacts(**kwargs: Any) -> dict[str, Any]:
    from domains.agent.memory import load_user_memory

    user_email = str(kwargs.get("user_email") or "")
    if not user_email:
        return _err("missing_args", "user_email 필요")
    items = load_user_memory(user_email, int(kwargs.get("limit") or 5))
    return {"ok": True, "artifacts": items}


def _run_get_email_body(**kwargs: Any) -> dict[str, Any]:
    from api.gmail.messages import get_message_body

    message_id = str(kwargs.get("message_id") or "")
    user_email = str(kwargs.get("user_email") or "")
    if not (message_id and user_email):
        return _err("missing_args", "message_id/user_email 필요")
    body = get_message_body(
        message_id=message_id,
        user_email=user_email,
        max_chars=int(kwargs.get("max_chars") or 1000),
    )
    if not body:
        return _err("not_found", "본문을 읽지 못함")
    return {"ok": True, "body": body}


# ---------------------------------------------------------------------------
# 생성 도구 — LLM 콘텐츠 생성. 외부 변경이 없으므로 side_effect=False (승인 불필요).
# 결과 content_html 을 create_confluence_page 에 $ref 로 연결하는 용도.
# ---------------------------------------------------------------------------


def _stringify_source(source: Any) -> str:
    """$ref 로 들어온 참조자료(문자열/리스트/dict)를 프롬프트용 텍스트로 평탄화."""
    if source is None:
        return ""
    if isinstance(source, str):
        return source.strip()
    if isinstance(source, (list, tuple)):
        return "\n".join(_stringify_source(v) for v in source if v is not None).strip()
    if isinstance(source, dict):
        parts = []
        for k, v in source.items():
            if k in ("ok", "error_kind", "detail", "dry_run"):
                continue
            parts.append(f"{k}: {_stringify_source(v)}")
        return "\n".join(parts).strip()
    return str(source).strip()


_CONTENT_FORMAT_HINTS = {
    "checklist": "결과는 실행 가능한 점검 항목들의 목록으로, 각 줄을 '- ' 로 시작하는 불릿으로 작성하라.",
    "roadmap": "결과는 기간/단계별 로드맵으로, 각 단계를 '- ' 불릿으로 명확히 구분해 작성하라.",
    "summary": "결과는 핵심만 간결히 요약한 글로 작성하라.",
    "draft": "결과는 바로 사용할 수 있는 문서 초안으로 작성하라.",
}


def _build_content_prompt(instruction: str, source: Any, content_type: str) -> str:
    src_text = _stringify_source(source)
    fmt = _CONTENT_FORMAT_HINTS.get(content_type, _CONTENT_FORMAT_HINTS["draft"])
    ref_block = f"\n[참조자료]\n{src_text}\n" if src_text else ""
    return f"""당신은 기업용 업무 에이전트 'All-Meet'의 콘텐츠 작성기입니다.
아래 [요청]에 맞춰 한국어로 콘텐츠를 작성하세요.{ref_block}
[요청]
{instruction}

[작성 지침]
{fmt}
- 참조자료가 있으면 그 내용을 근거로 삼되, 없는 사실을 지어내지 마라.
- 군더더기 설명·머리말 없이 결과 콘텐츠만 출력하라.
"""


def _to_html(text: str) -> str:
    """평문을 간단한 Confluence storage HTML 로 변환.

    '- '/'* ' 또는 '1.' 로 시작하는 줄은 <ul><li>, 그 외는 <p> 로 감싼다.
    """
    import html as _html
    import re as _re

    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    out: list[str] = []
    in_list = False
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        bullet = bool(_re.match(r"^([-*]|\d+[.)])\s+", stripped))
        if bullet:
            item = _re.sub(r"^([-*]|\d+[.)])\s+", "", stripped)
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_html.escape(item)}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{_html.escape(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _run_generate_content(**kwargs: Any) -> dict[str, Any]:
    from domains.agent._llm import generate_text

    instruction = str(kwargs.get("instruction") or "")
    if not instruction.strip():
        return _err("missing_args", "instruction 필요")
    source = kwargs.get("source")
    require_source = bool(kwargs.get("require_source"))
    if require_source and not _stringify_source(source):
        return _err("missing_source", "참조자료(source)가 필요합니다")
    content_type = str(kwargs.get("content_type") or "draft")
    prompt = _build_content_prompt(instruction, source, content_type)
    text = generate_text(prompt, max_output_tokens=int(kwargs.get("max_tokens") or 1200))
    if not text:
        return _err("generation_failed", "콘텐츠 생성 실패")
    return {"ok": True, "content": text, "content_html": _to_html(text)}


# ---------------------------------------------------------------------------
# 쓰기 도구 (Phase D) — side_effect=True. 승인 게이트 + dry-run 보호.
# ---------------------------------------------------------------------------


def _resolve_start_end(kwargs: dict[str, Any]) -> tuple[str, str]:
    """start_iso/end_iso 직접 지정 또는 date+time+duration_minutes 조합 지원."""
    from domains.schedule_management.calendar_client import to_kst_iso
    from datetime import datetime, timedelta

    start_iso = str(kwargs.get("start_iso") or "")
    end_iso = str(kwargs.get("end_iso") or "")
    if start_iso and end_iso:
        return start_iso, end_iso
    date = str(kwargs.get("meeting_date") or kwargs.get("date") or "")
    time = str(kwargs.get("meeting_time") or kwargs.get("time") or "")
    if not (date and time):
        return start_iso, end_iso
    start = to_kst_iso(date, time)
    duration = int(kwargs.get("duration_minutes") or 30)
    end_dt = datetime.fromisoformat(start) + timedelta(minutes=duration)
    return start, end_dt.isoformat()


def _run_create_meeting(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import (
        create_event,
        extract_meet_link,
    )

    calendar_id = str(kwargs.get("calendar_id") or "")
    summary = str(kwargs.get("summary") or kwargs.get("title") or "")
    start_iso, end_iso = _resolve_start_end(kwargs)
    if not (calendar_id and summary and start_iso and end_iso):
        return _err("missing_args", "calendar_id/summary/start/end 필요")
    res = create_event(
        calendar_id=calendar_id,
        summary=summary,
        start_iso=start_iso,
        end_iso=end_iso,
        attendees=_as_list(kwargs.get("attendee_emails") or kwargs.get("attendees")),
        location=str(kwargs.get("location") or ""),
        description=str(kwargs.get("description") or ""),
        add_google_meet=bool(kwargs.get("add_google_meet") or kwargs.get("auto_meet")),
    )
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    event = res.created_event or {}
    return {
        "ok": True,
        "dry_run": is_dry_run(),
        "event_id": str(event.get("id") or ""),
        "meet_link": extract_meet_link(event),
        "html_link": str(event.get("htmlLink") or ""),
    }


def _run_cancel_meeting(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import delete_event

    calendar_id = str(kwargs.get("calendar_id") or "")
    event_id = str(kwargs.get("event_id") or "")
    if not (calendar_id and event_id):
        return _err("missing_args", "calendar_id/event_id 필요")
    res = delete_event(calendar_id=calendar_id, event_id=event_id)
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "dry_run": is_dry_run()}


def _run_update_meeting(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import patch_event

    calendar_id = str(kwargs.get("calendar_id") or "")
    event_id = str(kwargs.get("event_id") or "")
    if not (calendar_id and event_id):
        return _err("missing_args", "calendar_id/event_id 필요")
    start_iso, end_iso = _resolve_start_end(kwargs)
    res = patch_event(
        calendar_id=calendar_id,
        event_id=event_id,
        summary=kwargs.get("summary") or kwargs.get("title"),
        start_iso=start_iso or None,
        end_iso=end_iso or None,
        location=kwargs.get("location"),
        attendees=_as_list(kwargs.get("attendee_emails") or kwargs.get("attendees")) or None,
    )
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "dry_run": is_dry_run()}


def _run_create_confluence_page(**kwargs: Any) -> dict[str, Any]:
    title = str(kwargs.get("title") or "")
    html_content = str(kwargs.get("html_content") or kwargs.get("body") or "")
    parent_id = str(kwargs.get("parent_id") or "")
    space_key = str(kwargs.get("space_key") or "")
    if not (title and parent_id and space_key):
        return _err("missing_args", "title/parent_id/space_key 필요")
    if is_dry_run():
        return {"ok": True, "dry_run": True, "preview": {"title": title, "space_key": space_key}}
    try:
        from api.confluence.client import ConfluenceClient

        client = ConfluenceClient(str(kwargs.get("auth_user_email") or ""))
        page = client.create_page(
            title=title,
            html_content=html_content,
            parent_id=parent_id,
            space_key=space_key,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("create_confluence_page 실패: %s", e)
        return _err("http_error", str(e))
    return {"ok": True, "page_id": str(page.get("id") or "")}


def _run_send_chat_message(**kwargs: Any) -> dict[str, Any]:
    space_name = str(kwargs.get("space_name") or "")
    text = str(kwargs.get("text") or kwargs.get("message") or "")
    if not (space_name and text):
        return _err("missing_args", "space_name/text 필요")
    if is_dry_run():
        return {"ok": True, "dry_run": True, "preview": {"space_name": space_name, "text": text}}
    from api.chat.messages import post_message_to_space

    ok = post_message_to_space(space_name=space_name, payload={"text": text})
    if not ok:
        return _err("http_error", "chat push 실패")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 등록
# ---------------------------------------------------------------------------


def register_default_tools() -> None:
    """기본 도구 카탈로그 등록 (idempotent)."""
    global _registered
    if _registered:
        return

    # --- 읽기 전용 ---
    register(Tool(
        name="find_free_slots",
        description=(
            "여러 캘린더의 busy(바쁜) 구간을 조회해 빈 시간을 찾는다. "
            "회의 생성(create_meeting) 전에 반드시 먼저 호출해 가능한 시간을 확인할 것. "
            "calendar_ids(이메일/캘린더ID 배열), time_min, time_max(RFC3339)를 받고 "
            "{busy: {calendar_id: [{start,end}]}} 를 반환한다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_ids": {"type": "array", "items": {"type": "string"}},
                "time_min": {"type": "string", "description": "RFC3339 시작"},
                "time_max": {"type": "string", "description": "RFC3339 끝"},
            },
            "required": ["calendar_ids", "time_min", "time_max"],
        },
        run=_run_find_free_slots,
    ))
    register(Tool(
        name="list_calendar_events",
        description=(
            "한 캘린더에서 기간 내 이벤트 목록을 조회한다. 기존 일정 확인·중복 점검에 쓴다. "
            "calendar_id, time_min, time_max, (선택) q 검색어를 받는다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
                "q": {"type": "string"},
            },
            "required": ["calendar_id", "time_min", "time_max"],
        },
        run=_run_list_calendar_events,
    ))
    register(Tool(
        name="search_emails",
        description=(
            "특정 사용자(OAuth 동의자)의 Gmail 을 키워드로 검색한다(메타데이터만, 본문 X). "
            "user_email, query 를 받고 {messages:[{subject,from,date,...}]} 반환. "
            "권한 없으면 error_kind=auth_required."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_email": {"type": "string"},
                "query": {"type": "string"},
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
            },
            "required": ["user_email", "query"],
        },
        run=_run_search_emails,
    ))
    register(Tool(
        name="search_confluence",
        description=(
            "Confluence 페이지를 본문·제목 키워드로 검색한다. query, (선택) space_key 를 받고 "
            "{pages:[{title,web_link,...}]} 반환."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "space_key": {"type": "string"},
            },
            "required": ["query"],
        },
        run=_run_search_confluence,
    ))
    register(Tool(
        name="find_expert",
        description=(
            "사내 데이터(Drive·Confluence·Calendar·동의자 Gmail)를 종합해 특정 주제의 사내 전문가를 추천한다. "
            "keyword 를 받고 결과 카드를 반환한다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "requester_email": {"type": "string"},
            },
            "required": ["keyword"],
        },
        run=_run_find_expert,
    ))
    register(Tool(
        name="get_confluence_page_body",
        description=(
            "Confluence 페이지 한 개의 본문 텍스트를 읽는다. search_confluence 는 제목·링크만 주므로, "
            "내용을 참조·요약·추출하려면 이 도구로 본문을 읽어야 한다. page_id 를 받고 {body} 를 반환한다. "
            "page_id 는 보통 이전 search_confluence 결과의 pages[i].id 를 $ref 로 넘긴다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["page_id"],
        },
        run=_run_get_confluence_page_body,
    ))
    register(Tool(
        name="get_email_body",
        description=(
            "Gmail 메시지 한 개의 본문 텍스트를 읽는다. search_emails 는 메타만 주므로, 내용을 참조·요약하려면 "
            "이 도구로 본문을 읽어야 한다. message_id, user_email(OAuth 동의자)을 받고 {body} 를 반환한다. "
            "message_id 는 보통 이전 search_emails 결과의 messages[i].id 를 $ref 로 넘긴다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "user_email": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["message_id", "user_email"],
        },
        run=_run_get_email_body,
    ))
    register(Tool(
        name="get_my_recent_artifacts",
        description=(
            "요청자가 최근 만든 산출물(페이지/회의) 목록을 조회한다. '지난번에 만든 거 이어서' 같은 "
            "연속 작업에서 이전 결과를 참조할 때 쓴다. user_email 을 받고 {artifacts:[{kind,ref_id,title,goal}]} 반환."
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_email": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["user_email"],
        },
        run=_run_get_my_recent_artifacts,
    ))
    register(Tool(
        name="generate_content",
        description=(
            "LLM 으로 새 문서/체크리스트/로드맵/요약을 작성한다. 외부 변경이 없는 생성 도구다. "
            "instruction(무엇을 쓸지), (선택) source(참조자료 — 보통 검색·본문읽기 결과를 $ref 로 연결), "
            "content_type(checklist|roadmap|summary|draft), require_source(검색→생성 체인이면 true) 을 받고 "
            "{content, content_html} 을 반환한다. "
            "생성 결과를 문서로 남기려면 content_html 을 create_confluence_page 의 html_content 에 $ref 로 연결할 것."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "source": {"description": "참조자료(문자열/리스트/dict). $ref 허용"},
                "content_type": {
                    "type": "string",
                    "enum": ["checklist", "roadmap", "summary", "draft"],
                },
                "require_source": {
                    "type": "boolean",
                    "description": "true 이면 source 가 비어 있을 때 실패(검색→생성 체인)",
                },
                "max_tokens": {"type": "integer"},
            },
            "required": ["instruction"],
        },
        run=_run_generate_content,
    ))

    # --- 쓰기 (side_effect) ---
    register(Tool(
        name="create_meeting",
        description=(
            "Google Calendar 에 회의를 생성한다. 되돌릴 수 없으므로 반드시 사전에 find_free_slots 로 "
            "빈 시간을 확인한 뒤 호출할 것. calendar_id, summary(title), "
            "(start_iso+end_iso) 또는 (meeting_date+meeting_time+duration_minutes), "
            "attendee_emails, add_google_meet 를 받는다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "summary": {"type": "string"},
                "meeting_date": {"type": "string", "description": "YYYY-MM-DD"},
                "meeting_time": {"type": "string", "description": "HH:MM"},
                "duration_minutes": {"type": "integer"},
                "attendee_emails": {"type": "array", "items": {"type": "string"}},
                "add_google_meet": {"type": "boolean"},
            },
            "required": ["calendar_id", "summary"],
        },
        run=_run_create_meeting,
        side_effect=True,
    ))
    register(Tool(
        name="cancel_meeting",
        description="기존 회의(이벤트)를 삭제한다. calendar_id, event_id 를 받는다. 되돌릴 수 없음.",
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "event_id": {"type": "string"},
            },
            "required": ["calendar_id", "event_id"],
        },
        run=_run_cancel_meeting,
        side_effect=True,
    ))
    register(Tool(
        name="update_meeting",
        description=(
            "기존 회의의 시간/제목/참석자를 수정한다. calendar_id, event_id + 변경할 필드를 받는다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "attendee_emails": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["calendar_id", "event_id"],
        },
        run=_run_update_meeting,
        side_effect=True,
    ))
    register(Tool(
        name="create_confluence_page",
        description=(
            "Confluence 에 새 페이지를 생성한다. title, html_content, parent_id, space_key 를 받는다. "
            "되돌릴 수 없으므로 신중히."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "html_content": {"type": "string"},
                "parent_id": {"type": "string"},
                "space_key": {"type": "string"},
            },
            "required": ["title", "parent_id", "space_key"],
        },
        run=_run_create_confluence_page,
        side_effect=True,
    ))
    register(Tool(
        name="send_chat_message",
        description=(
            "지정한 Google Chat space 에 메시지를 보낸다. space_name, text 를 받는다. "
            "사용자가 명시하지 않은 대상에는 보내지 말 것."
        ),
        parameters={
            "type": "object",
            "properties": {
                "space_name": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["space_name", "text"],
        },
        run=_run_send_chat_message,
        side_effect=True,
    ))

    _registered = True


register_default_tools()
