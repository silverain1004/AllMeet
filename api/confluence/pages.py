"""Confluence Cloud REST — `/wiki/rest/api/content/search` (CQL).

CONVENTIONS.md §11.1 — 도메인 키워드 금지. CQL 조립은 호출 측에서 ``space_key``,
``modified_by_email`` 등 generic 입력만 받음.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from api._auth.confluence_basic import get_confluence_auth
from api.confluence.users import resolve_account_id

logger = logging.getLogger(__name__)


@dataclass
class ListPagesResult:
    ok: bool
    pages: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str = ""


def list_pages_modified(
    *,
    space_key: str,
    modified_by_email: str,
    time_min: str,
    time_max: str,
    fallback_display_names: list[str] | None = None,
    limit: int = 50,
) -> ListPagesResult:
    """CQL: ``space=X AND lastmodified>=... AND contributor.accountid=Y AND type=page``.

    Atlassian Cloud 는 ``contributor.email`` 을 받지 않으므로 이메일을 accountId 로 변환해
    사용. 변환 실패 시 contributor 절 없이 스페이스+기간만 조회하고 ``fallback_display_names``
    로 client-side 필터.

    Args:
        space_key: Confluence 스페이스 키 (예: ``"PC2"``).
        modified_by_email: 기여자 이메일.
        time_min, time_max: ISO8601 (예: ``"2026-04-30T00:00:00Z"``).
        fallback_display_names: accountId 해석이 실패했을 때 ``version.by.displayName``
            매칭에 쓰는 후보 (팀 멤버 이름·닉네임). 비어 있으면 폴백 안 함.

    Returns:
        ``ListPagesResult`` — ``error_kind`` 후보:
        ``"space_key_missing"``, ``"auth_error"``, ``"not_found"``, ``"http_error"``,
        ``"account_id_unresolved"``.
    """
    if not space_key:
        return ListPagesResult(ok=False, error_kind="space_key_missing")

    try:
        auth = get_confluence_auth(modified_by_email)
    except Exception as e:
        logger.warning("confluence auth 실패: %s", e)
        return ListPagesResult(ok=False, error_kind="auth_error")

    cql_min = _iso_to_cql_date(time_min)
    cql_max = _iso_to_cql_date(time_max)

    resolved = resolve_account_id(modified_by_email)
    account_id = (resolved or {}).get("account_id") or ""

    use_fallback = not account_id
    if use_fallback and not fallback_display_names:
        logger.warning(
            "confluence list_pages accountId 미해석 + 폴백 이름 없음 email=%s",
            modified_by_email,
        )
        return ListPagesResult(ok=False, error_kind="account_id_unresolved")

    if account_id:
        cql = (
            f'space = "{space_key}" '
            f'AND lastmodified >= "{cql_min}" '
            f'AND lastmodified < "{cql_max}" '
            f'AND contributor.accountid = "{account_id}" '
            f'AND type = "page"'
        )
    else:
        cql = (
            f'space = "{space_key}" '
            f'AND lastmodified >= "{cql_min}" '
            f'AND lastmodified < "{cql_max}" '
            f'AND type = "page"'
        )

    params = {
        "cql": cql,
        "limit": str(limit),
        "expand": "version",
    }
    encoded = urllib.parse.urlencode(params)
    url = f"{auth['base_url']}/wiki/rest/api/content/search?{encoded}"

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", auth["auth_header"])
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code == 404:
            logger.warning("confluence list_pages 404 cql=%s body=%s", cql, body)
            return ListPagesResult(ok=False, error_kind="not_found")
        if e.code in (401, 403):
            logger.warning("confluence list_pages %s body=%s", e.code, body)
            return ListPagesResult(ok=False, error_kind="auth_error")
        logger.warning("confluence list_pages HTTP %s cql=%s body=%s", e.code, cql, body)
        return ListPagesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("confluence list_pages 실패: %s cql=%s", e, cql)
        return ListPagesResult(ok=False, error_kind="http_error")

    data = json.loads(payload)
    base_url = auth["base_url"]

    pages: list[dict[str, Any]] = []
    needles = _normalize_names(fallback_display_names) if use_fallback else []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        version = item.get("version") or {}
        if use_fallback and not _matches_displayname(version, needles):
            continue
        webui = ((item.get("_links") or {}).get("webui")) or ""
        pages.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "type": str(item.get("type") or ""),
                "modified_time": str(version.get("when") or ""),
                "web_link": f"{base_url}/wiki{webui}" if webui else "",
            }
        )
    return ListPagesResult(ok=True, pages=pages)


def list_pages_by_query(
    *,
    query: str,
    space_key: str = "",
    auth_user_email: str = "",
    limit: int = 50,
) -> ListPagesResult:
    """본문·제목 키워드 검색 (CQL ``text ~ "..."``).

    Args:
        query: 검색 키워드 (CQL escape 자동 처리).
        space_key: 특정 스페이스 한정. 빈 문자열이면 전사 검색 (지정된 인스턴스 전체).
        auth_user_email: Confluence Basic auth 발급용 사용자 email (기본 운영자 계정).
        limit: 페이지 최대 개수.

    Returns:
        ``ListPagesResult`` — 응답 dict 항목:
        ``id, title, type, created_time, modified_time, web_link,
        creator_account_id, creator_name, modifier_account_id, modifier_name``.
    """
    if not (query or "").strip():
        return ListPagesResult(ok=False, error_kind="empty_query")

    try:
        auth = get_confluence_auth(auth_user_email or "")
    except Exception as e:
        logger.warning("confluence auth 실패: %s", e)
        return ListPagesResult(ok=False, error_kind="auth_error")

    safe_q = _escape_cql_text(query)
    cql_parts = [f'text ~ "{safe_q}"', 'type = "page"']
    if (space_key or "").strip():
        cql_parts.insert(0, f'space = "{space_key.strip()}"')
    cql = " AND ".join(cql_parts)

    params = {
        "cql": cql,
        "limit": str(limit),
        "expand": "version,history",
    }
    encoded = urllib.parse.urlencode(params)
    url = f"{auth['base_url']}/wiki/rest/api/content/search?{encoded}"

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", auth["auth_header"])
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code == 404:
            logger.warning("confluence list_pages_by_query 404 cql=%s body=%s", cql, body)
            return ListPagesResult(ok=False, error_kind="not_found")
        if e.code in (401, 403):
            logger.warning("confluence list_pages_by_query %s body=%s", e.code, body)
            return ListPagesResult(ok=False, error_kind="auth_error")
        logger.warning("confluence list_pages_by_query HTTP %s cql=%s body=%s", e.code, cql, body)
        return ListPagesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("confluence list_pages_by_query 실패: %s cql=%s", e, cql)
        return ListPagesResult(ok=False, error_kind="http_error")

    data = json.loads(payload)
    base_url = auth["base_url"]
    pages: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        version = item.get("version") or {}
        history = item.get("history") or {}
        mod_by = version.get("by") if isinstance(version.get("by"), dict) else {}
        crt_by = history.get("createdBy") if isinstance(history.get("createdBy"), dict) else {}
        webui = ((item.get("_links") or {}).get("webui")) or ""
        pages.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "type": str(item.get("type") or ""),
                "created_time": str(history.get("createdDate") or ""),
                "modified_time": str(version.get("when") or ""),
                "web_link": f"{base_url}/wiki{webui}" if webui else "",
                "creator_account_id": str((crt_by or {}).get("accountId") or ""),
                "creator_name": str((crt_by or {}).get("displayName") or ""),
                "modifier_account_id": str((mod_by or {}).get("accountId") or ""),
                "modifier_name": str((mod_by or {}).get("displayName") or ""),
            }
        )
    logger.info(
        "confluence list_pages_by_query cql=%s raw_count=%d",
        cql,
        len(pages),
    )
    return ListPagesResult(ok=True, pages=pages)


def _escape_cql_text(s: str) -> str:
    """CQL ``text ~ "..."`` 내 큰따옴표·백슬래시 escape."""
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _matches_displayname(version: dict[str, Any], needles: list[str]) -> bool:
    """폴백 매칭 — ``version.by.displayName`` 이 후보 중 하나에 부분일치."""
    if not needles:
        return False
    by = version.get("by") if isinstance(version.get("by"), dict) else {}
    name = str((by or {}).get("displayName") or "").strip().lower()
    if not name:
        return False
    return any(n in name or name in n for n in needles)


def _normalize_names(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        s = str(v or "").strip().lower()
        if s:
            out.append(s)
    return out


def _iso_to_cql_date(iso: str) -> str:
    """ISO8601 → ``"YYYY-MM-DD HH:mm"`` (Confluence CQL date format)."""
    try:
        s = (iso or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return (iso or "")[:16].replace("T", " ")
