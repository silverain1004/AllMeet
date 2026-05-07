"""주간회의/휴가 일정 조회 유틸."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request
from typing import Any

import google.auth
from google.auth.transport.requests import Request


@dataclass
class LookupResult:
    ok: bool
    events: list[dict[str, str]]
    error_kind: str = ""


def _week_range(*, is_next_week: bool) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    if is_next_week:
        monday = monday + timedelta(days=7)
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _get_access_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/calendar.readonly"])
    if not creds.valid:
        creds.refresh(Request())
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("no_access_token")
    return token


def _calendar_list_events(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str,
) -> LookupResult:
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "q": q,
        "maxResults": "30",
    }
    encoded = urllib.parse.urlencode(params)
    url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id, safe='')}/events?{encoded}"
    req = urllib.request.Request(url, method="GET")
    try:
        token = _get_access_token()
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:  # type: ignore[name-defined]
        if e.code == 404:
            return LookupResult(ok=False, events=[], error_kind="calendar_not_found")
        return LookupResult(ok=False, events=[], error_kind="calendar_http_error")
    except Exception:
        return LookupResult(ok=False, events=[], error_kind="calendar_access_error")

    import json

    data = json.loads(payload)
    items = data.get("items") or []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        start = item.get("start") or {}
        end = item.get("end") or {}
        start_value = str(start.get("dateTime") or start.get("date") or "")
        end_value = str(end.get("dateTime") or end.get("date") or "")
        out.append({"summary": summary, "start": start_value, "end": end_value})
    return LookupResult(ok=True, events=out)


def lookup_weekly_meeting(*, team_name: str, is_next_week: bool, calendar_id: str = "primary") -> LookupResult:
    time_min, time_max = _week_range(is_next_week=is_next_week)
    q = f"{team_name} 주간회의"
    return _calendar_list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q)


def lookup_member_vacation(
    *,
    member_keywords: list[str],
    is_next_week: bool,
    calendar_id: str = "primary",
) -> LookupResult:
    time_min, time_max = _week_range(is_next_week=is_next_week)
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for keyword in member_keywords:
        q = keyword.strip()
        if not q:
            continue
        result = _calendar_list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q)
        if not result.ok:
            return result
        for event in result.events:
            summary = event.get("summary", "")
            lower = summary.lower()
            if ("휴가" not in summary) and ("연차" not in summary) and ("반차" not in summary) and ("vacation" not in lower):
                continue
            key = (summary, event.get("start", ""))
            if key in seen:
                continue
            seen.add(key)
            matched.append(event)
    matched.sort(key=lambda e: e.get("start", ""))
    return LookupResult(ok=True, events=matched)
