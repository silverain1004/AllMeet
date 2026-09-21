"""자연어 발화 → compose 초기 state 추출 (규칙 기반, 순수/오프라인)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from domains.schedule_management.compose_state import (
    BUSINESS_HOUR_END,
    BUSINESS_HOUR_START,
    empty_compose_state,
    pick_attendee_count_option,
)
from domains.schedule_management.mentions import (
    apply_negation,
    find_mentions,
    resolve_attendees,
    resolve_room_and_region,
)

KST = timezone(timedelta(hours=9))

_ATTENDEE_COUNT_RE = re.compile(r"(\d+)\s*명")
_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

# "3시부터 5시까지", "오후 3시~5시", "15:00-17:00" — 시작·종료 두 시각과 연결어가 모두 있을 때만 범위로 본다
# ("3시까지 잡아줘"는 데드라인이라 종료시각으로 오해하지 않는다).
_RANGE_RE = re.compile(
    r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?\s*(?:부터|에서|~|-|–)\s*"
    r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?(?:\s*까지)?"
)
_RANGE_HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(?:~|-|–|부터)\s*(\d{1,2}):(\d{2})")

# 숫자 시각이 전혀 없을 때만 쓰는 하루 구간 기본값. '오후 늦게'는 맨 '오후'(14:00)보다 먼저 봐야 한다.
_DAYPART_DEFAULTS: tuple[tuple[str, str], ...] = (
    ("오후 늦게", "16:00"),
    ("오후늦게", "16:00"),
    ("오후 일찍", "13:00"),
    ("점심 이후", "13:00"),
    ("점심 먹고", "13:00"),
    ("점심먹고", "13:00"),
    ("점심 후", "13:00"),
    ("퇴근 전", "17:00"),
    ("퇴근전", "17:00"),
    ("오전 중", "10:00"),
    ("오전중", "10:00"),
    ("아침", "10:00"),
    ("정오", "12:00"),
)

_FIND_SLOT_RE = re.compile(
    r"다\s*되는\s*시간|모두\s*가능한\s*시간|아무\s*때나|시간\s*맞춰서|되는\s*시간에|가능한\s*시간에"
)


def _now_kst() -> datetime:
    return datetime.now(KST)


def _extract_date(text: str, now: datetime) -> str:
    t = text
    # '내일모레'에는 '내일'이 들어 있어 먼저 본다.
    if "모레" in t:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")
    if "오늘" in t:
        return now.strftime("%Y-%m-%d")
    if "내일" in t:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
    if m:
        return f"{now.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(?<![\d:])(\d{1,2})/(\d{1,2})(?![\d:])", t)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31:
        return f"{now.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    days_to_next_monday = 7 - now.weekday()
    for name, wd in _WEEKDAYS.items():
        # 다다음주 → 다음주 순서: '다다음주 화'에는 '다음주 화'가 들어 있다.
        if f"다다음 주 {name}" in t or f"다다음주 {name}" in t:
            return (now + timedelta(days=days_to_next_monday + 7 + wd)).strftime("%Y-%m-%d")
        if f"다음 주 {name}" in t or f"다음주 {name}" in t:
            # 다음 달력 주(월~일)의 해당 요일 — 예전 공식은 오늘(월) 기준 '다음주 화'를 내일로 계산했다.
            return (now + timedelta(days=days_to_next_monday + wd)).strftime("%Y-%m-%d")
        if f"이번 주 {name}" in t or f"이번주 {name}" in t:
            days_ahead = (wd - now.weekday()) % 7
            return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    m = re.search(r"([월화수목금토일])요일", t)
    if m:
        wd = _WEEKDAYS[m.group(1)]
        days_ahead = (wd - now.weekday()) % 7 or 7
        return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
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


def _resolve_clock(h: int, minute: int, ampm: str) -> str:
    if not (0 <= h < 24 and 0 <= minute < 60):
        return ""
    if ampm == "오후":
        return _format_hhmm(h + 12 if h < 12 else h, minute)
    if ampm == "오전":
        return _format_hhmm(h, minute)
    return _pick_business_hours_time(h, minute)


def _extract_time_range(text: str) -> tuple[str, str] | None:
    """(start, end) — 범위 표현이 있을 때만."""
    m = _RANGE_HHMM_RE.search(text)
    if m:
        start = _format_hhmm(int(m.group(1)), int(m.group(2)))
        end = _format_hhmm(int(m.group(3)), int(m.group(4)))
        return (start, end) if _hhmm_to_minutes(end) > _hhmm_to_minutes(start) else None
    m = _RANGE_RE.search(text)
    if not m:
        return None
    start_ampm = m.group(1) or ""
    end_ampm = m.group(4) or start_ampm
    start = _resolve_clock(int(m.group(2)), int(m.group(3) or 0), start_ampm)
    if not start:
        return None
    end_h, end_min = int(m.group(5)), int(m.group(6) or 0)
    end = _resolve_clock(end_h, end_min, end_ampm) if end_ampm else ""
    if not end:
        # 오전/오후 없이 모호하면 시작보다 뒤인 가장 이른 후보.
        later = [
            c for c in _ambiguous_hour_candidates(end_h, end_min)
            if _hhmm_to_minutes(c) > _hhmm_to_minutes(start)
        ]
        end = later[0] if later else ""
    if not end or _hhmm_to_minutes(end) <= _hhmm_to_minutes(start):
        return None
    return start, end


def _extract_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"오전\s*(\d{1,2})\s*시\s*반", text)
    if m:
        return f"{int(m.group(1)):02d}:30"
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
        return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"
    m = re.search(r"오후\s*(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        if h < 12:
            h += 12
        return f"{h:02d}:{int(m.group(2) or 0):02d}"
    m = re.search(r"(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분)?", text)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        if 0 <= h < 24:
            picked = _pick_business_hours_time(h, minute)
            if picked:
                return picked
    for phrase, hhmm in _DAYPART_DEFAULTS:
        if phrase in text:
            return hhmm
    if "오후" in text and not re.search(r"오후\s*\d", text):
        return "14:00"
    return ""


def _extract_duration_minutes(text: str) -> int | None:
    m = re.search(r"(\d+)\s*시간\s*반", text)
    if m:
        return int(m.group(1)) * 60 + 30
    if re.search(r"한\s*시간\s*반", text):
        return 90
    m = re.search(r"(\d+)\s*시간", text)
    if m:
        return int(m.group(1)) * 60
    if "한시간" in text or "한 시간" in text:
        return 60
    # '3시 30분'의 30분은 시각이지 소요시간이 아니다 — 시각 표현을 지운 뒤 분을 찾는다.
    stripped = re.sub(r"\d{1,2}\s*시\s*\d{1,2}\s*분", " ", text)
    stripped = re.sub(r"\d{1,2}:\d{2}", " ", stripped)
    m = re.search(r"(\d+)\s*분", stripped)
    if m:
        return max(int(m.group(1)), 10)
    if "삼십분" in text:
        return 30
    return None


def _extract_title_quoted(text: str) -> str:
    m = re.search(r"[\"'「]([^\"'」]+)[\"'」]", text)
    if m:
        return m.group(1).strip()
    return ""


def extract_compose_state(
    user_message: str,
    *,
    members: list[dict[str, Any]],
    sender_email: str = "",
) -> dict[str, Any]:
    state = empty_compose_state()
    text = (user_message or "").strip()
    if not text:
        return state

    now = _now_kst()
    date = _extract_date(text, now)
    if date:
        state["meeting_date"] = date

    time_range = _extract_time_range(text)
    if time_range:
        state["meeting_time"], state["meeting_end_time"] = time_range
        state["duration_mode"] = "custom"
        state["duration_minutes"] = _hhmm_to_minutes(time_range[1]) - _hhmm_to_minutes(time_range[0])
    else:
        time_str = _extract_time(text)
        if time_str:
            state["meeting_time"] = time_str

    mentions = find_mentions(text, members, sender_email=sender_email)
    apply_negation(text, mentions)
    state["attendees"] = resolve_attendees(text, mentions, members)

    m = _ATTENDEE_COUNT_RE.search(text)
    if m and int(m.group(1)) > 0:
        headcount = int(m.group(1))
        state["attendee_headcount"] = headcount
        state["attendee_count"] = pick_attendee_count_option(headcount)

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
    room_name, _room_office, region = resolve_room_and_region(text, mentions)
    if room_name:
        # 특정 회의실 이름은 "대/소" 크기 힌트보다 정확한 신호라 덮어쓴다.
        state["room_name_keyword"] = room_name
    if region:
        state["room_region"] = region

    if _FIND_SLOT_RE.search(text):
        state["find_slot"] = True

    if not time_range:
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
    sender_email: str = "",
) -> dict[str, Any]:
    """규칙 기반 추출 후, 비어 있는 핵심 필드가 있으면 LLM 보조(선택)."""
    state = extract_compose_state(user_message, members=members, sender_email=sender_email)
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
