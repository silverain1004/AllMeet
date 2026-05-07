"""Google Calendar v3 — `events.list` generic CRUD.

CONVENTIONS.md §11.1 — 도메인 키워드 금지. ``q`` 는 호출 측에서 조립.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from api._auth.service_account import get_access_token

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


@dataclass
class ListEventsResult:
    ok: bool
    events: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str = ""


def list_events(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str | None = None,
    max_results: int = 50,
    credentials_source: str = "service_account",
    user_email: str | None = None,
) -> ListEventsResult:
    """주어진 캘린더에서 시간 범위 + 검색어로 이벤트 목록.

    Args:
        calendar_id: 캘린더 ID (그룹 캘린더의 ``c_b9...@group.calendar.google.com`` 또는 ``"primary"``).
        time_min, time_max: RFC3339 (예: ``"2026-04-30T00:00:00Z"``).
        q: 캘린더 검색어. None 이면 전체.
        credentials_source: ``"service_account"`` (Phase 1, SA 공유 캘린더) 또는
            ``"user_oauth"`` (Phase 2 개인 캘린더 — ``user_email`` 필수).

    Returns:
        ``ListEventsResult`` — ``error_kind`` 후보:
        ``"not_found"``, ``"http_error"``, ``"auth_error"``, ``"auth_required"``.
    """
    if not calendar_id:
        return ListEventsResult(ok=False, error_kind="calendar_id_missing")

    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max_results),
    }
    if q:
        params["q"] = q
    encoded = urllib.parse.urlencode(params)
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events?{encoded}"
    )

    try:
        if credentials_source == "user_oauth":
            from api._auth.user_oauth import AuthRequiredError, get_user_access_token

            try:
                token = get_user_access_token(user_email or "", [_SCOPE])
            except AuthRequiredError:
                return ListEventsResult(ok=False, error_kind="auth_required")
        else:
            token = get_access_token([_SCOPE])

        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ListEventsResult(ok=False, error_kind="not_found")
        if e.code in (401, 403):
            return ListEventsResult(ok=False, error_kind="auth_error")
        logger.warning("calendar list_events HTTP %s", e.code)
        return ListEventsResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("calendar list_events 실패: %s", e)
        return ListEventsResult(ok=False, error_kind="auth_error")

    data = json.loads(payload)
    events: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or {}
        end = item.get("end") or {}
        events.append(
            {
                "id": str(item.get("id") or ""),
                "summary": str(item.get("summary") or ""),
                "description": str(item.get("description") or ""),
                "start": str(start.get("dateTime") or start.get("date") or ""),
                "end": str(end.get("dateTime") or end.get("date") or ""),
            }
        )
    return ListEventsResult(ok=True, events=events)
