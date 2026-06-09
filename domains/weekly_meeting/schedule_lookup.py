"""주간회의/휴가 일정 조회 유틸."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import urllib.parse
import urllib.error
import urllib.request
from typing import Any, Iterable

import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account


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


def _refresh_and_get_token(creds: Any) -> str:
    if not getattr(creds, "valid", False):
        creds.refresh(Request())
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("no_access_token")
    return str(token)


def _get_access_token() -> str:
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    # 1) ADC 우선 (Cloud Run 기본 경로)
    try:
        creds, _ = google.auth.default(scopes=scopes)
        return _refresh_and_get_token(creds)
    except Exception:
        pass

    # 2) 로컬 key file fallback
    key_file = os.environ.get("GOOGLE_CALENDAR_KEY_FILE") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if key_file:
        try:
            creds = service_account.Credentials.from_service_account_file(key_file, scopes=scopes)
            return _refresh_and_get_token(creds)
        except Exception:
            pass

    raise RuntimeError("calendar_auth_error")


def _calendar_list_events(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str = "",
) -> LookupResult:
    params: dict = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "50",
    }
    if q:
        params["q"] = q
    encoded = urllib.parse.urlencode(params)
    url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id, safe='')}/events?{encoded}"
    req = urllib.request.Request(url, method="GET")
    try:
        token = _get_access_token()
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
    except RuntimeError as e:
        if str(e) == "calendar_auth_error":
            return LookupResult(ok=False, events=[], error_kind="calendar_auth_error")
        return LookupResult(ok=False, events=[], error_kind="calendar_access_error")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return LookupResult(ok=False, events=[], error_kind="calendar_not_found")
        if e.code in (401, 403):
            return LookupResult(ok=False, events=[], error_kind="calendar_auth_error")
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


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def lookup_weekly_meeting(*, team_name: str, is_next_week: bool, calendar_id: str = "primary") -> LookupResult:
    time_min, time_max = _week_range(is_next_week=is_next_week)
    q = f"{team_name} 주간회의"
    result = _calendar_list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q)
    if not result.ok:
        return result
    filtered: list[dict[str, str]] = []
    for event in result.events:
        summary = str(event.get("summary") or "")
        if team_name not in summary:
            continue
        if "주간회의" not in summary.replace(" ", ""):
            continue
        filtered.append(event)
    return LookupResult(ok=True, events=filtered)


def lookup_member_vacation(
    *,
    member_keywords: list[str],
    is_next_week: bool,
    calendar_id: str = "primary",
) -> LookupResult:
    time_min, time_max = _week_range(is_next_week=is_next_week)
    return lookup_member_vacation_range(
        member_keywords=member_keywords,
        time_min=time_min,
        time_max=time_max,
        calendar_id=calendar_id,
    )


def _name_matches_summary(keyword: str, summary: str) -> bool:
    """키워드가 이벤트 제목의 이름 부분과 일치하는지 확인.

    '종류(이름)' 형식에서 이름을 추출해 suffix 매칭도 허용.
    예: keyword='이영은', summary='연차(영은)' → '영은' in '이영은' → True
    """
    import re as _re
    q = keyword.lower()
    s = summary.lower()
    if q in s:
        return True
    m = _re.search(r"\(([^)]+)\)", summary)
    if m:
        for name in m.group(1).split(","):
            n = name.strip().lower()
            if n and (n in q or q in n):
                return True
    return False


def lookup_member_vacation_range(
    *,
    member_keywords: list[str],
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> LookupResult:
    """지정된 시간 범위 내 팀원 휴가 이벤트 조회."""
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    vacation_keywords = (
        "휴가", "연차", "반차", "오전반차", "오후반차", "refresh", "vacation",
        "리프레시", "리프레쉬", "출산", "육아", "경조", "공가", "병가",
        "대체", "아이돌봄", "장기근속", "생일", "샌드위치", "조퇴",
    )
    for keyword in member_keywords:
        q = keyword.strip()
        if not q:
            continue
        result = _calendar_list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q)
        if not result.ok:
            return result
        for event in result.events:
            summary = event.get("summary", "")
            if not _contains_any(summary, vacation_keywords):
                continue
            if not _name_matches_summary(q, summary):
                continue
            key = (summary, event.get("start", ""))
            if key in seen:
                continue
            seen.add(key)
            matched.append(event)
    matched.sort(key=lambda e: e.get("start", ""))
    return LookupResult(ok=True, events=matched)


def fetch_all_calendar_events(
    *,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> LookupResult:
    """지정된 시간 범위 내 캘린더 전체 이벤트 조회 (q 필터 없음).

    원본 vacation_fetcher.fetch_calendar_events 와 동일한 방식.
    '종류(이름)' 형식 이벤트를 로컬에서 파싱하기 위한 전용 함수.
    """
    return _calendar_list_events(calendar_id=calendar_id, time_min=time_min, time_max=time_max)


# 일정 공유 표에 채울 모든 카테고리 키워드 (출장/외근/재택/휴가 포함)
_ALL_SCHEDULE_KEYWORDS = (
    "출장", "외근", "재택",
    "휴가", "연차", "반차", "오전반차", "오후반차", "refresh", "vacation",
    "리프레시", "리프레쉬", "출산", "육아", "경조", "공가", "병가",
    "대체", "아이돌봄", "장기근속", "생일", "샌드위치", "조퇴",
)


def lookup_member_schedule_range(
    *,
    member_keywords: list[str],
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> LookupResult:
    """지정된 시간 범위 내 팀원 출장/외근/재택/휴가 이벤트 조회."""
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
            if not _contains_any(summary, _ALL_SCHEDULE_KEYWORDS):
                continue
            if not _name_matches_summary(q, summary):
                continue
            key = (summary, event.get("start", ""))
            if key in seen:
                continue
            seen.add(key)
            matched.append(event)
    matched.sort(key=lambda e: e.get("start", ""))
    return LookupResult(ok=True, events=matched)
