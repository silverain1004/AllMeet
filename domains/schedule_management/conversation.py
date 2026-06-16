"""자연어 발화 → compose 초기 state 추출 (규칙 기반)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from domains.schedule_management.compose_state import (
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
    empty_compose_state,
)

KST = timezone(timedelta(hours=9))

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _extract_date(text: str, now: datetime) -> str:
    t = text
    if "오늘" in t:
        return now.strftime("%Y-%m-%d")
    if "내일" in t:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "모레" in t:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        year = now.year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    weekdays = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    for name, wd in weekdays.items():
        if f"다음 주 {name}" in t or f"다음주 {name}" in t:
            days_ahead = (wd - now.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            return target.strftime("%Y-%m-%d")
        if f"이번 주 {name}" in t or f"이번주 {name}" in t:
            days_ahead = (wd - now.weekday()) % 7
            target = now + timedelta(days=days_ahead)
            return target.strftime("%Y-%m-%d")
    return ""


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _format_hhmm(h: int, minute: int) -> str:
    return f"{h:02d}:{minute:02d}"


def _is_within_business_hours(hhmm: str) -> bool:
    t = _hhmm_to_minutes(hhmm)
    return _hhmm_to_minutes(BUSINESS_HOUR_START) <= t <= _hhmm_to_minutes(BUSINESS_HOUR_END)


def _ambiguous_hour_candidates(h: int, minute: int) -> list[str]:
    if not (0 <= h < 24):
        return []
    candidates = [_format_hhmm(h, minute)]
    if 1 <= h <= 11:
        candidates.append(_format_hhmm(h + 12, minute))
    return candidates


def _pick_business_hours_time(h: int, minute: int) -> str:
    matches = [c for c in _ambiguous_hour_candidates(h, minute) if _is_within_business_hours(c)]
    if len(matches) == 1:
        return matches[0]
    return ""


def _extract_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"오전\s*(\d{1,2})\s*시\s*반", text)
    if m:
        h = int(m.group(1))
        return f"{h:02d}:30"
    m = re.search(r"오후\s*(\d{1,2})\s*시\s*반", text)
    if m:
        h = int(m.group(1))
        if h < 12:
            h += 12
        return f"{h:02d}:30"
    m = re.search(r"(\d{1,2})\s*시\s*반", text)
    if m:
        h = int(m.group(1))
        if 0 <= h < 24:
            return _pick_business_hours_time(h, 30)
    m = re.search(r"오전\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        return f"{h:02d}:{minute:02d}"
    m = re.search(r"오후\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        if h < 12:
            h += 12
        minute = int(m.group(2) or 0)
        return f"{h:02d}:{minute:02d}"
    if "오후" in text and not re.search(r"오후\s*\d", text):
        return "14:00"
    m = re.search(r"(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        if 0 <= h < 24:
            return _pick_business_hours_time(h, minute)
    return ""


def _extract_duration_minutes(text: str) -> int | None:
    m = re.search(r"(\d+)\s*분", text)
    if m:
        return max(int(m.group(1)), 10)
    if "30분" in text or "삼십분" in text:
        return 30
    if "1시간" in text or "한시간" in text:
        return 60
    if "2시간" in text:
        return 120
    return None


def _extract_title_quoted(text: str) -> str:
    m = re.search(r"[\"'「]([^\"'」]+)[\"'」]", text)
    if m:
        return m.group(1).strip()
    return ""


def _match_members_by_name(text: str, members: list[dict[str, Any]]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        name = str(m.get("name") or "").strip()
        email = str(m.get("email") or "").strip()
        if not name:
            continue
        if name in text and email and email not in seen:
            found.append({"name": name, "email": email})
            seen.add(email)
        for nick in m.get("nickname") or []:
            nick_s = str(nick).strip()
            if nick_s and nick_s in text and email and email not in seen:
                found.append({"name": name, "email": email})
                seen.add(email)
    return found


def extract_compose_state(
    user_message: str,
    *,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    state = empty_compose_state()
    text = (user_message or "").strip()
    if not text:
        return state

    now = _now_kst()
    date = _extract_date(text, now)
    if date:
        state["meeting_date"] = date
    time_str = _extract_time(text)
    if time_str:
        state["meeting_time"] = time_str

    for email in _EMAIL_RE.findall(text):
        state["attendees"].append({"name": "", "email": email})

    for person in _match_members_by_name(text, members):
        if not any(a.get("email") == person["email"] for a in state["attendees"]):
            state["attendees"].append(person)

    meet_kw = ("meet", "구글미트", "화상", "원격", "줌", "zoom")
    if any(k in text.lower() for k in meet_kw):
        state["auto_meet"] = True

    equip_kw: list[str] = []
    for kw in ("프로젝터", "화이트보드", "화상", "모니터", "TV"):
        if kw in text:
            equip_kw.append(kw)
    state["equipment_keywords"] = equip_kw

    if "대형" in text or "대회의" in text:
        state["room_name_keyword"] = "대"
    if "소회의" in text:
        state["room_name_keyword"] = "소"

    dur = _extract_duration_minutes(text)
    if dur:
        state["duration_minutes"] = dur

    quoted = _extract_title_quoted(text)
    if quoted:
        state["title"] = quoted
    elif "회의" in text and not state.get("title"):
        state["title"] = "회의"

    return state


def extract_compose_state_with_llm_fallback(
    user_message: str,
    *,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """규칙 기반 추출 후, 비어 있는 핵심 필드가 있으면 LLM 보조(선택)."""
    state = extract_compose_state(user_message, members=members)
    if os.environ.get("SCHEDULE_LLM_PREFILL", "").lower() not in ("1", "true", "yes"):
        return state
    if state.get("meeting_date") and state.get("meeting_time") and state.get("title"):
        return state
    try:
        from domains.schedule_management.conversation_llm import llm_extract_compose_state

        llm_state = llm_extract_compose_state(user_message, members=members)
        for key in ("meeting_date", "meeting_time", "title", "duration_minutes"):
            if llm_state.get(key) and not state.get(key):
                state[key] = llm_state[key]
        if llm_state.get("attendees"):
            existing = {a.get("email") for a in state.get("attendees") or []}
            for a in llm_state["attendees"]:
                if a.get("email") not in existing:
                    state.setdefault("attendees", []).append(a)
        if llm_state.get("auto_meet"):
            state["auto_meet"] = True
    except Exception:
        pass
    return state
