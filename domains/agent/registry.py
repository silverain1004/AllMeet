"""기존 함수들을 도구 어댑터로 감싸 TOOL_REGISTRY 에 등록한다.

원본 함수 시그니처는 절대 바꾸지 않고, 여기서 **args 를 받아 호출 + 결과를
``{"ok", "error_kind", "detail", ...}`` 로 정규화하는 얇은 래퍼만 둔다.

import 시점에 ``register_default_tools()`` 가 자동 호출되어 도구가 등록된다.

─────────────────────────────────────────────────────────────────────────────
[새 도구 추가 가이드]  ← 이 5단계만 하면 플래너가 코드 수정 없이 자동 활용한다.
  1. api/·domains 의 기존 함수를 `_run_xxx(**kwargs)` 로 감싼다.
     성공: {"ok": True, ...}  (검색·목록류는 found/count 도 넣어 0건 처리·요약 체인에 도움).
     실패: `_err(error_kind, detail)`  (auth_required → 사용자에게 '내 데이터 연결' 안내됨).
  2. register_default_tools() 안에서 register(Tool(name, description, parameters, run, side_effect, examples)).
  3. description 이 핵심 — 플래너는 이것만 읽는다: '언제 쓰는지 + 인자 + 반환 키(요약은
     generate_content 의 source 에 {"$ref": "<단계>.<반환키>"} 로 연결)'. 되돌릴 수 없으면 side_effect=True.
  4. examples=[{"when": "대표 요청", "args": {...}}] 1~2개 — 플래너 few-shot + 검색 회수에 기여.
  5. tests/ 에 원본 함수를 mock 한 단위테스트 추가.
─────────────────────────────────────────────────────────────────────────────
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
# 캘린더 인증 — 개인 캘린더는 요청자 OAuth 토큰, 팀/공유 캘린더는 봇 SA 폴백.
# (schedule_management 도메인과 동일하게 get_user_access_token 사용 → 기존 동의로 바로 동작)
# ---------------------------------------------------------------------------

_PERSONAL_CAL_HINTS = {"primary", "me", "mine", "my", "나", "내 캘린더", "내캘린더", "본인", "내것"}


def _is_personal_calendar(calendar_id: str, user_email: str) -> bool:
    """calendar_id 가 '요청자 본인 캘린더'를 가리키는지 — 비었거나 primary/내 캘린더/본인 이메일."""
    cid = (calendar_id or "").strip().lower()
    if cid in _PERSONAL_CAL_HINTS:
        return True
    if cid and user_email and cid == (user_email or "").strip().lower():
        return True
    if not cid and (user_email or "").strip():  # 캘린더 미지정 + 요청자 알면 본인으로 간주
        return True
    return False


def _user_access_token(user_email: str) -> tuple[str | None, str | None]:
    """요청자 OAuth access token. (token, error_kind). 미연결/실패 시 (None, 'auth_required')."""
    email = (user_email or "").strip()
    if not email:
        return None, "auth_required"
    from domains.schedule_management.oauth_calendar import (
        get_user_access_token,
        is_oauth_linked,
    )

    if not is_oauth_linked(email):
        return None, "auth_required"
    token = get_user_access_token(email)
    if not token:
        return None, "auth_required"
    return token, None


def _resolve_calendar_auth(calendar_id: str, kwargs: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """(resolved_calendar_id, access_token, error_kind).

    개인 캘린더 → 요청자 OAuth 토큰 + calendar_id='primary'. 팀/공유 캘린더 → SA 폴백(token None).
    """
    user_email = str(kwargs.get("user_email") or kwargs.get("requester_email") or "").strip()
    if not _is_personal_calendar(calendar_id, user_email):
        return calendar_id, None, None
    token, err = _user_access_token(user_email)
    if err:
        return (calendar_id or "primary"), None, err
    return "primary", token, None


def _resolve_freebusy_auth(
    calendar_ids: list[str], kwargs: dict[str, Any]
) -> tuple[list[str], str | None, str | None]:
    """freebusy 다중 캘린더용. 개인 캘린더가 섞이면 요청자 토큰으로 조회(개인 id→'primary')."""
    user_email = str(kwargs.get("user_email") or kwargs.get("requester_email") or "").strip()
    ids = [str(c).strip() for c in (calendar_ids or []) if str(c).strip()]
    needs_personal = (not ids and bool(user_email)) or any(
        _is_personal_calendar(c, user_email) for c in ids
    )
    if not needs_personal:
        return ids, None, None
    token, err = _user_access_token(user_email)
    if err:
        return ids, None, err
    out: list[str] = []
    for c in ids or ["primary"]:
        mapped = "primary" if _is_personal_calendar(c, user_email) else c
        if mapped not in out:
            out.append(mapped)
    return out, token, None


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
    calendar_ids, access_token, auth_err = _resolve_freebusy_auth(calendar_ids, kwargs)
    if auth_err:
        return _err(auth_err, "개인 캘린더 조회에는 '내 데이터 연결'(OAuth)이 필요해요.")
    res = freebusy_query(
        calendar_ids=calendar_ids, time_min=time_min, time_max=time_max, access_token=access_token
    )
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "busy": res.busy}


def _run_list_calendar_events(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import list_events

    time_min = str(kwargs.get("time_min") or "")
    time_max = str(kwargs.get("time_max") or "")
    if not (time_min and time_max):
        return _err("missing_args", "time_min/time_max 필요")
    calendar_id, access_token, auth_err = _resolve_calendar_auth(
        str(kwargs.get("calendar_id") or ""), kwargs
    )
    if auth_err:
        return _err(auth_err, "개인 캘린더 조회에는 '내 데이터 연결'(OAuth)이 필요해요.")
    if not calendar_id:
        return _err("calendar_id_missing", "calendar_id 필요(본인 캘린더는 user_email + calendar_id=primary)")
    res = list_events(
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        q=str(kwargs.get("q") or ""),
        max_results=int(kwargs.get("max_results") or 30),
        access_token=access_token,
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


def _confluence_numeric_id(url_or_id: str) -> str:
    """Confluence URL/숫자에서 folder/page 숫자 ID 추출 (page_creator._extract_confluence_id 와 동일)."""
    import re as _re

    text = (url_or_id or "").strip()
    if not text:
        return ""
    m = _re.search(r"/(?:folder|pages|content)/(\d+)", text)
    if m:
        return m.group(1)
    return text if text.isdigit() else ""


def _resolve_report_root_id(team_index: dict[str, Any]) -> str:
    """팀 설정에서 주간보고 root 폴더 ID. 우선순위: root_pages[0] → report_root_page_id."""
    for entry in team_index.get("root_pages") or []:
        raw = str(entry.get("page_id") if isinstance(entry, dict) else entry).strip()
        rid = _confluence_numeric_id(raw)
        if rid:
            return rid
    return _confluence_numeric_id(str(team_index.get("report_root_page_id") or ""))


def _run_find_team_weekly_report(**kwargs: Any) -> dict[str, Any]:
    """팀·날짜 기준 최신 주간보고 페이지를 찾아 본문까지 읽어온다.

    키워드 검색(search_confluence)과 달리 팀 폴더(YYYY년>N분기) 구조를 따라 제목 날짜가
    가장 최신인 페이지를 고르므로 "최신/최근/PC2팀 주간보고" 의미를 정확히 살린다.
    """
    from api.confluence.client import ConfluenceClient
    from api.confluence.pages import get_page_body
    from api.confluence.previous_report import (
        get_latest_weekly_report_page_id_for_team_root,
    )
    from firestore.team_config import find_team_by_email, get_team_from_index, make_team_id

    team_name_in = str(kwargs.get("team_name") or "").strip()
    requester_email = str(
        kwargs.get("requester_email") or kwargs.get("user_email") or ""
    ).strip()

    # 1. 팀 ID — 명시 팀명 우선, 없으면 요청자 소속팀
    team_id = ""
    if team_name_in:
        team_id = make_team_id(team_name_in)
    elif requester_email:
        team_id = find_team_by_email(requester_email) or ""
    if not team_id:
        return {"ok": True, "found": False, "detail": "팀을 찾지 못했어요(team_name 또는 요청자 소속팀 필요)."}

    team_index = get_team_from_index(team_id) or {}
    team_name = str(team_index.get("name") or team_name_in or team_id).strip()
    report_root_id = _resolve_report_root_id(team_index)
    if not report_root_id:
        return {"ok": True, "found": False, "detail": f"{team_name}의 주간보고 폴더(report_root)가 설정되지 않았어요."}

    # 2. 팀·날짜 기준 최신 주간보고 페이지 ID
    try:
        client = ConfluenceClient()
        page_id = get_latest_weekly_report_page_id_for_team_root(
            client, report_root_id, team_name
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("find_team_weekly_report 조회 실패 team=%s: %s", team_name, e)
        return _err("http_error", str(e))

    if not page_id:
        return {"ok": True, "found": False, "detail": f"{team_name}의 최신 주간보고 페이지를 찾지 못했어요."}

    page_id = str(page_id)
    # 3. 본문(요약에 바로 쓸 수 있게) + 제목·링크
    content = get_page_body(page_id=page_id, max_chars=int(kwargs.get("max_chars") or 4000))
    title = ""
    try:
        info = client.get_page(page_id, expand="title")
        title = str((info or {}).get("title") or "")
    except Exception:  # noqa: BLE001
        title = ""
    space_key = str(team_index.get("space_key") or team_index.get("confluence_space_key") or "").strip()
    web_link = ""
    try:
        base = str(getattr(client, "_base_url", "") or "").rstrip("/")
        if base and space_key:
            web_link = f"{base}/wiki/spaces/{space_key}/pages/{page_id}"
    except Exception:  # noqa: BLE001
        web_link = ""

    return {
        "ok": True,
        "found": True,
        "team_name": team_name,
        "page_id": page_id,
        "title": title,
        "web_link": web_link,
        "content": content,
    }


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


def _run_list_meeting_rooms(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.rooms_store import get_rooms

    rooms = get_rooms() or []
    out = [
        {
            "name": str(r.get("display_name") or r.get("name") or "").strip(),
            "capacity": r.get("capacity"),
            "equipment": r.get("equipment") or [],
            "location": str(r.get("location") or "").strip(),
            # 예약용 calendar_id (create_meeting/find_free_slots 의 calendar_id 로 사용)
            "calendar_id": str(r.get("calendar_resource_id") or "").strip(),
        }
        for r in rooms
        if isinstance(r, dict)
    ]
    return {"ok": True, "rooms": out, "count": len(out), "found": len(out) > 0}


def _run_search_drive_files(**kwargs: Any) -> dict[str, Any]:
    from api.drive.files import list_files_by_query

    query = str(kwargs.get("query") or "")
    if not query.strip():
        return _err("empty_query", "query 필요")
    user_email = str(kwargs.get("user_email") or kwargs.get("requester_email") or "").strip()
    # 요청자가 있으면 본인 OAuth(My Drive)로 검색 — 공유 Drive(SHARED_DRIVE_ID) 미설정 환경에서도
    # 동작. 미연결이면 list_files_by_query 가 error_kind=auth_required → '내 데이터 연결' 안내.
    if user_email:
        res = list_files_by_query(query=query, credentials_source="user_oauth", user_email=user_email)
    else:
        res = list_files_by_query(query=query, credentials_source="service_account")
    if not res.ok:
        return _err(res.error_kind or "http_error")
    files = res.files or []
    return {"ok": True, "files": files, "count": len(files), "found": len(files) > 0}


def _run_list_confluence_pages_by_author(**kwargs: Any) -> dict[str, Any]:
    from api.confluence.pages import list_pages_modified

    space_key = str(kwargs.get("space_key") or "")
    email = str(kwargs.get("modified_by_email") or kwargs.get("user_email") or "")
    time_min = str(kwargs.get("time_min") or "")
    time_max = str(kwargs.get("time_max") or "")
    if not (space_key and email and time_min and time_max):
        return _err("missing_args", "space_key/modified_by_email/time_min/time_max 필요")
    res = list_pages_modified(
        space_key=space_key,
        modified_by_email=email,
        time_min=time_min,
        time_max=time_max,
        fallback_display_names=_as_list(kwargs.get("fallback_display_names")),
        limit=int(kwargs.get("limit") or 30),
    )
    if not res.ok:
        return _err(res.error_kind or "http_error")
    pages = res.pages or []
    return {"ok": True, "pages": pages, "count": len(pages), "found": len(pages) > 0}


def _run_list_drive_files_by_author(**kwargs: Any) -> dict[str, Any]:
    from api.drive.files import list_files_modified

    email = str(kwargs.get("modified_by_email") or kwargs.get("user_email") or "")
    time_min = str(kwargs.get("time_min") or "")
    time_max = str(kwargs.get("time_max") or "")
    if not (email and time_min and time_max):
        return _err("missing_args", "modified_by_email/time_min/time_max 필요")
    # 요청자 본인의 My Drive(user_oauth, 'me' in writers)에서 조회 — '내가 최근 수정한 파일' 대응.
    # 공유 Drive(SHARED_DRIVE_ID) 미설정 환경에서도 동작. 미연결 시 auth_required.
    res = list_files_modified(
        modified_by_email=email,
        time_min=time_min,
        time_max=time_max,
        credentials_source="user_oauth",
        user_email=email,
    )
    if not res.ok:
        return _err(res.error_kind or "http_error")
    files = res.files or []
    return {"ok": True, "files": files, "count": len(files), "found": len(files) > 0}


def _run_lookup_team_members(**kwargs: Any) -> dict[str, Any]:
    """팀원 이름→이메일 조회 — 회의 초대 등에서 사용자에게 되묻지 않고 이메일을 확보."""
    from firestore.team_config import (
        find_team_by_email,
        get_team_config,
        make_team_id,
    )

    team_name = str(kwargs.get("team_name") or "").strip()
    user_email = str(kwargs.get("user_email") or kwargs.get("requester_email") or "").strip()
    team_id = ""
    if team_name:
        team_id = make_team_id(team_name)
    elif user_email:
        try:
            team_id = find_team_by_email(user_email) or ""
        except Exception:  # noqa: BLE001
            team_id = ""
    if not team_id:
        return {"ok": True, "found": False, "members": [], "detail": "소속 팀을 찾지 못했어요(team_name 또는 요청자 소속팀 필요)."}

    cfg = get_team_config(team_id) or {}
    members: list[dict[str, Any]] = []
    for m in cfg.get("team_members") or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        if not name and not m.get("email"):
            continue
        nick = m.get("nickname")
        nicknames = nick if isinstance(nick, list) else ([str(nick)] if nick else [])
        members.append(
            {"name": name, "email": str(m.get("email") or "").strip(), "nickname": [str(n) for n in nicknames]}
        )
    return {"ok": True, "found": len(members) > 0, "members": members, "count": len(members)}


def _run_lookup_team_vacation(**kwargs: Any) -> dict[str, Any]:
    """팀 휴가자/부재자 조회 — 휴가 캘린더를 팀 설정에서 해석(사용자에게 되묻지 않음)."""
    from config.settings import VACATION_CALENDAR_ID
    from domains.weekly_meeting.schedule_lookup import lookup_member_vacation
    from firestore.team_config import find_team_by_email, get_team_config, make_team_id

    team_name = str(kwargs.get("team_name") or "").strip()
    user_email = str(kwargs.get("user_email") or kwargs.get("requester_email") or "").strip()
    team_id = ""
    if team_name:
        team_id = make_team_id(team_name)
    elif user_email:
        try:
            team_id = find_team_by_email(user_email) or ""
        except Exception:  # noqa: BLE001
            team_id = ""
    if not team_id:
        return {"ok": True, "found": False, "detail": "소속 팀을 찾지 못했어요(team_name 또는 요청자 소속팀 필요)."}

    cfg = get_team_config(team_id) or {}
    cal_id = str(
        cfg.get("vacation_calendar_id") or cfg.get("calendar_id") or VACATION_CALENDAR_ID or ""
    ).strip()
    if not cal_id:
        return {"ok": True, "found": False, "detail": "팀 휴가 캘린더가 설정되지 않았어요."}

    keywords: list[str] = []
    for m in cfg.get("team_members") or []:
        if not isinstance(m, dict):
            continue
        nm = str(m.get("name") or "").strip()
        if nm:
            keywords.append(nm)
        nick = m.get("nickname")
        for n in nick if isinstance(nick, list) else []:
            if str(n).strip():
                keywords.append(str(n).strip())
    if not keywords:
        return {"ok": True, "found": False, "detail": "팀원이 설정되지 않아 휴가자를 조회할 수 없어요."}

    res = lookup_member_vacation(
        member_keywords=keywords,
        is_next_week=bool(kwargs.get("next_week")),
        calendar_id=cal_id,
    )
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error")
    events = res.events or []
    return {"ok": True, "found": len(events) > 0, "vacations": events, "count": len(events)}


def _pick_relevant_page(intent: str, pages: list[dict[str, Any]]) -> int:
    """검색 결과(여러 페이지) 중 의도에 가장 부합하는 페이지 index 를 LLM 이 고른다. 실패 시 0."""
    import json as _json

    from domains.agent._llm import PLANNER_MODEL, generate_json

    cand = [{"index": i, "title": str(p.get("title") or "")} for i, p in enumerate(pages[:15])]
    prompt = (
        "아래 후보 문서 중 사용자 의도에 가장 부합하는 것 하나의 index 만 고르세요.\n"
        f"[의도] {intent}\n"
        f"[후보(index:title)] {_json.dumps(cand, ensure_ascii=False)}\n"
        '출력: JSON 오브젝트 하나만 — {"index": 0}'
    )
    data = generate_json(prompt, model=PLANNER_MODEL)
    try:
        idx = int(data.get("index"))
        if 0 <= idx < len(pages):
            return idx
    except (TypeError, ValueError):
        pass
    return 0


def _run_find_and_read_confluence(**kwargs: Any) -> dict[str, Any]:
    """Confluence 검색 → 제목 기준 가장 적합한 1건 선택 → 본문 읽기(한 단계). pages[0] 맹신 방지."""
    from api.confluence.pages import get_page_body, list_pages_by_query

    query = str(kwargs.get("query") or "")
    if not query.strip():
        return _err("empty_query", "query 필요")
    res = list_pages_by_query(
        query=query,
        space_key=str(kwargs.get("space_key") or ""),
        auth_user_email=str(kwargs.get("auth_user_email") or ""),
        limit=int(kwargs.get("limit") or 20),
    )
    if not res.ok:
        return _err(res.error_kind or "http_error")
    pages = res.pages or []
    if not pages:
        return {"ok": True, "found": False, "detail": f"'{query}' 검색 결과가 없어요."}
    idx = _pick_relevant_page(str(kwargs.get("intent") or query), pages) if len(pages) > 1 else 0
    page = pages[idx]
    page_id = str(page.get("id") or "")
    body = get_page_body(page_id=page_id, max_chars=int(kwargs.get("max_chars") or 4000))
    if not body:
        return {"ok": True, "found": False, "detail": "관련 페이지를 찾았으나 본문을 읽지 못했어요."}
    return {
        "ok": True,
        "found": True,
        "page_id": page_id,
        "title": str(page.get("title") or ""),
        "web_link": str(page.get("web_link") or ""),
        "content": body,
    }


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
    # 요약/문서가 중간에 잘리지 않도록 기본 토큰을 넉넉히(1200→2048). 긴 결과의 챗 표시 잘림은
    # actions._make_push 가 메시지 분할로 처리한다.
    text = generate_text(prompt, max_output_tokens=int(kwargs.get("max_tokens") or 2048))
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

    calendar_id, access_token, auth_err = _resolve_calendar_auth(
        str(kwargs.get("calendar_id") or ""), kwargs
    )
    if auth_err:
        return _err(auth_err, "개인 캘린더에 일정 생성하려면 '내 데이터 연결'(OAuth)이 필요해요.")
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
        access_token=access_token,
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

    calendar_id, access_token, auth_err = _resolve_calendar_auth(
        str(kwargs.get("calendar_id") or ""), kwargs
    )
    if auth_err:
        return _err(auth_err, "개인 캘린더 일정 취소에는 '내 데이터 연결'(OAuth)이 필요해요.")
    event_id = str(kwargs.get("event_id") or "")
    if not (calendar_id and event_id):
        return _err("missing_args", "calendar_id/event_id 필요")
    res = delete_event(calendar_id=calendar_id, event_id=event_id, access_token=access_token)
    if not res.ok:
        return _err(res.error_kind or "calendar_http_error", res.detail)
    return {"ok": True, "dry_run": is_dry_run()}


def _run_update_meeting(**kwargs: Any) -> dict[str, Any]:
    from domains.schedule_management.calendar_client import patch_event

    calendar_id, access_token, auth_err = _resolve_calendar_auth(
        str(kwargs.get("calendar_id") or ""), kwargs
    )
    if auth_err:
        return _err(auth_err, "개인 캘린더 일정 수정에는 '내 데이터 연결'(OAuth)이 필요해요.")
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
        access_token=access_token,
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
            "{busy: {calendar_id: [{start,end}]}} 를 반환한다. "
            "요청자 '본인' 캘린더의 빈 시간이면 calendar_ids=['primary'] 와 user_email(요청자 이메일)을 넣어라."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_ids": {"type": "array", "items": {"type": "string"}},
                "time_min": {"type": "string", "description": "RFC3339 시작"},
                "time_max": {"type": "string", "description": "RFC3339 끝"},
                "user_email": {"type": "string", "description": "요청자 이메일 — 본인 캘린더 조회 시 필수"},
            },
            "required": ["calendar_ids", "time_min", "time_max"],
        },
        run=_run_find_free_slots,
    ))
    register(Tool(
        name="list_calendar_events",
        description=(
            "한 캘린더에서 기간 내 이벤트 목록을 조회한다. '내 일정/이번주 일정' 등 일정 조회·요약에 쓴다. "
            "calendar_id, time_min, time_max, (선택) q 검색어를 받고 "
            "{events:[{id,summary,start,end,location,attendees}]} 를 반환한다"
            "(요약하려면 generate_content 의 source 에 {\"$ref\": \"<이단계번호>.events\"} 로 연결). "
            "요청자 '본인' 캘린더(내 캘린더/내 일정)이면 calendar_id='primary' 와 user_email(요청자 이메일)을 넣어라. "
            "팀·회의실 등 공유 캘린더는 그 calendar_id 를 그대로 쓴다(user_email 불필요)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "본인 캘린더는 'primary'"},
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
                "q": {"type": "string"},
                "user_email": {"type": "string", "description": "요청자 이메일 — 본인 캘린더 조회 시 필수"},
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
        examples=[{"when": "사내 위키에서 키워드로 문서 검색", "args": {"query": "온보딩 체크리스트"}}],
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
        name="find_team_weekly_report",
        description=(
            "팀의 '최신/최근/지난주/이번주' 주간보고·주간회의 페이지를 정확히 찾아 본문까지 읽어온다. "
            "팀 폴더(연도>분기) 구조에서 제목 날짜가 가장 최신인 페이지를 고르므로, "
            "주간보고/주간회의를 조회·요약하려면 키워드 검색(search_confluence) 대신 이 도구를 우선 사용할 것. "
            "team_name(예: 'PC2팀'. 생략하면 요청자 소속팀 자동 인식), (선택) requester_email 을 받아 "
            "{found, page_id, title, web_link, content} 를 반환한다. 요약은 보통 content 를 generate_content 의 "
            "source 로 바로 연결하면 된다(예: source={\"$ref\": \"1.content\"}). found=false 면 페이지가 없는 것."
        ),
        parameters={
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "예: 'PC2팀'. 없으면 요청자 소속팀"},
                "requester_email": {"type": "string", "description": "요청자 이메일(팀 자동 인식용)"},
                "max_chars": {"type": "integer"},
            },
            "required": [],
        },
        run=_run_find_team_weekly_report,
        examples=[
            {"when": "PC2팀 최신 주간보고 요약", "args": {"team_name": "PC2팀", "requester_email": "me@corp.com"}},
            {"when": "내 팀 최근 주간보고 요약(팀 미지정)", "args": {"requester_email": "me@corp.com"}},
        ],
    ))
    register(Tool(
        name="list_meeting_rooms",
        description=(
            "사내 회의실 목록과 각 방의 예약용 calendar_id(+정원·장비·위치)를 반환한다. "
            "회의실을 예약(create_meeting)하거나 빈 시간 확인(find_free_slots) 하기 전에 이 도구로 방을 "
            "찾아 calendar_id 를 얻어라. 인자는 없고 {rooms:[{name,capacity,equipment,location,calendar_id}]} 반환. "
            "이후 단계에서 특정 방의 calendar_id 는 {\"$ref\": \"<이단계>.rooms.0.calendar_id\"} 로 연결."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        run=_run_list_meeting_rooms,
        examples=[{"when": "회의실 예약/빈 회의실 찾기 전에 방 목록·calendar_id 조회", "args": {}}],
    ))
    register(Tool(
        name="search_drive_files",
        description=(
            "Drive 를 키워드로 검색한다(제목·본문 fullText). query 와 user_email(요청자)을 받아 "
            "요청자 본인 Drive(My Drive)에서 검색하고 {files:[{id,name,web_view_link,modified_time}]} 를 "
            "반환한다(메타데이터·링크만, 본문 미포함). 'Drive/구글드라이브에서 ~자료·문서 찾아줘' 류에 쓴다. "
            "OAuth 미연결이면 error_kind=auth_required('내 데이터 연결' 안내)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "user_email": {"type": "string", "description": "요청자 이메일(본인 Drive 검색)"},
            },
            "required": ["query"],
        },
        run=_run_search_drive_files,
        examples=[{"when": "내 Drive에서 특정 주제 자료 찾기", "args": {"query": "벡터DB", "user_email": "me@corp.com"}}],
    ))
    register(Tool(
        name="list_confluence_pages_by_author",
        description=(
            "특정 사용자가 기간 내 수정한 Confluence 페이지 목록을 조회한다(space 한정). "
            "space_key, modified_by_email(요청자 본인이면 user_email), time_min, time_max(RFC3339)를 받고 "
            "{pages:[{id,title,modified_time,web_link}]} 를 반환한다. '내가/누가 최근 수정·작성한 페이지' 류. "
            "본문이 필요하면 get_confluence_page_body 로 이어서 읽어라(page_id={\"$ref\": \"<이단계>.pages.0.id\"})."
        ),
        parameters={
            "type": "object",
            "properties": {
                "space_key": {"type": "string"},
                "modified_by_email": {"type": "string", "description": "본인이면 요청자 이메일"},
                "time_min": {"type": "string", "description": "RFC3339"},
                "time_max": {"type": "string", "description": "RFC3339"},
                "limit": {"type": "integer"},
            },
            "required": ["space_key", "modified_by_email", "time_min", "time_max"],
        },
        run=_run_list_confluence_pages_by_author,
        examples=[
            {
                "when": "내가 이번 달 수정한 Confluence 페이지 조회",
                "args": {
                    "space_key": "PC2",
                    "modified_by_email": "me@corp.com",
                    "time_min": "2026-06-01T00:00:00Z",
                    "time_max": "2026-06-30T23:59:59Z",
                },
            }
        ],
    ))
    register(Tool(
        name="list_drive_files_by_author",
        description=(
            "요청자 본인의 Drive(My Drive)에서 기간 내 수정한 파일 목록을 조회한다. "
            "modified_by_email(요청자 이메일), time_min, time_max(RFC3339)를 받고 "
            "{files:[{id,name,web_view_link,modified_time}]} 를 반환한다. '내가 최근 수정/작업한 파일' 류. "
            "OAuth 미연결이면 error_kind=auth_required."
        ),
        parameters={
            "type": "object",
            "properties": {
                "modified_by_email": {"type": "string", "description": "본인이면 요청자 이메일"},
                "time_min": {"type": "string", "description": "RFC3339"},
                "time_max": {"type": "string", "description": "RFC3339"},
            },
            "required": ["modified_by_email", "time_min", "time_max"],
        },
        run=_run_list_drive_files_by_author,
        examples=[
            {
                "when": "내가 최근 일주일간 수정한 Drive 파일 조회",
                "args": {
                    "modified_by_email": "me@corp.com",
                    "time_min": "2026-06-17T00:00:00Z",
                    "time_max": "2026-06-24T00:00:00Z",
                },
            }
        ],
    ))
    register(Tool(
        name="lookup_team_members",
        description=(
            "팀원 이름→이메일 명단을 조회한다. 회의 초대·참석자 이메일이 필요할 때 사용자에게 되묻지 말고 "
            "이 도구로 먼저 팀원 명단을 확보하라(예: '나랑 서영은 초대' → 명단에서 서영은 이메일 매칭). "
            "user_email(요청자, 소속팀 자동) 또는 team_name 을 받아 {members:[{name,email,nickname}]} 를 반환. "
            "이후 create_meeting 의 attendee_emails 를 이 결과에서 채운다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "예: 'PC2팀'. 없으면 요청자 소속팀"},
                "user_email": {"type": "string", "description": "요청자 이메일(소속팀 자동 인식)"},
            },
            "required": [],
        },
        run=_run_lookup_team_members,
        examples=[
            {"when": "회의 초대 전 팀원 이메일 확보(이름만 알 때)", "args": {"user_email": "me@corp.com"}},
        ],
    ))
    register(Tool(
        name="lookup_team_vacation",
        description=(
            "팀 휴가자/부재자를 조회한다. user_email(요청자, 소속팀 자동) 또는 team_name, (선택)next_week 를 받아 "
            "팀 설정의 휴가 캘린더에서 팀원 휴가/연차/반차 일정을 조회하고 {vacations:[{summary,start,end}]} 를 반환한다. "
            "'이번주 휴가자 누구', '누가 쉬어/연차' 류. 휴가 캘린더는 팀 설정에서 자동 해석하므로 사용자에게 캘린더를 되묻지 말 것."
        ),
        parameters={
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "예: 'PC2팀'. 없으면 요청자 소속팀"},
                "user_email": {"type": "string", "description": "요청자 이메일(소속팀 자동)"},
                "next_week": {"type": "boolean", "description": "다음주면 true(기본: 이번주)"},
            },
            "required": [],
        },
        run=_run_lookup_team_vacation,
        examples=[{"when": "이번주 우리팀 휴가자 조회", "args": {"user_email": "me@corp.com"}}],
    ))
    register(Tool(
        name="find_and_read_confluence",
        description=(
            "Confluence 에서 문서를 찾아(검색) 제목 기준 가장 적합한 1건을 골라 본문까지 읽어온다. "
            "query 와 (선택)intent(무엇을 찾는지)·space_key 를 받아 {found, page_id, title, web_link, content} 를 반환한다. "
            "검색 결과가 여러 개여도 가장 관련 있는 페이지를 자동 선택하므로(엉뚱한 pages[0] 읽기 방지), "
            "'OO 문서/자료 찾아서 요약' 류에는 search_confluence + get_confluence_page_body 대신 이 도구를 우선 사용하라. "
            "(주간보고/주간회의는 find_team_weekly_report 우선.) 요약은 content 를 generate_content 의 source 에 $ref 로 연결."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "intent": {"type": "string", "description": "무엇을 찾는지(선택 — 후보 선택 정확도↑)"},
                "space_key": {"type": "string"},
            },
            "required": ["query"],
        },
        run=_run_find_and_read_confluence,
        examples=[
            {"when": "E-BIZ 시스템 관련 문서 찾아 요약", "args": {"query": "E-BIZ 시스템", "intent": "E-BIZ 시스템 관련 문서"}},
        ],
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
            "attendee_emails, add_google_meet 를 받는다. "
            "요청자 '본인' 캘린더에 만들면 calendar_id='primary' 와 user_email(요청자 이메일)을 넣어라."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "본인 캘린더는 'primary'"},
                "summary": {"type": "string"},
                "meeting_date": {"type": "string", "description": "YYYY-MM-DD"},
                "meeting_time": {"type": "string", "description": "HH:MM"},
                "duration_minutes": {"type": "integer"},
                "attendee_emails": {"type": "array", "items": {"type": "string"}},
                "add_google_meet": {"type": "boolean"},
                "user_email": {"type": "string", "description": "요청자 이메일 — 본인 캘린더에 생성 시 필수"},
            },
            "required": ["calendar_id", "summary"],
        },
        run=_run_create_meeting,
        side_effect=True,
        examples=[
            {
                "when": "내 캘린더에 회의 생성(빈 시간 확인 후)",
                "args": {
                    "calendar_id": "primary",
                    "user_email": "me@corp.com",
                    "summary": "팀 동기화",
                    "meeting_date": "2026-06-25",
                    "meeting_time": "15:00",
                    "duration_minutes": 30,
                },
            }
        ],
    ))
    register(Tool(
        name="cancel_meeting",
        description=(
            "기존 회의(이벤트)를 삭제한다. calendar_id, event_id 를 받는다. 되돌릴 수 없음. "
            "본인 캘린더 일정이면 calendar_id='primary' + user_email."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "본인 캘린더는 'primary'"},
                "event_id": {"type": "string"},
                "user_email": {"type": "string", "description": "요청자 이메일 — 본인 캘린더 일정 시 필수"},
            },
            "required": ["calendar_id", "event_id"],
        },
        run=_run_cancel_meeting,
        side_effect=True,
    ))
    register(Tool(
        name="update_meeting",
        description=(
            "기존 회의의 시간/제목/참석자를 수정한다. calendar_id, event_id + 변경할 필드를 받는다. "
            "본인 캘린더 일정이면 calendar_id='primary' + user_email."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "본인 캘린더는 'primary'"},
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "attendee_emails": {"type": "array", "items": {"type": "string"}},
                "user_email": {"type": "string", "description": "요청자 이메일 — 본인 캘린더 일정 시 필수"},
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
