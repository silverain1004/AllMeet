"""정기회의 재배치 요청 → 구조화된 제약 명세(SeriesRebookSpec).

복잡한 자연어("오후 고정이 최선이지만 안 되면 가능한 시간대", "6명인 회의실도 괜찮으니 회의실
우선")는 정규식만으로는 우선순위·완화 조건을 담기 어렵다. 그래서

  1) 규칙으로 확실한 것(팀·회의실·요일·"N월부터"·"N명 회의실도 괜찮")을 먼저 뽑고
  2) LLM(JSON 고정 스키마)은 규칙이 못 잡은 칸만 채우며
  3) 모든 값을 결정적으로 검증·보정한다(모르는 팀/회의실·과거 날짜·업무시간 밖은 버림).

탐색·판정은 여기서 하지 않는다 — 명세는 사람(카드의 "이렇게 이해했어요")과 솔버 사이의 계약이다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from domains.schedule_management.compose_state import BUSINESS_HOUR_END, BUSINESS_HOUR_START
from domains.schedule_management.mentions import (
    _team_of_sender,
    find_mentions,
    resolve_team_id_from_text,
    teams_from_members,
)

logger = logging.getLogger(__name__)

WEEKDAYS_KO = ("월", "화", "수", "목", "금", "토", "일")

# 하루 구간 → 선호 시간창(시작~종료, 회의가 이 안에 끝나야 함)
_WINDOW_BY_DAYPART = {
    "오전": ("09:00", "12:00"),
    "점심": ("12:00", "14:00"),
    "오후": ("13:00", "18:00"),
    "저녁": ("17:00", BUSINESS_HOUR_END),
}

_TITLE_RE = re.compile(
    r"(주간\s*회의|주간\s*미팅|정기\s*회의|정기\s*미팅|월간\s*회의|격주\s*회의|스탠드업|데일리\s*스크럼|위클리)"
)
_SOURCE_WEEKDAY_RE = re.compile(r"(?:매주|격주)?\s*([월화수목금])요일")
_FROM_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:부터|이후)")
_FROM_MONTH_RE = re.compile(r"(\d{1,2})\s*월\s*(?:부터|이후|달부터)")
_NEXT_WEEK_FROM_RE = re.compile(r"다음\s*주\s*부터")
_UNTIL_MONTH_RE = re.compile(r"(\d{1,2})\s*월\s*(?:말|까지)")
_YEAR_END_RE = re.compile(r"(연말|올해\s*(?:말|끝))\s*까지")
_MIN_CAP_RE = re.compile(
    r"(\d{1,2})\s*(?:명|인)\s*(?:인|짜리|이하)?\s*(?:인)?\s*(?:회의실|룸|방|실)?\s*(?:이라도|도)\s*"
    r"(?:괜찮|가능|돼|되|ok|OK|상관)"
)
# "매주 목요일 고정으로 잡혀 있던" 처럼 기존 일정을 설명하는 '고정'은 요일 유지 요청이 아니다.
_KEEP_WEEKDAY_RE = re.compile(r"(요일\s*(?:은|는)\s*(?:그대로|고정|유지)|[월화수목금]요일\s*(?:만|에만)\s*(?:가능|돼|되|해))")
_STRICT_WINDOW_RE = re.compile(r"(오전|오후|점심|저녁)\s*(?:만|에만|시간대만)")


@dataclass
class SeriesRebookSpec:
    team_id: str = ""
    title_keyword: str = "주간회의"
    source_weekday: int | None = None  # 0=월
    source_room: str = ""  # display_name, 예: London
    effective_from: str = ""  # YYYY-MM-DD
    horizon_end: str = ""  # 기존 시리즈 종료일이 없을 때의 확인 한계
    preferred_window: tuple[str, str] | None = None
    allow_other_weekdays: bool = True
    allow_any_time: bool = True
    exclude_rooms: list[str] = field(default_factory=list)
    preferred_rooms: list[str] = field(default_factory=list)
    min_capacity: int | None = None
    region: str = ""
    notes: list[str] = field(default_factory=list)  # 카드에 그대로 보여줄 해석 메모

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("notes", None)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "SeriesRebookSpec":
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        spec = cls()
        for key, value in (data or {}).items():
            if not hasattr(spec, key):
                continue
            if key == "preferred_window" and value:
                value = (str(value[0]), str(value[1]))
            setattr(spec, key, value)
        return spec

    def time_tiers(self, source_weekday: int | None) -> list[tuple[str, tuple[str, str]]]:
        """우선순위 순 (요일 범위, 시간창). 요일 범위 'same'|'other'."""
        full = (BUSINESS_HOUR_START, BUSINESS_HOUR_END)
        windows: list[tuple[str, str]] = []
        if self.preferred_window:
            windows.append(self.preferred_window)
        if self.allow_any_time or not windows:
            if full not in windows:
                windows.append(full)
        scopes = ["same"] if source_weekday is not None else ["other"]
        if source_weekday is not None and self.allow_other_weekdays:
            scopes.append("other")
        return [(scope, win) for scope in scopes for win in windows]

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.effective_from:
            lines.append(f"적용: {_md(self.effective_from)}부터" + (f" ~ {_md(self.horizon_end)}" if self.horizon_end else ""))
        prefs: list[str] = []
        wd = f"{WEEKDAYS_KO[self.source_weekday]}요일" if self.source_weekday is not None else "같은 요일"
        if self.preferred_window:
            prefs.append(f"{wd} {self.preferred_window[0]}~{self.preferred_window[1]}")
        if self.allow_any_time:
            prefs.append(f"{wd} 아무 시간")
        if self.allow_other_weekdays:
            prefs.append("다른 평일")
        if prefs:
            lines.append("우선순위: " + " → ".join(prefs))
        room_bits: list[str] = []
        if self.exclude_rooms:
            room_bits.append(", ".join(self.exclude_rooms) + " 제외")
        if self.preferred_rooms:
            room_bits.append(", ".join(self.preferred_rooms) + " 우선")
        if self.min_capacity:
            room_bits.append(f"{self.min_capacity}인 이상이면 인원보다 작아도 OK")
        if room_bits:
            lines.append("회의실: " + " · ".join(room_bits))
        lines.append("참석자 일정이 겹치면 경고만 표시(회의실 확보 우선)")
        return lines + list(self.notes)


def _md(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return iso
    return f"{d.month}/{d.day}({WEEKDAYS_KO[d.weekday()]})"


def _year_for_month(month: int, today: date) -> int:
    # 지난 달을 말하면(예: 11월에 "3월부터") 내년으로 본다. 이번 달은 올해.
    return today.year if month >= today.month else today.year + 1


def _valid_window(raw: Any) -> tuple[str, str] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        s = datetime.strptime(str(raw[0]).strip(), "%H:%M")
        e = datetime.strptime(str(raw[1]).strip(), "%H:%M")
    except ValueError:
        return None
    lo = datetime.strptime(BUSINESS_HOUR_START, "%H:%M")
    hi = datetime.strptime(BUSINESS_HOUR_END, "%H:%M")
    s, e = max(s, lo), min(e, hi)
    if e <= s:
        return None
    return s.strftime("%H:%M"), e.strftime("%H:%M")


def _valid_date(raw: Any, today: date) -> str:
    try:
        d = datetime.strptime(str(raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return ""
    return d.isoformat() if d >= today else ""


def _rule_extract(
    text: str,
    *,
    members: list[dict[str, Any]],
    sender_email: str,
    today: date,
) -> tuple[SeriesRebookSpec, set[str]]:
    spec = SeriesRebookSpec()
    explicit: set[str] = set()

    teams = teams_from_members(members)
    tid = resolve_team_id_from_text(text, teams)
    if not tid and re.search(r"(우리|저희)\s*팀", text):
        tid = _team_of_sender(sender_email, members) or None
    if tid:
        spec.team_id = tid
        explicit.add("team_id")

    m = _TITLE_RE.search(text)
    if m:
        spec.title_keyword = re.sub(r"\s+", "", m.group(1))
        explicit.add("title_keyword")

    m = _SOURCE_WEEKDAY_RE.search(text)
    if m:
        spec.source_weekday = WEEKDAYS_KO.index(m.group(1))
        explicit.add("source_weekday")

    rooms = [mm.value for mm in find_mentions(text, members, sender_email=sender_email) if mm.kind == "room"]
    rooms = list(dict.fromkeys(rooms))
    if rooms:
        # 첫 언급 = 지금 쓰는(못 쓰게 된) 회의실, 나머지 = 옮겨 갈 후보로 본다.
        spec.source_room = rooms[0]
        spec.exclude_rooms = [rooms[0]]
        spec.preferred_rooms = rooms[1:]
        explicit.update({"source_room", "exclude_rooms"})

    m = _FROM_MONTH_DAY_RE.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            spec.effective_from = date(_year_for_month(month, today), month, day).isoformat()
            explicit.add("effective_from")
        except ValueError:
            pass
    if "effective_from" not in explicit:
        m = _FROM_MONTH_RE.search(text)
        if m and 1 <= int(m.group(1)) <= 12:
            month = int(m.group(1))
            spec.effective_from = date(_year_for_month(month, today), month, 1).isoformat()
            explicit.add("effective_from")
        elif _NEXT_WEEK_FROM_RE.search(text):
            spec.effective_from = (today + timedelta(days=7 - today.weekday())).isoformat()
            explicit.add("effective_from")

    m = _UNTIL_MONTH_RE.search(text)
    if m and 1 <= int(m.group(1)) <= 12:
        month = int(m.group(1))
        year = _year_for_month(month, today)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        spec.horizon_end = (nxt - timedelta(days=1)).isoformat()
        explicit.add("horizon_end")
    elif _YEAR_END_RE.search(text):
        spec.horizon_end = date(today.year, 12, 31).isoformat()
        explicit.add("horizon_end")

    for part, window in _WINDOW_BY_DAYPART.items():
        if part in text:
            spec.preferred_window = window
            explicit.add("preferred_window")
            break
    if _STRICT_WINDOW_RE.search(text):
        spec.allow_any_time = False
        explicit.add("allow_any_time")
    if _KEEP_WEEKDAY_RE.search(text):
        spec.allow_other_weekdays = False
        explicit.add("allow_other_weekdays")

    m = _MIN_CAP_RE.search(text)
    if m:
        spec.min_capacity = int(m.group(1))
        explicit.add("min_capacity")

    return spec, explicit


_LLM_PROMPT = """너는 회의실 예약 봇의 요청 해석기다. 사용자는 이미 캘린더에 있는 '반복(정기) 회의'를
다른 회의실/시간으로 옮기고 싶어 한다. 아래 요청을 읽고 JSON 하나만 출력하라. 모르면 null.

