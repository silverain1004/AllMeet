"""Google Calendar API 클라이언트 (조회 / freebusy / 생성 / 수정 / 삭제)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account


KST = timezone(timedelta(hours=9))
READ_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
WRITE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def is_dry_run() -> bool:
    raw = (os.environ.get("SCHEDULE_DRY_RUN") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class CalendarResult:
    ok: bool
    events: list[dict[str, Any]] = field(default_factory=list)
    busy: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    created_event: dict[str, Any] | None = None
    calendar_list_items: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str = ""
    detail: str = ""
    next_page_token: str = ""


def calendar_error_message(result: CalendarResult) -> str:
    kind = result.error_kind
    if kind == "calendar_not_found":
        return "캘린더를 찾을 수 없습니다. ID와 공유 권한을 확인해 주세요."
    if kind == "calendar_auth_error":
        return "캘린더 인증/권한 오류입니다. OAuth 연결 또는 서비스 계정 공유를 확인해 주세요."
    if kind == "calendar_id_missing":
        return "캘린더가 선택되지 않았습니다."
    if kind == "event_field_missing":
        return "예약 필수 항목이 비어 있습니다."
    if kind == "calendar_http_error":
        return "캘린더 API 호출에 실패했습니다."
    return "캘린더 처리 중 오류가 발생했습니다."


def _refresh_and_get_token(creds: Any) -> str:
    if not getattr(creds, "valid", False):
        creds.refresh(Request())
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("no_access_token")
    return str(token)


def _get_service_account_credentials(
    *,
    write: bool = False,
    subject_email: str | None = None,
) -> Any:
    scopes = WRITE_SCOPES if write else READ_SCOPES
    creds: Any = None
    try:
        creds, _ = google.auth.default(scopes=scopes)
    except Exception:
        creds = None
    if creds is None:
        key_file = os.environ.get("GOOGLE_CALENDAR_KEY_FILE") or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        if key_file:
            try:
                creds = service_account.Credentials.from_service_account_file(key_file, scopes=scopes)
            except Exception:
                creds = None
    if creds is None:
        raise RuntimeError("calendar_auth_error")
    subject = (subject_email or "").strip()
    if subject and hasattr(creds, "with_subject"):
        creds = creds.with_subject(subject)
    return creds


def _get_service_account_token(
    *,
    write: bool = False,
    subject_email: str | None = None,
) -> str:
    creds = _get_service_account_credentials(write=write, subject_email=subject_email)
    return _refresh_and_get_token(creds)


def _http_error_kind(code: int) -> str:
    if code == 404:
        return "calendar_not_found"
    if code in (401, 403):
        return "calendar_auth_error"
    return "calendar_http_error"


def _request(
    *,
    url: str,
    method: str,
    write: bool,
    body: dict[str, Any] | None = None,
    access_token: str | None = None,
    timeout: int = 15,
) -> tuple[dict[str, Any] | None, CalendarResult | None]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        if access_token:
            token = access_token
        else:
            token = _get_service_account_token(write=write)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
    except RuntimeError as e:
        kind = "calendar_auth_error" if str(e) == "calendar_auth_error" else "calendar_access_error"
        return None, CalendarResult(ok=False, error_kind=kind, detail=str(e))
    except urllib.error.HTTPError as e:
        try:
            err_payload = e.read().decode("utf-8")
        except Exception:
            err_payload = ""
        return None, CalendarResult(ok=False, error_kind=_http_error_kind(e.code), detail=err_payload[:500])
    except Exception as e:
        return None, CalendarResult(ok=False, error_kind="calendar_access_error", detail=str(e))
    if method == "DELETE" or not payload:
        return {}, None
    try:
        return json.loads(payload), None
    except json.JSONDecodeError as e:
        return None, CalendarResult(ok=False, error_kind="calendar_http_error", detail=str(e))


def _merge_attendees(
    people: list[str] | None,
    resource_emails: list[str] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for email in people or []:
        e = str(email or "").strip()
        if e and e not in seen:
            out.append({"email": e})
            seen.add(e)
    for rid in resource_emails or []:
        e = str(rid or "").strip()
        if e and e not in seen:
            out.append({"email": e, "resource": True})
            seen.add(e)
    return out


def list_calendar_list(
    *,
    access_token: str | None = None,
    subject_email: str | None = None,
    max_results: int = 250,
) -> CalendarResult:
    url = f"https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults={max_results}"
    token = access_token
    if not token:
        try:
            token = _get_service_account_token(write=False, subject_email=subject_email)
        except RuntimeError as e:
            return CalendarResult(ok=False, error_kind="calendar_auth_error", detail=str(e))
    data, err = _request(url=url, method="GET", write=False, access_token=token)
    if err is not None:
        return err
    items = (data or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
    return CalendarResult(ok=True, calendar_list_items=out)


def extract_meet_link(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    hangout = str(event.get("hangoutLink") or "").strip()
    if hangout:
        return hangout
    conf = event.get("conferenceData") or {}
    for ep in conf.get("entryPoints") or []:
        if isinstance(ep, dict) and str(ep.get("entryPointType") or "") == "video":
            uri = str(ep.get("uri") or "").strip()
            if uri:
                return uri
    return ""


def _event_row(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start") or {}
    end = item.get("end") or {}
    attendees: list[dict[str, Any]] = []
    for att in item.get("attendees") or []:
        if not isinstance(att, dict):
            continue
        attendees.append(
            {
                "email": str(att.get("email") or "").strip(),
                "displayName": str(att.get("displayName") or "").strip(),
                "resource": bool(att.get("resource")),
            }
        )
    organizer = item.get("organizer") or {}
    original = item.get("originalStartTime") or {}
    return {
        "id": str(item.get("id") or ""),
        "summary": str(item.get("summary") or "").strip(),
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "end": str(end.get("dateTime") or end.get("date") or ""),
        "html_link": str(item.get("htmlLink") or ""),
        "location": str(item.get("location") or "").strip(),
        "attendees": attendees,
        # 반복 일정 인스턴스면 원본 시리즈 id — 정기회의 재배치가 시리즈 단위로 묶을 때 쓴다.
        "recurring_event_id": str(item.get("recurringEventId") or ""),
        "original_start": str(original.get("dateTime") or original.get("date") or ""),
        "organizer_email": str(organizer.get("email") or "").strip(),
        "organizer_self": bool(organizer.get("self")),
        "status": str(item.get("status") or ""),
    }


def list_events(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str = "",
    max_results: int = 30,
    access_token: str | None = None,
    page_token: str = "",
) -> CalendarResult:
    params: dict[str, str] = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max_results),
    }
    if q:
        params["q"] = q
    if page_token:
        params["pageToken"] = page_token
    encoded = urllib.parse.urlencode(params)
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events?{encoded}"
    )
    data, err = _request(url=url, method="GET", write=False, access_token=access_token)
    if err is not None:
        return err
    items = (data or {}).get("items") or []
    out = [_event_row(item) for item in items if isinstance(item, dict)]
    return CalendarResult(
        ok=True, events=out, next_page_token=str((data or {}).get("nextPageToken") or "")
    )


def list_events_all(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str = "",
    access_token: str | None = None,
    max_pages: int = 10,
) -> CalendarResult:
    """``list_events`` 를 nextPageToken 끝까지(최대 ``max_pages``) 따라가 합친다 — 수개월 범위 조회용."""
    events: list[dict[str, Any]] = []
    token = ""
    for _ in range(max_pages):
        result = list_events(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            q=q,
            max_results=250,
            access_token=access_token,
            page_token=token,
        )
        if not result.ok:
            return result if not events else CalendarResult(ok=True, events=events)
        events.extend(result.events)
        token = result.next_page_token
        if not token:
            break
    return CalendarResult(ok=True, events=events)


def list_instances(
    *,
    calendar_id: str,
    event_id: str,
    time_min: str,
    time_max: str,
    access_token: str | None = None,
    max_pages: int = 5,
) -> CalendarResult:
    """반복 일정 시리즈의 인스턴스(예외 포함) — GET /events/{id}/instances."""
    if not calendar_id or not event_id:
        return CalendarResult(ok=False, error_kind="event_field_missing")
    events: list[dict[str, Any]] = []
    token = ""
    for _ in range(max_pages):
        params = {"timeMin": time_min, "timeMax": time_max, "maxResults": "250"}
        if token:
            params["pageToken"] = token
        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(calendar_id, safe='')}/events/"
            f"{urllib.parse.quote(event_id, safe='')}/instances?{urllib.parse.urlencode(params)}"
        )
        data, err = _request(url=url, method="GET", write=False, access_token=access_token)
        if err is not None:
            return err if not events else CalendarResult(ok=True, events=events)
        events.extend(
            _event_row(item) for item in (data or {}).get("items") or [] if isinstance(item, dict)
        )
        token = str((data or {}).get("nextPageToken") or "")
        if not token:
            break
    return CalendarResult(ok=True, events=events)


def freebusy_query(
    *,
    calendar_ids: list[str],
    time_min: str,
    time_max: str,
    access_token: str | None = None,
) -> CalendarResult:
    if not calendar_ids:
        return CalendarResult(ok=True, busy={})
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": cid} for cid in calendar_ids if cid],
    }
    url = "https://www.googleapis.com/calendar/v3/freeBusy"
    data, err = _request(
        url=url, method="POST", write=False, body=body, access_token=access_token
    )
    if err is not None:
        return err
    calendars = (data or {}).get("calendars") or {}
    busy: dict[str, list[dict[str, str]]] = {}
    for cid, info in calendars.items():
        if not isinstance(info, dict):
            continue
        slots = info.get("busy") or []
        normalized: list[dict[str, str]] = []
        for slot in slots:
            if isinstance(slot, dict):
                normalized.append(
                    {
                        "start": str(slot.get("start") or ""),
                        "end": str(slot.get("end") or ""),
                    }
                )
        busy[str(cid)] = normalized
    return CalendarResult(ok=True, busy=busy)


def create_event(
    *,
    calendar_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    attendees: list[str] | None = None,
    resource_emails: list[str] | None = None,
    location: str = "",
    description: str = "",
    time_zone: str = "Asia/Seoul",
    send_updates: str = "none",
    add_google_meet: bool = False,
    access_token: str | None = None,
    recurrence: list[str] | None = None,
    conference_data: dict[str, Any] | None = None,
) -> CalendarResult:
    if is_dry_run():
        return CalendarResult(
            ok=True,
            created_event={
                "id": "dry-run",
                "htmlLink": "",
                "summary": summary,
                "hangoutLink": "https://meet.google.com/dry-run-placeholder",
            },
        )
    if not calendar_id:
        return CalendarResult(ok=False, error_kind="calendar_id_missing")
    if not summary or not start_iso or not end_iso:
        return CalendarResult(ok=False, error_kind="event_field_missing")

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": time_zone},
        "end": {"dateTime": end_iso, "timeZone": time_zone},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    merged_attendees = _merge_attendees(attendees, resource_emails)
    if merged_attendees:
        body["attendees"] = merged_attendees
    if recurrence:
        body["recurrence"] = list(recurrence)
    if conference_data:
        # 기존 일정의 conferenceData 를 그대로 넘기면 같은 Meet 링크를 재사용한다.
        body["conferenceData"] = conference_data
    elif add_google_meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"allmeet-{int(datetime.now().timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    query: dict[str, str] = {"sendUpdates": send_updates}
    if add_google_meet or conference_data:
        query["conferenceDataVersion"] = "1"
    params = urllib.parse.urlencode(query)
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events?{params}"
    )
    data, err = _request(
        url=url, method="POST", write=True, body=body, access_token=access_token
    )
    if err is not None:
        return err
    return CalendarResult(ok=True, created_event=data or {})


def delete_event(
    *,
    calendar_id: str,
    event_id: str,
    access_token: str | None = None,
    send_updates: str = "",
) -> CalendarResult:
    if is_dry_run():
        return CalendarResult(ok=True)
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}"
    )
    if send_updates:
        url += "?" + urllib.parse.urlencode({"sendUpdates": send_updates})
    _, err = _request(url=url, method="DELETE", write=True, access_token=access_token)
    if err is not None:
        return err
    return CalendarResult(ok=True)


def get_event(
    *,
    calendar_id: str,
    event_id: str,
    access_token: str | None = None,
) -> CalendarResult:
    if not calendar_id or not event_id:
        return CalendarResult(ok=False, error_kind="event_field_missing")
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}"
    )
    data, err = _request(url=url, method="GET", write=False, access_token=access_token)
    if err is not None:
        return err
    return CalendarResult(ok=True, created_event=data or {})


def patch_event(
    *,
    calendar_id: str,
    event_id: str,
    summary: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    resource_emails: list[str] | None = None,
    time_zone: str = "Asia/Seoul",
    send_updates: str = "none",
    access_token: str | None = None,
    recurrence: list[str] | None = None,
) -> CalendarResult:
    if is_dry_run():
        return CalendarResult(ok=True, created_event={"id": event_id})
    if not calendar_id or not event_id:
        return CalendarResult(ok=False, error_kind="event_field_missing")
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if start_iso:
        body["start"] = {"dateTime": start_iso, "timeZone": time_zone}
    if end_iso:
        body["end"] = {"dateTime": end_iso, "timeZone": time_zone}
    if location is not None:
        body["location"] = location
    if recurrence is not None:
        body["recurrence"] = list(recurrence)
    if attendees is not None or resource_emails is not None:
        people = attendees
        resources = resource_emails
        # PATCH는 attendees 배열을 통째로 교체한다(기존 요소 폐기 — API 문서 명시).
        # None으로 넘어온 쪽(사람/회의실 리소스)은 기존 이벤트에서 읽어 보존한다.
        if people is None or resources is None:
            existing = get_event(
                calendar_id=calendar_id, event_id=event_id, access_token=access_token
            )
            if existing.ok:
                current = (existing.created_event or {}).get("attendees") or []
                if resources is None:
                    resources = [
                        str(a.get("email") or "")
                        for a in current
                        if isinstance(a, dict) and a.get("resource")
                    ]
                if people is None:
                    people = [
                        str(a.get("email") or "")
                        for a in current
                        if isinstance(a, dict) and not a.get("resource")
                    ]
        merged = _merge_attendees(people, resources)
        if merged:
            body["attendees"] = merged
    if not body:
        return CalendarResult(ok=False, error_kind="event_field_missing")
    params = urllib.parse.urlencode({"sendUpdates": send_updates})
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}"
        f"?{params}"
    )
    data, err = _request(
        url=url, method="PATCH", write=True, body=body, access_token=access_token
    )
    if err is not None:
        return err
    return CalendarResult(ok=True, created_event=data or {})


def patch_event_raw(
    *,
    calendar_id: str,
    event_id: str,
    body: dict[str, Any],
    send_updates: str = "none",
    access_token: str | None = None,
) -> CalendarResult:
    """임의 필드 PATCH — 백업 복원용(start/end/attendees/recurrence/status 원본 그대로)."""
    if is_dry_run():
        return CalendarResult(ok=True, created_event={"id": event_id})
    if not calendar_id or not event_id or not body:
        return CalendarResult(ok=False, error_kind="event_field_missing")
    url = (
        "https://www.googleapis.com/calendar/v3/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}/events/{urllib.parse.quote(event_id, safe='')}"
        f"?{urllib.parse.urlencode({'sendUpdates': send_updates})}"
    )
    data, err = _request(url=url, method="PATCH", write=True, body=body, access_token=access_token)
    if err is not None:
        return err
    return CalendarResult(ok=True, created_event=data or {})


def to_kst_iso(date_str: str, time_str: str) -> str:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    return dt.isoformat()


def day_range_kst(date_str: str) -> tuple[str, str]:
    start_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=KST)
    end_dt = start_dt + timedelta(days=1)
    return start_dt.isoformat(), end_dt.isoformat()


def busy_from_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for e in events or []:
        start = str(e.get("start") or "")
        end = str(e.get("end") or "")
        if start and end:
            out.append({"start": start, "end": end})
    return out
