"""공용 데이터 키워드 검색 — Shared Drive · Confluence · 공용 Calendar.

PLAN.md §4 흐름 step 3. 각 호출 try/except 격리, drive/calendar 의 ID 별 호출은
ThreadPoolExecutor 병렬. accountId → email 변환은 캐시 lookup + displayName 폴백.

M2 범위: 공용 갈래만. 개인 데이터(Gmail · 내 Drive · 개인 Calendar) 갈래는 M3.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from api.calendar.events import list_events
from api.confluence.pages import list_pages_by_query
from api.confluence.users import resolve_email_by_account_id
from api.drive.files import list_files_by_query
from api.gmail.messages import list_messages_by_query
from domains.expert_finder.public_sources import lookup_email_by_display_name

logger = logging.getLogger(__name__)

_PARALLEL_WORKERS = 10
# 공용 Calendar 풀 윈도우 — 과거 5년 ~ 미래 1년 (계획 일정 포함).
_CAL_PAST_DAYS = 5 * 365
_CAL_FUTURE_DAYS = 365

# 노이즈 제목 패턴 — 정형 페이지/이벤트는 키워드가 단순 단어로만 등장하는 경우가 많아
# 작성자가 분야 전문가 신호가 아님. hit 자체를 등록 안 함.
# (예: "PC2팀 주간회의" 에 'MES 개발' 단어 한두 번 등장 → 회의록 정리자가 1위 폭주)
_NOISE_TITLE_RE = re.compile(
    r"주간\s*(?:회의|보고|업무\s*보고)"
    r"|월간\s*(?:회의|보고)"
    r"|일간\s*(?:회의|보고)"
    r"|일일\s*(?:회의|보고)"
    r"|데일리\s*스크럼"
    r"|All\s*Hands"
    r"|전사\s*공지"
    r"|\[공지\]"
    r"|\[안내\]",
    re.IGNORECASE,
)

# Gmail 제목 노이즈 — 자동발신·결재·시스템 알림·뉴스레터·답장 prefix 등.
# (`weekly_report/draft._NOISE_SUBJECT_RE` 패턴 차용)
_GMAIL_NOISE_RE = re.compile(
    r"\[ITS"
    r"|\[결재"
    r"|\[Jira\]"
    r"|\[공지\]"
    r"|\[전사"
    r"|\[Slashpage\]"
    r"|보안\s*알림"
    r"|일일\s*다이제스트"
    r"|^\s*Re:|^\s*RE:|^\s*Fw:|^\s*FW:|^\s*Fwd:"
    r"|^\(광고\)"
    r"|광고\s*\|",
    re.IGNORECASE,
)


def _is_noise_title(title: str) -> bool:
    """정형 페이지·이벤트 제목 매칭 (주간회의·주간보고 등)."""
    return bool(_NOISE_TITLE_RE.search(title or ""))


def _is_noise_gmail_subject(subject: str) -> bool:
    """Gmail 제목 노이즈 매칭 (자동발신·답장 prefix 등)."""
    s = (subject or "").strip()
    if not s:
        return True
    return bool(_GMAIL_NOISE_RE.search(s))


# 사람 email 이 아닌 system/resource email (캘린더 자체·서비스 계정·회의실 등) 제외.
# Calendar API 가 group calendar 이벤트의 organizer 를 사람 대신 캘린더 ID 로 반환하는
# 케이스 회피. 후보 풀에 'c_XXX@group.calendar.google.com' 같은 항목 등장 방지.
_SYSTEM_EMAIL_SUFFIXES = (
    "@group.calendar.google.com",
    "@resource.calendar.google.com",
    "@gserviceaccount.com",
    "@calendar.google.com",
)


def _is_system_email(email: str) -> bool:
    s = (email or "").strip().lower()
    if not s:
        return True
    return any(s.endswith(suf) for suf in _SYSTEM_EMAIL_SUFFIXES)


def search_public(
    *,
    keyword: str,
    sources: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """공용 데이터 키워드 검색 → hit 리스트 + 소스별 에러 dict.

    Args:
        keyword: 검색 키워드.
        sources: ``public_sources.build_public_sources()`` 반환 dict.

    Returns:
        ``(hits, errors)``:
        - hits: ``[{source, role, email, name, title, url, when}, ...]``
            ``email`` 가 빈 문자열이면 신원 미해석 (스코어링에서 제외).
        - errors: ``{"drive": "...", "confluence": "...", "calendar": "..."}`` (전부 실패한 경우만).
    """
    member_pool = sources.get("member_pool") or []
    hits: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    futures: dict[concurrent.futures.Future, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
        for did in sources.get("shared_drive_ids") or []:
            fut = ex.submit(_search_drive, keyword=keyword, drive_id=did)
            futures[fut] = f"drive:{did}"
        for sk in sources.get("confluence_space_keys") or []:
            fut = ex.submit(
                _search_confluence,
                keyword=keyword,
                space_key=sk,
                member_pool=member_pool,
            )
            futures[fut] = f"confluence:{sk}"
        for cid in sources.get("calendar_ids") or []:
            fut = ex.submit(_search_calendar, keyword=keyword, calendar_id=cid)
            futures[fut] = f"calendar:{cid}"

        # 소스 타입별 에러 누적 — 한 소스 타입의 모든 ID 가 실패해야 errors 에 등록.
        per_type_errors: dict[str, list[str]] = {"drive": [], "confluence": [], "calendar": []}
        per_type_total: dict[str, int] = {"drive": 0, "confluence": 0, "calendar": 0}

        for fut in concurrent.futures.as_completed(futures):
            label = futures[fut]
            source_type = label.split(":", 1)[0]
            per_type_total[source_type] += 1
            try:
                partial_hits, err = fut.result()
            except Exception as e:
                logger.warning("expert_finder search %s 예외: %s", label, e)
                per_type_errors[source_type].append(f"{label}:exception")
                continue
            if err:
                per_type_errors[source_type].append(f"{label}:{err}")
            else:
                hits.extend(partial_hits)

    for st in ("drive", "confluence", "calendar"):
        if per_type_total[st] > 0 and len(per_type_errors[st]) == per_type_total[st]:
            errors[st] = ";".join(per_type_errors[st])[:300]

    logger.info(
        "expert_finder search_public keyword=%s hits=%d errors=%s",
        keyword,
        len(hits),
        errors,
    )
    return hits, errors


# 공용 Drive 1개 검색 — 작성자(owner)·수정자(last_modifier) 각각 hit 등록.
def _search_drive(*, keyword: str, drive_id: str) -> tuple[list[dict[str, Any]], str]:
    res = list_files_by_query(
        query=keyword,
        drive_id=drive_id,
        credentials_source="service_account",
    )
    if not res.ok:
        return [], res.error_kind or "unknown"

    out: list[dict[str, Any]] = []
    for f in res.files:
        when = (f.get("modified_time") or "").strip()
        url = (f.get("web_view_link") or "").strip()
        title = (f.get("name") or "").strip()
        if _is_noise_title(title):
            continue
        owner_email = (f.get("owner_email") or "").strip()
        owner_name = (f.get("owner_name") or "").strip()
        if owner_email:
            out.append({
                "source": "drive",
                "role": "owner",
                "email": owner_email,
                "name": owner_name,
                "title": title,
                "url": url,
                "when": when,
            })
        mod_email = (f.get("last_modifier_email") or "").strip()
        mod_name = (f.get("last_modifier_name") or "").strip()
        if mod_email and mod_email.lower() != owner_email.lower():
            out.append({
                "source": "drive",
                "role": "modifier",
                "email": mod_email,
                "name": mod_name,
                "title": title,
                "url": url,
                "when": when,
            })
    return out, ""


# Confluence 1 space 검색 — creator·modifier 각각 hit. accountId → email 변환 + displayName 폴백.
def _search_confluence(
    *,
    keyword: str,
    space_key: str,
    member_pool: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    res = list_pages_by_query(query=keyword, space_key=space_key)
    if not res.ok:
        return [], res.error_kind or "unknown"

    out: list[dict[str, Any]] = []
    for p in res.pages:
        title = (p.get("title") or "").strip()
        if _is_noise_title(title):
            continue
        url = (p.get("web_link") or "").strip()
        created_when = (p.get("created_time") or "").strip()
        mod_when = (p.get("modified_time") or "").strip()

        crt_acc = (p.get("creator_account_id") or "").strip()
        crt_name = (p.get("creator_name") or "").strip()
        crt_email = _confluence_account_to_email(crt_acc, crt_name, member_pool)
        if crt_email:
            out.append({
                "source": "confluence",
                "role": "creator",
                "email": crt_email,
                "name": crt_name,
                "title": title,
                "url": url,
                "when": created_when or mod_when,
            })

        mod_acc = (p.get("modifier_account_id") or "").strip()
        mod_name = (p.get("modifier_name") or "").strip()
        mod_email = _confluence_account_to_email(mod_acc, mod_name, member_pool)
        if mod_email and mod_email.lower() != (crt_email or "").lower():
            out.append({
                "source": "confluence",
                "role": "modifier",
                "email": mod_email,
                "name": mod_name,
                "title": title,
                "url": url,
                "when": mod_when,
            })
    return out, ""


# 공용 Calendar 1개 검색 — organizer/creator/attendees hit.
def _search_calendar(*, keyword: str, calendar_id: str) -> tuple[list[dict[str, Any]], str]:
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=_CAL_PAST_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=_CAL_FUTURE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    res = list_events(
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        q=keyword,
        max_results=50,
    )
    if not res.ok:
        return [], res.error_kind or "unknown"

    out: list[dict[str, Any]] = []
    for ev in res.events:
        title = (ev.get("summary") or "").strip()
        if _is_noise_title(title):
            continue
        when = (ev.get("start") or "").strip()
        url = (ev.get("html_link") or "").strip()

        org_email = (ev.get("organizer_email") or "").strip()
        if org_email and not _is_system_email(org_email):
            out.append({
                "source": "calendar",
                "role": "organizer",
                "email": org_email,
                "name": "",
                "title": title,
                "url": url,
                "when": when,
            })
        crt_email = (ev.get("creator_email") or "").strip()
        if (
            crt_email
            and not _is_system_email(crt_email)
            and crt_email.lower() != org_email.lower()
        ):
            out.append({
                "source": "calendar",
                "role": "creator",
                "email": crt_email,
                "name": "",
                "title": title,
                "url": url,
                "when": when,
            })
        for a in ev.get("attendees") or []:
            ae = (a.get("email") or "").strip()
            if not ae or ae.lower() in (org_email.lower(), crt_email.lower()):
                continue
            if _is_system_email(ae):
                continue
            if str(a.get("response_status") or "") == "declined":
                continue
            out.append({
                "source": "calendar",
                "role": "attendee",
                "email": ae,
                "name": "",
                "title": title,
                "url": url,
                "when": when,
            })
    return out, ""


# accountId → email 변환. 1) 캐시 inverted lookup → 2) displayName 폴백.
def _confluence_account_to_email(
    account_id: str,
    display_name: str,
    member_pool: list[dict[str, Any]],
) -> str:
    if account_id:
        email = resolve_email_by_account_id(account_id)
        if email:
            return email
    if display_name:
        email = lookup_email_by_display_name(display_name, member_pool)
        if email:
            return email
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# M3: 개인 데이터 갈래 — OAuth 동의자의 Gmail · 개인 Calendar 만.
# 내 Drive 는 §10 개인정보 우려로 제외 (PLAN.md §5.3).
# ──────────────────────────────────────────────────────────────────────────────


def search_private(
    *,
    keyword: str,
    consenting_emails: list[str],
    time_min: str | None = None,
    time_max: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """동의자별 개인 데이터 키워드 검색 — Gmail · 개인 Calendar.

    Args:
        keyword: 검색 키워드.
        consenting_emails: OAuth ``status="linked"`` 사용자 email 리스트.
        time_min, time_max: ISO8601. None 이면 시간 제약 없음 (Calendar 는 매우 넓은 범위로 폴백).

    Returns:
        ``(hits, errors)`` — ``search_public`` 과 동일 구조.
        hit 은 본인이 발신/주최한 것만 (받은 메일·초대받은 일정은 전문성 신호 약하므로 제외).
    """
    hits: list[dict[str, Any]] = []
    if not consenting_emails:
        return hits, {}

    futures: dict[concurrent.futures.Future, str] = {}
    per_user_errors: dict[str, dict[str, str]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
        for email in consenting_emails:
            fut = ex.submit(
                _search_personal_per_user,
                keyword=keyword,
                user_email=email,
                time_min=time_min,
                time_max=time_max,
            )
            futures[fut] = email

        for fut in concurrent.futures.as_completed(futures):
            user_email = futures[fut]
            try:
                partial_hits, errs = fut.result()
            except Exception as e:
                logger.warning("expert_finder search_private %s 예외: %s", user_email, e)
                per_user_errors[user_email] = {"gmail": "exception", "personal_calendar": "exception"}
                continue
            hits.extend(partial_hits)
            per_user_errors[user_email] = errs

    # 소스 타입별 — 모든 사용자가 같은 종류 에러를 받았으면 그것만 errors 에 등록.
    errors: dict[str, str] = {}
    for st in ("gmail", "personal_calendar"):
        non_auth_errs = [
            f"{u}:{errs.get(st)}"
            for u, errs in per_user_errors.items()
            if errs.get(st) and errs.get(st) not in ("", "auth_required")
        ]
        # auth_required 는 동의 안 한 케이스라 정상 — 에러 아님. 나머지 에러가 전수면 등록.
        total_actual = sum(1 for u, errs in per_user_errors.items() if errs.get(st) and errs.get(st) != "auth_required")
        if non_auth_errs and total_actual >= len(consenting_emails) / 2:
            errors[st] = ";".join(non_auth_errs)[:300]

    logger.info(
        "expert_finder search_private keyword=%s users=%d hits=%d errors=%s",
        keyword,
        len(consenting_emails),
        len(hits),
        errors,
    )
    return hits, errors


# 한 사용자의 Gmail + 개인 Calendar 키워드 검색. 본인이 보낸/주최한 것만 hit.
def _search_personal_per_user(
    *,
    keyword: str,
    user_email: str,
    time_min: str | None,
    time_max: str | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    out: list[dict[str, Any]] = []
    errs: dict[str, str] = {"gmail": "", "personal_calendar": ""}

    # Gmail — 본인 발신 메일만 (받은 메일은 전문성 신호 약함).
    try:
        gm_res = list_messages_by_query(
            user_email=user_email,
            query=keyword,
            time_min=time_min,
            time_max=time_max,
            max_results=30,
        )
        if not gm_res.ok:
            errs["gmail"] = gm_res.error_kind or "unknown"
        else:
            user_needle = user_email.lower()
            for m in gm_res.messages:
                subject = (m.get("subject") or "").strip()
                if _is_noise_gmail_subject(subject):
                    continue
                from_addr = (m.get("from") or "").lower()
                if user_needle not in from_addr:
                    # 본인 발신이 아니면 skip — 받은 메일은 자기가 그 분야 한 신호 약함.
                    continue
                out.append({
                    "source": "gmail",
                    "role": "sender",
                    "email": user_email,
                    "name": "",
                    "title": subject,
                    "url": f"https://mail.google.com/mail/u/0/#all/{m.get('id') or ''}",
                    "when": _gmail_internal_date_to_iso(m.get("internal_date") or ""),
                })
    except Exception as e:
        logger.warning("gmail per-user 예외 user=%s: %s", user_email, e)
        errs["gmail"] = "exception"

    # 개인 Calendar — 본인이 organizer/creator 인 일정만.
    try:
        cal_time_min = time_min or _default_personal_calendar_past()
        cal_time_max = time_max or _default_personal_calendar_future()
        pcal_res = list_events(
            calendar_id="primary",
            time_min=cal_time_min,
            time_max=cal_time_max,
            q=keyword,
            credentials_source="user_oauth",
            user_email=user_email,
            max_results=30,
        )
        if not pcal_res.ok:
            errs["personal_calendar"] = pcal_res.error_kind or "unknown"
        else:
            user_needle = user_email.lower()
            for ev in pcal_res.events:
                title = (ev.get("summary") or "").strip()
                if _is_noise_title(title):
                    continue
                org = (ev.get("organizer_email") or "").lower()
                crt = (ev.get("creator_email") or "").lower()
                if user_needle not in (org, crt):
                    continue
                role = "organizer" if user_needle == org else "creator"
                out.append({
                    "source": "personal_calendar",
                    "role": role,
                    "email": user_email,
                    "name": "",
                    "title": title,
                    "url": "",
                    "when": (ev.get("start") or "").strip(),
                })
    except Exception as e:
        logger.warning("personal_calendar per-user 예외 user=%s: %s", user_email, e)
        errs["personal_calendar"] = "exception"

    return out, errs


def _default_personal_calendar_past() -> str:
    """개인 Calendar 시간 윈도우 미지정 시 폴백 (과거 5년)."""
    return (datetime.now(timezone.utc) - timedelta(days=_CAL_PAST_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_personal_calendar_future() -> str:
    """개인 Calendar 시간 윈도우 미지정 시 폴백 (미래 1년)."""
    return (datetime.now(timezone.utc) + timedelta(days=_CAL_FUTURE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gmail_internal_date_to_iso(internal_date: str) -> str:
    """Gmail ``internalDate`` (ms epoch 문자열) → ISO8601."""
    if not internal_date:
        return ""
    try:
        ts = int(internal_date) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""