오늘: {today} ({weekday})
알려진 팀ID: {teams}
알려진 회의실: {rooms}

스키마:
{{
  "team_id": 알려진 팀ID 중 하나 | null,
  "title_keyword": 반복 회의 제목 핵심어(예: "주간회의") | null,
  "source_weekday": 기존 회의 요일 0=월..4=금 | null,
  "source_room": 지금 쓰는(못 쓰게 된) 회의실, 알려진 회의실 이름 그대로 | null,
  "preferred_rooms": 옮겨 가고 싶은 회의실 목록 | [],
  "effective_from": 변경 시작일 YYYY-MM-DD | null,
  "horizon_end": 변경 적용 마지막 날 YYYY-MM-DD | null,
  "preferred_window": 가장 원하는 시간창 ["HH:MM","HH:MM"] | null,
  "allow_any_time": 선호 시간창이 안 되면 다른 시간도 괜찮은지 true/false | null,
  "allow_other_weekdays": 기존 요일이 안 되면 다른 요일도 괜찮은지 true/false | null,
  "min_capacity": 인원보다 작아도 허용하는 회의실 최소 정원(숫자) | null
}}

요청: {text}
"""


def _llm_extract(
    text: str,
    *,
    today: date,
    teams: list[str],
    rooms: list[str],
    llm: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if llm is None:
        from domains.agent._llm import generate_json

        llm = generate_json
    prompt = _LLM_PROMPT.format(
        today=today.isoformat(),
        weekday=WEEKDAYS_KO[today.weekday()],
        teams=", ".join(teams) or "(없음)",
        rooms=", ".join(rooms) or "(없음)",
        text=text,
    )
    try:
        out = llm(prompt)
    except Exception:  # noqa: BLE001 - LLM 실패는 규칙 결과로 degrade
        logger.warning("series spec LLM 추출 실패", exc_info=True)
        return {}
    return out if isinstance(out, dict) else {}


def extract_series_spec(
    text: str,
    *,
    members: list[dict[str, Any]],
    sender_email: str = "",
    today: date | None = None,
    room_names: list[str] | None = None,
    llm: Callable[[str], dict[str, Any]] | None = None,
    use_llm: bool = True,
) -> SeriesRebookSpec:
    today = today or datetime.now().date()
    spec, explicit = _rule_extract(text, members=members, sender_email=sender_email, today=today)
    team_ids = [t["id"] for t in teams_from_members(members)]
    known_rooms = list(dict.fromkeys(room_names or []))
    room_by_lower = {r.lower(): r for r in known_rooms}

    if use_llm:
        raw = _llm_extract(text, today=today, teams=team_ids, rooms=known_rooms, llm=llm)
        # 규칙이 명시적으로 잡은 칸은 덮어쓰지 않는다 — LLM은 빈칸 채우기 + 검증 통과분만.
        if "team_id" not in explicit and str(raw.get("team_id") or "") in team_ids:
            spec.team_id = str(raw["team_id"])
        if "title_keyword" not in explicit and str(raw.get("title_keyword") or "").strip():
            spec.title_keyword = re.sub(r"\s+", "", str(raw["title_keyword"]))[:20]
        wd = raw.get("source_weekday")
        if "source_weekday" not in explicit and isinstance(wd, int) and 0 <= wd <= 4:
            spec.source_weekday = wd
        src = room_by_lower.get(str(raw.get("source_room") or "").strip().lower())
        if "source_room" not in explicit and src:
            spec.source_room = src
            spec.exclude_rooms = [src]
        if not spec.preferred_rooms:
            spec.preferred_rooms = [
                room_by_lower[r.strip().lower()]
                for r in (raw.get("preferred_rooms") or [])
                if isinstance(r, str) and r.strip().lower() in room_by_lower
                and room_by_lower[r.strip().lower()] not in spec.exclude_rooms
            ]
        for key in ("effective_from", "horizon_end"):
            if key not in explicit:
                val = _valid_date(raw.get(key), today)
                if val:
                    setattr(spec, key, val)
        if "preferred_window" not in explicit:
            win = _valid_window(raw.get("preferred_window"))
            if win:
                spec.preferred_window = win
        for key in ("allow_any_time", "allow_other_weekdays"):
            if key not in explicit and isinstance(raw.get(key), bool):
                setattr(spec, key, raw[key])
        cap = raw.get("min_capacity")
        if "min_capacity" not in explicit and isinstance(cap, int) and 1 <= cap <= 50:
            spec.min_capacity = cap

    # 기본값·보정
    if not spec.effective_from or spec.effective_from < today.isoformat():
        spec.effective_from = today.isoformat()
    if spec.horizon_end and spec.horizon_end < spec.effective_from:
        spec.horizon_end = ""
    if not spec.horizon_end:
        # 기존 시리즈에 종료일이 없을 때의 확인 한계(사용자 결정: 연말). 연말이 너무 가까우면 8주는 본다.
        year_end = date(int(spec.effective_from[:4]), 12, 31)
        floor = datetime.strptime(spec.effective_from, "%Y-%m-%d").date() + timedelta(weeks=8)
        spec.horizon_end = max(year_end, floor).isoformat()
    if spec.source_room and spec.source_room not in spec.exclude_rooms:
        spec.exclude_rooms.insert(0, spec.source_room)
    return spec
