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
    limit: int = 50,
) -> ListPagesResult:
    """CQL: ``space=X AND lastmodified>=... AND contributor.email=Y AND type=page``.

    Args:
        space_key: Confluence 스페이스 키 (예: ``"PC2"``).
        modified_by_email: 기여자 이메일.
        time_min, time_max: ISO8601 (예: ``"2026-04-30T00:00:00Z"``).

    Returns:
        ``ListPagesResult`` — ``error_kind`` 후보:
        ``"space_key_missing"``, ``"auth_error"``, ``"not_found"``, ``"http_error"``.
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
    cql = (
        f'space = "{space_key}" '
        f'AND lastmodified >= "{cql_min}" '
        f'AND lastmodified < "{cql_max}" '
        f'AND contributor.email = "{modified_by_email}" '
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
        if e.code == 404:
            return ListPagesResult(ok=False, error_kind="not_found")
        if e.code in (401, 403):
            return ListPagesResult(ok=False, error_kind="auth_error")
        logger.warning("confluence list_pages HTTP %s", e.code)
        return ListPagesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("confluence list_pages 실패: %s", e)
        return ListPagesResult(ok=False, error_kind="http_error")

    data = json.loads(payload)
    pages: list[dict[str, Any]] = []
    base_url = auth["base_url"]
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        version = item.get("version") or {}
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


def _iso_to_cql_date(iso: str) -> str:
    """ISO8601 → ``"YYYY-MM-DD HH:mm"`` (Confluence CQL date format)."""
    try:
        s = (iso or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return (iso or "")[:16].replace("T", " ")
