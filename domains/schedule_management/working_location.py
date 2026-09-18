"""Google Calendar '근무 위치'(Working Location) 이벤트로 근무지(군산/서울) 판별.

사용자 OAuth 액세스 토큰(oauth_calendar)을 재사용해 본인 primary 캘린더의
eventType=workingLocation 이벤트를 조회한다. 미동의/조회 실패/미상 시 빈 문자열을
반환하고, 호출부는 이를 "근무지 미상 → 전체 회의실 노출"로 처리한다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from domains.schedule_management.oauth_calendar import get_user_access_token

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

GUNSAN_KEYWORDS = ("군산", "gunsan")
SEOUL_KEYWORDS = ("서울", "seoul")

_TTL_SEC = 600
_cache: dict[tuple[str, str], tuple[float, str]] = {}
_lock = threading.Lock()


def _match_office(label: str) -> str:
    low = (label or "").strip().lower()
    if not low:
        return ""
    if any(k in low for k in GUNSAN_KEYWORDS):
        return "gunsan"
    if any(k in low for k in SEOUL_KEYWORDS):
        return "seoul"
    return ""


def _fetch_working_location_events(user_email: str, date_str: str) -> list[dict[str, Any]]:
    token = get_user_access_token(user_email)
    if not token:
        return []
    try:
        start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return []
    end = start + timedelta(days=1)
    params = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "eventTypes": "workingLocation",
        "maxResults": "10",
    }
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?"
        + urllib.parse.urlencode(params)
    )
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
        logger.warning("workingLocation fetch failed for %s: %s", user_email, e)
        return []
    return [item for item in (data.get("items") or []) if isinstance(item, dict)]


def _office_from_event(item: dict[str, Any]) -> str:
    props = item.get("workingLocationProperties") or {}
    wl_type = str(props.get("type") or "")
    label = ""
    if wl_type == "officeLocation":
        label = str((props.get("officeLocation") or {}).get("label") or "")
    elif wl_type == "customLocation":
        label = str((props.get("customLocation") or {}).get("label") or "")
    return _match_office(label)


def detect_office_for_date(user_email: str, date_str: str) -> str:
    """근무 위치 이벤트로 그날 근무지('gunsan'/'seoul') 판별. 미상이면 ''."""
    email = (user_email or "").strip()
    date_str = (date_str or "").strip()
    if not email or not date_str:
        return ""
    cache_key = (email, date_str)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(cache_key)
        if cached and (now - cached[0]) < _TTL_SEC:
            return cached[1]
    office = ""
    for item in _fetch_working_location_events(email, date_str):
        office = _office_from_event(item)
        if office:
            break
    with _lock:
        _cache[cache_key] = (now, office)
    return office


def clear_cache(user_email: str | None = None) -> None:
    with _lock:
        if user_email:
            for key in [k for k in _cache if k[0] == user_email]:
                _cache.pop(key, None)
        else:
            _cache.clear()
