"""기존 일정 수정·삭제 — 요청 해석(EditRequest) · 대상 찾기 · 실행.

해석은 정기회의 재배치와 같은 원칙: 규칙으로 확실한 칸 → 빈칸만 LLM → 검증. 카드에서 사용자가
값을 고칠 수 있으므로 여기서는 '좋은 기본값'을 채우는 데 집중한다.

  "내일 3시 ERP2 회의 4시로 옮겨줘"     → 대상(내일 15:00, ERP2) / 변경(16:00)
  "주간회의 10/15만 취소해줘"            → 삭제, 이 회차만
  "주간회의 11월부터 없애줘"             → 삭제, 이후 전체
  "목요일 주간회의 회의실 Paris로 바꿔줘" → 변경(회의실), 범위는 카드에서 선택

반복 일정 범위(scope): this(이 회차) | following(이 회차부터 이후) | all(반복 전체).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from domains.schedule_management.calendar_client import (
    KST,
    CalendarResult,
    _event_row,
    create_event,
    delete_event,
    get_event,
    list_events_all,
    patch_event,
)
from domains.schedule_management.conversation import (
    _extract_date,
    _extract_duration_minutes,
    _extract_time,
    _extract_time_range,
    _extract_title_quoted,
)
from domains.schedule_management.mentions import (
    find_mentions,
    resolve_team_id_from_text,
    teams_from_members,
)
from domains.schedule_management.series_spec import _TITLE_RE, WEEKDAYS_KO

logger = logging.getLogger(__name__)

_DELETE_RE = re.compile(r"(취소|삭제|지워|지우|없애|없에|날려)")
_CHANGE_VERB = r"(?:옮|변경|바꿔|바꾸|미뤄|미루|당겨|당기|연기|이동|늘려|늘리|줄여|줄이|수정)"
_TO_RE = re.compile(r"(\S+?)(?:으로|로)\s*" + _CHANGE_VERB)
_TIME_TOKEN_RE = re.compile(
    r"^(?:오전|오후|오늘|내일|모레|내일모레|다음주|다다음주|이번주|[월화수목금]요일|\d{1,2}(?:시|분|월|일)\S*|"
    r"\d{1,2}[:/]\d{1,2}\S*|\d{4}-\d{1,2}-\d{1,2}|반|\d+시간\S*|한|시간|\d+분)$"
)
_SCOPE_THIS_RE = re.compile(
    r"(이번\s*(?:회차|주)?\s*만|그\s*날\s*만|당일\s*만|이\s*날\s*만|이\s*회차\s*만|\d\s*일\s*만|\d/\d{1,2}\s*만|"
    r"(?:요일|내일|오늘|모레)\s*만|만\s*" + r"(?:취소|삭제|옮|변경|바꿔))"
)
_SCOPE_FOLLOWING_RE = re.compile(r"(부터|이후|앞으로)")
_SCOPE_ALL_RE = re.compile(r"(전체|전부|모두|모든\s*회차|반복\s*(?:일정\s*)?(?:전체|자체)|시리즈)")
_FROM_MONTH_RE = re.compile(r"(\d{1,2})\s*월\s*(?:부터|이후)")
_KEYWORD_NEAR_RE = re.compile(r"(\S+)\s*(?:회의|미팅|면담|약속|일정|세미나)")
_STOP_WORDS = {"오늘", "내일", "모레", "이번", "다음", "다음주", "이번주", "그", "이", "내", "우리", "기존", "잡힌", "있는"}


@dataclass
class EditRequest:
    operation: str = "modify"  # modify | delete
    keyword: str = ""
    target_date: str = ""
    target_time: str = ""
    target_room: str = ""
    scope: str = ""  # this | following | all | ""(미정)
    new_date: str = ""
    new_start: str = ""
    new_end: str = ""
    duration_minutes: int | None = None
    new_room: str = ""  # display_name
    new_title: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "EditRequest":
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        req = cls()
        for k, v in (data or {}).items():
            if hasattr(req, k):
                setattr(req, k, v)
        return req

    @property
    def has_changes(self) -> bool:
        return bool(self.new_date or self.new_start or self.new_end or self.duration_minutes
                    or self.new_room or self.new_title)


# ---------- 해석 ----------
def _split_change(text: str) -> tuple[str, str]:
    """(대상 구간, 변경 구간). '4시로 옮겨' 앞의 날짜·시각 토큰을 변경 구간으로 본다."""
    m = None
    for m in _TO_RE.finditer(text):
        pass
    if m is None:
        return text, ""
    head_end = m.end(1)
    before = text[: m.start(1)].rstrip()
    tokens = before.split()
    taken: list[str] = []
    while tokens and _TIME_TOKEN_RE.match(tokens[-1]):
        taken.insert(0, tokens.pop())
    change = " ".join([*taken, text[m.start(1) : head_end]]).strip()
    target = " ".join(tokens)
    return target, change


def _room_in(text: str, members: list[dict[str, Any]]) -> str:
    rooms = [m.value for m in find_mentions(text, members) if m.kind == "room"]
    return rooms[-1] if rooms else ""


def _keyword(text: str, members: list[dict[str, Any]]) -> str:
    quoted = _extract_title_quoted(text)
    if quoted:
        return quoted
    tid = resolve_team_id_from_text(text, teams_from_members(members))
    m = _TITLE_RE.search(text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    if tid:
        return tid
    for m in _KEYWORD_NEAR_RE.finditer(text):
        word = re.sub(r"(의|에|은|는|이|가)$", "", m.group(1))
        if word and word not in _STOP_WORDS and not _TIME_TOKEN_RE.match(word):
            return word
    return ""


def _rule_extract(text: str, *, members: list[dict[str, Any]], now: datetime) -> tuple[EditRequest, set[str]]:
    req = EditRequest()
    explicit: set[str] = set()
    if _DELETE_RE.search(text) and not _TO_RE.search(text):
        req.operation = "delete"
        target, change = text, ""
    else:
        target, change = _split_change(text)
    explicit.add("operation")

    if _SCOPE_THIS_RE.search(text):
        req.scope = "this"
    elif _SCOPE_FOLLOWING_RE.search(text):
        req.scope = "following"
    elif _SCOPE_ALL_RE.search(text):
        req.scope = "all"
    if req.scope:
        explicit.add("scope")

    req.target_date = _extract_date(target, now)
    if not req.target_date and req.scope == "following":
        m = _FROM_MONTH_RE.search(target)
        if m and 1 <= int(m.group(1)) <= 12:
            month = int(m.group(1))
            year = now.year if month >= now.month else now.year + 1
            req.target_date = date(year, month, 1).isoformat()
    req.target_time = _extract_time(target) if re.search(r"\d", target) else ""
    req.target_room = _room_in(target, members)
    req.keyword = _keyword(target, members)
    for k in ("target_date", "target_time", "keyword"):
        if getattr(req, k):
            explicit.add(k)

    if change:
        req.new_date = _extract_date(change, now)
        rng = _extract_time_range(change)
        if rng:
            req.new_start, req.new_end = rng
        elif re.search(r"\d\s*시(?!간)|\d:\d|오전|오후", change):
            req.new_start = _extract_time(change)
        if re.search(r"시간|분", change) and not req.new_start:
            req.duration_minutes = _extract_duration_minutes(change)
        req.new_room = _room_in(change, members)
        if _extract_title_quoted(change):
            req.new_title = _extract_title_quoted(change)
    for k in ("new_date", "new_start", "new_room", "duration_minutes"):
        if getattr(req, k):
            explicit.add(k)
    return req, explicit


_LLM_PROMPT = """너는 캘린더 봇의 요청 해석기다. 사용자는 이미 있는 일정을 수정하거나 삭제하려 한다.
JSON 하나만 출력. 모르면 null. 날짜는 YYYY-MM-DD, 시각은 HH:MM(24시간).
오늘: {today} ({weekday}) / 알려진 회의실: {rooms}
{{
 "operation": "modify"|"delete",
 "keyword": 찾을 일정 제목 핵심어 | null,
 "target_date": 바꿀 대상 일정의 날짜 | null,
 "target_time": 대상 일정의 시작 시각 | null,
 "scope": 반복 일정일 때 "this"(이 회차만)|"following"(이후 전체)|"all"(반복 전체) | null,
 "new_date": 옮길 날짜 | null, "new_start": 새 시작 시각 | null, "new_end": 새 종료 시각 | null,
 "duration_minutes": 새 길이(분) | null, "new_room": 새 회의실(알려진 이름 그대로) | null,
 "new_title": 새 제목 | null
}}
요청: {text}
"""


def _valid_hhmm(raw: Any) -> str:
    try:
        return datetime.strptime(str(raw or "").strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def extract_edit_request(
    text: str,
    *,
    members: list[dict[str, Any]],
    now: datetime | None = None,
    room_names: list[str] | None = None,
    llm: Callable[[str], dict[str, Any]] | None = None,
    use_llm: bool = True,
) -> EditRequest:
    now = now or datetime.now(KST)
    req, explicit = _rule_extract(text, members=members, now=now)
    # 규칙으로 대상·변경이 충분히 잡히면 LLM 왕복을 생략한다(응답 속도).
    gaps = not (req.keyword or req.target_date) or (req.operation == "modify" and not req.has_changes)
    if use_llm and gaps:
        if llm is None:
            from domains.agent._llm import generate_json

            llm = generate_json
        rooms = list(dict.fromkeys(room_names or []))
        by_lower = {r.lower(): r for r in rooms}
        try:
            raw = llm(_LLM_PROMPT.format(
                today=now.date().isoformat(), weekday=WEEKDAYS_KO[now.weekday()],
                rooms=", ".join(rooms) or "(없음)", text=text,
            )) or {}
        except Exception:  # noqa: BLE001
            logger.warning("edit request LLM 추출 실패", exc_info=True)
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        today = now.date().isoformat()

        def _date(v: Any) -> str:
            try:
                d = datetime.strptime(str(v or ""), "%Y-%m-%d").date().isoformat()
            except ValueError:
                return ""
            return d if d >= today else ""

        if "keyword" not in explicit and str(raw.get("keyword") or "").strip():
            req.keyword = str(raw["keyword"]).strip()[:30]
        for k in ("target_date", "new_date"):
            if k not in explicit and _date(raw.get(k)):
                setattr(req, k, _date(raw.get(k)))
        for k in ("target_time", "new_start", "new_end"):
            if k not in explicit and _valid_hhmm(raw.get(k)):
                setattr(req, k, _valid_hhmm(raw.get(k)))
        if "scope" not in explicit and raw.get("scope") in ("this", "following", "all"):
            req.scope = raw["scope"]
        if "operation" in raw and raw.get("operation") in ("modify", "delete") and not _TO_RE.search(text):
            req.operation = raw["operation"]
        if "new_room" not in explicit and str(raw.get("new_room") or "").lower() in by_lower:
            req.new_room = by_lower[str(raw["new_room"]).lower()]
        dm = raw.get("duration_minutes")
        if "duration_minutes" not in explicit and isinstance(dm, int) and 10 <= dm <= 600:
            req.duration_minutes = dm
        if str(raw.get("new_title") or "").strip() and req.operation == "modify":
            req.new_title = str(raw["new_title"]).strip()[:100]
    return req


# ---------- 대상 찾기 ----------
def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _parse(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def find_target_events(
    req: EditRequest,
    *,
    access_token: str,
    rooms: list[dict[str, Any]],
    now: datetime | None = None,
    calendar_id: str = "primary",
    max_n: int = 5,
) -> list[dict[str, Any]]:
    now = now or datetime.now(KST)
    if req.target_date:
        d0 = datetime.strptime(req.target_date, "%Y-%m-%d").replace(tzinfo=KST)
        d1 = d0 + timedelta(days=1 if req.scope != "following" or req.target_time else 31)
        d0 = max(d0, now - timedelta(hours=1))
    else:
        d0, d1 = now, now + timedelta(days=14)
    result = list_events_all(
        calendar_id=calendar_id,
        time_min=d0.isoformat(),
        time_max=d1.isoformat(),
        q=req.keyword,
        access_token=access_token,
        max_pages=3,
    )
    events = result.events if result.ok else []
    room_ids = {
        str(r.get("calendar_resource_id") or "")
        for r in rooms
        if req.target_room and req.target_room in (r.get("display_name"), r.get("name"))
    }
    out: list[dict[str, Any]] = []
    seen_series: set[str] = set()
    for ev in events:
        start = _parse(ev.get("start"))
        if not start or "T" not in str(ev.get("start")) or ev.get("status") == "cancelled":
            continue
        if req.keyword and _norm(req.keyword) not in _norm(ev.get("summary")):
            continue
        if req.target_time and start.strftime("%H:%M") != req.target_time:
            continue
        if room_ids and not room_ids & {a.get("email") for a in ev.get("attendees") or [] if a.get("resource")}:
            continue
        sid = str(ev.get("recurring_event_id") or "")
        if sid and not req.target_date:
            # 날짜를 안 정했으면 반복 일정은 가장 가까운 회차 하나만 후보로.
            if sid in seen_series:
                continue
            seen_series.add(sid)
        out.append(ev)
        if len(out) >= max_n:
            break
    return out


def load_event(event_id: str, *, access_token: str, calendar_id: str = "primary") -> dict[str, Any] | None:
    res = get_event(calendar_id=calendar_id, event_id=event_id, access_token=access_token)
    if not res.ok or not res.created_event:
        return None
    return _event_row(res.created_event)


# ---------- 실행 ----------
@dataclass
class EditPlan:
    """카드 폼에서 확정된 값 — 검토·실행에 그대로 쓴다."""

    operation: str
    scope: str  # single | this | following | all
    event_id: str
    series_id: str = ""
    new_date: str = ""
    new_start: str = ""
    new_end: str = ""
    new_room_id: str = ""  # "" = 그대로
    new_title: str = ""

    def params(self) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(self).items()}

    @classmethod
    def from_params(cls, p: dict[str, str]) -> "EditPlan":
        return cls(**{k: str(p.get(k) or "") for k in cls.__dataclass_fields__})


@dataclass
class EditResult:
    ok: bool
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_event_ids: list[str] = field(default_factory=list)
    html_link: str = ""


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def new_times(event: dict[str, Any], plan: EditPlan) -> tuple[datetime, datetime]:
    """폼 값으로 새 (시작, 종료). 비운 칸은 원래 값 유지, 길이는 원래 길이 유지."""
    s0, e0 = _parse(event.get("start")), _parse(event.get("end"))
    assert s0 and e0
    d = datetime.strptime(plan.new_date, "%Y-%m-%d").date() if plan.new_date else s0.date()
    start = _at(d, plan.new_start or s0.strftime("%H:%M"))
    if plan.new_end:
        end = _at(d, plan.new_end)
        if end <= start:
            end = start + (e0 - s0)
    else:
        end = start + (e0 - s0)
    return start, end


def _until_before(dt: datetime) -> str:
    return (dt - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _set_rrule_part(rrule: str, key: str, value: str | None) -> str:
    from domains.schedule_management.series_rebook import _set_rrule_part as impl

    return impl(rrule, key, value)


_RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _room_fields(plan: EditPlan, rooms: list[dict[str, Any]]) -> tuple[list[str] | None, str | None]:
    if not plan.new_room_id:
        return None, None
    loc = next(
        (str(r.get("name") or r.get("display_name") or "") for r in rooms
         if str(r.get("calendar_resource_id") or "") == plan.new_room_id),
        "",
    )
    return [plan.new_room_id], loc


def _label(start: datetime, end: datetime) -> str:
    return f"{start.month}/{start.day}({WEEKDAYS_KO[start.weekday()]}) {start:%H:%M}~{end:%H:%M}"


def apply_edit(
    event: dict[str, Any],
    plan: EditPlan,
    *,
    rooms: list[dict[str, Any]],
    access_token: str,
    calendar_id: str = "primary",
) -> EditResult:
    summary = str(event.get("summary") or "")
    occ_start = _parse(event.get("start"))
    assert occ_start is not None
    series_id = plan.series_id
    master: dict[str, Any] = {}
    if series_id and plan.scope in ("following", "all"):
        res = get_event(calendar_id=calendar_id, event_id=series_id, access_token=access_token)
        master = (res.created_event or {}) if res.ok else {}
        if not master:
            return EditResult(ok=False, failed=["반복 일정 원본을 불러오지 못했어요"])
    rrules = [r for r in master.get("recurrence") or [] if str(r).upper().startswith("RRULE:")]
    master_start = _parse(str((master.get("start") or {}).get("dateTime") or ""))
    is_first = bool(master_start and master_start >= occ_start - timedelta(minutes=1))
    scope = plan.scope
    if scope == "following" and is_first:
        scope = "all"  # 첫 회차부터면 '이후 전체' = '반복 전체'

    # ----- 삭제 -----
    if plan.operation == "delete":
        if scope in ("single", "this"):
            res = delete_event(calendar_id=calendar_id, event_id=plan.event_id, access_token=access_token,
                               send_updates="all")
            what = f"{summary} {_label(occ_start, _parse(event.get('end')) or occ_start)} 삭제"
        elif scope == "all":
            res = delete_event(calendar_id=calendar_id, event_id=series_id, access_token=access_token,
                               send_updates="all")
            what = f"{summary} 반복 일정 전체 삭제"
        else:
            if not rrules:
                return EditResult(ok=False, failed=["반복 규칙을 읽지 못했어요"])
            res = patch_event(
                calendar_id=calendar_id, event_id=series_id,
                # COUNT 와 UNTIL 은 함께 쓸 수 없다 — 횟수 규칙이어도 이 회차 직전에서 끊는다.
                recurrence=[_set_rrule_part(_set_rrule_part(rrules[0], "COUNT", None), "UNTIL",
                                            _until_before(occ_start))],
                send_updates="all", access_token=access_token,
            )
            what = f"{summary} {occ_start.month}/{occ_start.day} 회차부터 이후 전체 삭제"
        return EditResult(ok=res.ok, done=[what] if res.ok else [], failed=[] if res.ok else [what])

    # ----- 수정 -----
    resource_emails, location = _room_fields(plan, rooms)
    title = plan.new_title or None
    start, end = new_times(event, plan)
    if scope in ("single", "this"):
        res = patch_event(
            calendar_id=calendar_id, event_id=plan.event_id, summary=title,
            start_iso=start.isoformat(), end_iso=end.isoformat(),
            resource_emails=resource_emails, location=location,
            send_updates="all", access_token=access_token,
        )
        what = f"{summary} → {_label(start, end)}"
        return EditResult(ok=res.ok, done=[what] if res.ok else [], failed=[] if res.ok else [what])

    if not rrules:
        return EditResult(ok=False, failed=["반복 규칙을 읽지 못했어요"])
    day_shift = start.date() - occ_start.date()
    new_rrule = rrules[0]
    if day_shift.days:
        new_rrule = _set_rrule_part(new_rrule, "BYDAY", _RRULE_DAYS[start.weekday()])
    duration = end - start

    if scope == "all":
        assert master_start is not None
        m_start = _at(master_start.date() + day_shift, start.strftime("%H:%M"))
        res = patch_event(
            calendar_id=calendar_id, event_id=series_id, summary=title,
            start_iso=m_start.isoformat(), end_iso=(m_start + duration).isoformat(),
            resource_emails=resource_emails, location=location,
            recurrence=[new_rrule] if day_shift.days else None,
            send_updates="all", access_token=access_token,
        )
        what = f"{summary} 반복 전체 → 매주 {WEEKDAYS_KO[start.weekday()]} {start:%H:%M}~{end:%H:%M}"
        return EditResult(ok=res.ok, done=[what] if res.ok else [], failed=[] if res.ok else [what])

    # following: 이 회차부터 새 반복 일정을 만들고 기존 반복을 끊는다(생성 성공 후에만 끊음).
    if "COUNT=" in new_rrule.upper():
        return EditResult(ok=False, failed=["횟수가 정해진 반복 일정은 '이후 전체' 변경을 지원하지 않아요. '이 회차만' 또는 '반복 전체'를 골라 주세요."])
    people = [str(a.get("email") or "") for a in master.get("attendees") or [] if not a.get("resource") and a.get("email")]
    old_rooms = [str(a.get("email") or "") for a in master.get("attendees") or [] if a.get("resource") and a.get("email")]
    created = create_event(
        calendar_id=calendar_id,
        summary=title or str(master.get("summary") or summary),
        start_iso=start.isoformat(), end_iso=end.isoformat(),
        attendees=people,
        resource_emails=resource_emails if resource_emails is not None else old_rooms,
        location=location if location is not None else str(master.get("location") or ""),
        description=str(master.get("description") or ""),
        recurrence=[new_rrule],
        conference_data=master.get("conferenceData") or None,
        send_updates="all", access_token=access_token,
    )
    if not created.ok:
        return EditResult(ok=False, failed=["새 반복 일정 생성"])
    new_event = created.created_event or {}
    result = EditResult(ok=True, created_event_ids=[str(new_event.get("id") or "")],
                        html_link=str(new_event.get("htmlLink") or ""))
    result.done.append(f"{occ_start.month}/{occ_start.day}부터 매주 {WEEKDAYS_KO[start.weekday()]} {start:%H:%M}~{end:%H:%M}")
    cut = patch_event(
        calendar_id=calendar_id, event_id=series_id,
        recurrence=[_set_rrule_part(rrules[0], "UNTIL", _until_before(occ_start))],
        send_updates="all", access_token=access_token,
    )
    if not cut.ok:
        result.ok = False
        result.failed.append("기존 반복 일정 종료 처리(새 일정은 만들어졌어요 — 기존 일정의 이후 회차를 직접 삭제해 주세요)")
    return result


def backup_ids(plan: EditPlan) -> list[str]:
    """변경 전에 원본을 떠 둘 이벤트 — 회차 하나면 그 회차, 반복 범위면 원본(master)."""
    if plan.scope in ("following", "all") and plan.series_id:
        return [plan.series_id]
    return [plan.event_id]


def check_room_free(
    event: dict[str, Any],
    plan: EditPlan,
    *,
    access_token: str,
) -> bool | None:
    """바뀐 시각에 (새/기존) 회의실이 비는지 — 이 일정 자신은 제외. 회의실이 없으면 None."""
    from domains.schedule_management.calendar_client import freebusy_query

    room_ids = [plan.new_room_id] if plan.new_room_id else [
        str(a.get("email") or "") for a in event.get("attendees") or [] if a.get("resource")
    ]
    room_ids = [r for r in room_ids if r]
    if not room_ids or plan.operation != "modify":
        return None
    start, end = new_times(event, plan)
    fb: CalendarResult = freebusy_query(
        calendar_ids=room_ids, time_min=start.isoformat(), time_max=end.isoformat(), access_token=access_token
    )
    if not fb.ok:
        return None
    from domains.schedule_management.series_rebook import _overlaps, _subtract

    s0, e0 = _parse(event.get("start")), _parse(event.get("end"))
    own_rooms = {a.get("email") for a in event.get("attendees") or [] if a.get("resource")}
    for rid in room_ids:
        spans = [(_parse(x.get("start")), _parse(x.get("end"))) for x in fb.busy.get(rid) or []]
        ranges = [(a, b) for a, b in spans if a and b]
        # 원래 회의실이면 이 일정 자신이 잡고 있는 구간은 빼고 본다.
        if rid in own_rooms and s0 and e0:
            ranges = _subtract(ranges, [(s0, e0)])
        if _overlaps(ranges, start, end):
            return False
    return True
