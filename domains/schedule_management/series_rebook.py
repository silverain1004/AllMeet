"""정기회의(반복 일정) 재배치 — 대상 시리즈 찾기 · 주 단위 가용성 조회 · 결정적 솔버 · 실행.

흐름: SeriesRebookSpec(series_spec.py) → find_series_candidates → fetch_week_busy → solve
     → (카드에서 승인) → apply_rebook.

솔버 원칙(사용자 결정):
- 회의실 가용성이 하드 제약, 참석자 충돌은 경고(동점일 때만 적은 쪽 우선).
- 모든 주에 같은 (요일·시각·회의실)이 되면 그걸로 고정. 아니면 가장 많은 주를 덮는 조합을
  공통으로 두고, 안 되는 주만 개별 배정(같은 시각 다른 회의실 → 우선순위 순 다른 시각/요일).
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from domains.schedule_management.calendar_client import (
    KST,
    CalendarResult,
    create_event,
    get_event,
    is_dry_run,
    list_events_all,
    list_instances,
    patch_event,
    to_kst_iso,
)
from domains.schedule_management.conflict_slots import _busy_ranges, _freebusy_chunked
from domains.schedule_management.rooms import _room_recommendation_sort_key
from domains.schedule_management.rooms_group import list_group_room_bookings, match_booking_to_rooms
from domains.schedule_management.series_spec import WEEKDAYS_KO, SeriesRebookSpec

logger = logging.getLogger(__name__)

Range = tuple[datetime, datetime]
_RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_BUSY_TTL_SEC = 60


# ---------- 데이터 ----------
@dataclass
class Occurrence:
    instance_id: str
    start: datetime
    end: datetime
    room_ids: list[str] = field(default_factory=list)

    @property
    def monday(self) -> date:
        d = self.start.date()
        return d - timedelta(days=d.weekday())


@dataclass
class SeriesCandidate:
    series_id: str
    summary: str
    organizer_email: str
    organizer_self: bool
    occurrences: list[Occurrence]
    people: list[str]
    room_ids: list[str]
    html_link: str = ""
    score: int = 0

    @property
    def weekday(self) -> int:
        return self.occurrences[0].start.weekday()

    @property
    def start_time(self) -> str:
        return self.occurrences[0].start.strftime("%H:%M")

    @property
    def duration(self) -> int:
        occ = self.occurrences[0]
        return max(int((occ.end - occ.start).total_seconds() // 60), 30)


@dataclass(frozen=True)
class SlotKey:
    weekday: int
    start: str
    room_id: str

    def encode(self) -> str:
        return f"{self.weekday}|{self.start}|{self.room_id}"

    @classmethod
    def decode(cls, raw: str) -> "SlotKey | None":
        parts = (raw or "").split("|")
        if len(parts) != 3:
            return None
        try:
            return cls(int(parts[0]), parts[1], parts[2])
        except ValueError:
            return None


@dataclass
class CommonOption:
    key: SlotKey
    room_name: str
    capacity: int
    tier: int
    available_weeks: int
    total_weeks: int
    conflict_weeks: int


@dataclass
class WeekAssignment:
    monday: date
    instance_id: str
    original_start: datetime
    key: SlotKey | None  # None = 배정 불가
    room_name: str = ""
    capacity: int = 0
    conflicts: list[str] = field(default_factory=list)
    is_exception: bool = False

    def new_date(self) -> date | None:
        return self.monday + timedelta(days=self.key.weekday) if self.key else None


@dataclass
class RebookPlan:
    options: list[CommonOption]
    chosen: CommonOption | None
    weeks: list[WeekAssignment]
    duration: int
    people_count: int

    @property
    def unassigned(self) -> list[WeekAssignment]:
        return [w for w in self.weeks if w.key is None]

    @property
    def exceptions(self) -> list[WeekAssignment]:
        return [w for w in self.weeks if w.key is not None and w.is_exception]


@dataclass
class WeekBusy:
    rooms: dict[str, list[Range]] = field(default_factory=dict)
    people: dict[str, list[Range]] = field(default_factory=dict)


# ---------- 시간 유틸 ----------
def _parse_dt(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")).astimezone(KST)
    except ValueError:
        return None


def _at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _subtract(ranges: list[Range], holes: list[Range]) -> list[Range]:
    """busy 구간에서 이 시리즈 자신의 회의 구간을 뺀다 — 안 빼면 원래 시각에 전원이 바빠 보인다."""
    out = list(ranges)
    for h0, h1 in holes:
        nxt: list[Range] = []
        for s, e in out:
            if e <= h0 or s >= h1:
                nxt.append((s, e))
                continue
            if s < h0:
                nxt.append((s, h0))
            if e > h1:
                nxt.append((h1, e))
        out = nxt
    return out


def _overlaps(ranges: list[Range], t0: datetime, t1: datetime) -> bool:
    return any(s < t1 and e > t0 for s, e in ranges)


# ---------- 대상 시리즈 찾기 ----------
def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def find_series_candidates(
    spec: SeriesRebookSpec,
    *,
    access_token: str,
    rooms: list[dict[str, Any]],
    calendar_id: str = "primary",
) -> list[SeriesCandidate]:
    d0 = datetime.strptime(spec.effective_from, "%Y-%m-%d").date()
    d1 = datetime.strptime(spec.horizon_end, "%Y-%m-%d").date() + timedelta(days=1)
    time_min, time_max = _at(d0, "00:00").isoformat(), _at(d1, "00:00").isoformat()
    queries = [q for q in (spec.title_keyword, spec.team_id) if q]
    events: list[dict[str, Any]] = []
    for q in queries:
        result = list_events_all(
            calendar_id=calendar_id, time_min=time_min, time_max=time_max, q=q, access_token=access_token
        )
        if result.ok and result.events:
            events = result.events
            break
    return group_series(events, spec=spec, rooms=rooms)


def group_series(
    events: list[dict[str, Any]],
    *,
    spec: SeriesRebookSpec,
    rooms: list[dict[str, Any]],
) -> list[SeriesCandidate]:
    room_ids = {
        str(r.get("calendar_resource_id") or "").strip()
        for r in rooms
        if spec.source_room and spec.source_room in (r.get("display_name"), r.get("name"))
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        sid = str(ev.get("recurring_event_id") or "")
        if not sid or ev.get("status") == "cancelled" or "T" not in str(ev.get("start") or ""):
            continue
        grouped.setdefault(sid, []).append(ev)

    out: list[SeriesCandidate] = []
    for sid, items in grouped.items():
        occs: list[Occurrence] = []
        for ev in items:
            s, e = _parse_dt(ev.get("start")), _parse_dt(ev.get("end"))
            if not s or not e:
                continue
            rids = [str(a.get("email") or "") for a in ev.get("attendees") or [] if a.get("resource")]
            occs.append(Occurrence(instance_id=str(ev.get("id") or ""), start=s, end=e, room_ids=rids))
        if not occs:
            continue
        occs.sort(key=lambda o: o.start)
        first = items[0]
        people = [
            str(a.get("email") or "")
            for a in first.get("attendees") or []
            if not a.get("resource") and str(a.get("email") or "")
        ]
        organizer = str(first.get("organizer_email") or "")
        if organizer and organizer not in people:
            people.insert(0, organizer)
        summary = str(first.get("summary") or "")
        score = 0
        if spec.team_id and spec.team_id.lower() in _norm(summary):
            score += 3
        if spec.title_keyword and _norm(spec.title_keyword) in _norm(summary):
            score += 1
        if spec.source_weekday is not None and occs[0].start.weekday() == spec.source_weekday:
            score += 2
        all_rids = list(dict.fromkeys(r for o in occs for r in o.room_ids))
        if room_ids and room_ids & set(all_rids):
            score += 2
        elif spec.source_room and spec.source_room.lower() in str(first.get("location") or "").lower():
            score += 2
        out.append(
            SeriesCandidate(
                series_id=sid,
                summary=summary,
                organizer_email=organizer,
                organizer_self=bool(first.get("organizer_self")),
                occurrences=occs,
                people=people,
                room_ids=all_rids,
                html_link=str(first.get("html_link") or ""),
                score=score,
            )
        )
    out.sort(key=lambda c: (-c.score, c.occurrences[0].start))
    return out


def candidate_rooms(
    spec: SeriesRebookSpec,
    series: SeriesCandidate,
    rooms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """옮겨 갈 수 있는 회의실 — 같은 지역, 제외 목록 밖, 최소 정원 이상(인원보다 작아도 허용)."""
    by_id = {str(r.get("calendar_resource_id") or ""): r for r in rooms}
    region = spec.region
    if not region:
        for rid in series.room_ids:
            if rid in by_id:
                region = str(by_id[rid].get("office") or "")
                break
    excluded = {x.lower() for x in spec.exclude_rooms}
    need = len(series.people)
    floor = spec.min_capacity if spec.min_capacity else need
    out: list[dict[str, Any]] = []
    for r in rooms:
        rid = str(r.get("calendar_resource_id") or "").strip()
        if not rid:
            continue
        if region and str(r.get("office") or "gunsan") != region:
            continue
        names = {str(r.get("display_name") or "").lower(), str(r.get("name") or "").lower()}
        if names & excluded:
            continue
        cap = int(r.get("capacity") or 0)
        if cap and cap < floor:
            continue
        out.append(r)
    return out


# ---------- 가용성 조회 ----------
_busy_cache: dict[tuple, tuple[float, WeekBusy]] = {}


def fetch_week_busy(
    mondays: list[date],
    *,
    room_ids: list[str],
    people: list[str],
    rooms: list[dict[str, Any]],
    access_token: str,
    fresh: bool = False,
) -> dict[date, WeekBusy]:
    """주(월~금) 단위로 회의실·참석자 freeBusy + 집계 캘린더 예약을 병렬 조회."""
    key_ids = (tuple(sorted(room_ids)), tuple(sorted(p.lower() for p in people)))

    def _one(monday: date) -> WeekBusy:
        ck = (monday, key_ids)
        hit = _busy_cache.get(ck)
        if not fresh and hit and time.monotonic() - hit[0] < _BUSY_TTL_SEC:
            return hit[1]
        t0 = _at(monday, "00:00").isoformat()
        t1 = _at(monday + timedelta(days=5), "00:00").isoformat()
        room_busy = _freebusy_chunked(room_ids, time_min=t0, time_max=t1, access_token=access_token)
        people_busy = _freebusy_chunked(people, time_min=t0, time_max=t1, access_token=access_token)
        week = WeekBusy(
            rooms={rid: _busy_ranges(room_busy.get(rid)) for rid in room_ids},
            people={p: _busy_ranges(spans) for p, spans in people_busy.items()},
        )
        for ev in list_group_room_bookings(time_min=t0, time_max=t1, access_token=access_token, all_pages=True):
            s, e = _parse_dt(ev.get("start")), _parse_dt(ev.get("end"))
            if not s or not e:
                continue
            for room in match_booking_to_rooms(ev, rooms):
                rid = str(room.get("calendar_resource_id") or "")
                if rid in week.rooms:
                    week.rooms[rid].append((s, e))
        _busy_cache[ck] = (time.monotonic(), week)
        return week

    out: dict[date, WeekBusy] = {}
    with ThreadPoolExecutor(max_workers=min(6, max(len(mondays), 1))) as pool:
        futures = {m: pool.submit(_one, m) for m in mondays}
        for m, f in futures.items():
            try:
                out[m] = f.result()
            except Exception:
                logger.warning("주간 가용성 조회 실패 week=%s", m, exc_info=True)
                out[m] = WeekBusy()
    return out


# ---------- 솔버 ----------
def _weekdays_for(scope: str, source_wd: int) -> list[int]:
    if scope == "same":
        return [source_wd]
    others = [d for d in range(5) if d != source_wd]
    return sorted(others, key=lambda d: (abs(d - source_wd), d))


def _starts_in(window: tuple[str, str], duration: int, original: str) -> list[str]:
    lo, hi = _minutes(window[0]), _minutes(window[1]) - duration
    starts = [f"{m // 60:02d}:{m % 60:02d}" for m in range(lo, hi + 1, 30)]
    base = _minutes(original)
    return sorted(starts, key=lambda t: (abs(_minutes(t) - base), t))


def solve(
    spec: SeriesRebookSpec,
    series: SeriesCandidate,
    rooms: list[dict[str, Any]],
    busy: dict[date, WeekBusy],
    *,
    now: datetime | None = None,
    choice: SlotKey | None = None,
    max_options: int = 3,
    name_of: dict[str, str] | None = None,
) -> RebookPlan:
    now = now or datetime.now(KST)
    duration = series.duration
    people_count = len(series.people)
    room_by_id = {str(r.get("calendar_resource_id") or ""): r for r in rooms}
    preferred = {p.lower() for p in spec.preferred_rooms}
    eff = datetime.strptime(spec.effective_from, "%Y-%m-%d").date()
    names = name_of or {}

    # 주별 busy 에서 이 시리즈 자신의 구간을 뺀다.
    occ_by_week: dict[date, Occurrence] = {}
    for occ in series.occurrences:
        occ_by_week.setdefault(occ.monday, occ)
    weeks = sorted(occ_by_week)
    cleaned: dict[date, WeekBusy] = {}
    for m in weeks:
        wb = busy.get(m) or WeekBusy()
        occ = occ_by_week[m]
        hole = [(occ.start, occ.end)]
        cleaned[m] = WeekBusy(
            rooms={
                rid: _subtract(rs, hole) if rid in occ.room_ids else rs for rid, rs in wb.rooms.items()
            },
            people={p: _subtract(rs, hole) for p, rs in wb.people.items()},
        )

    # 우선순위 순 후보 키 (중복은 처음 나온 tier 유지)
    keys: dict[SlotKey, int] = {}
    for tier, (scope, window) in enumerate(spec.time_tiers(series.weekday)):
        for wd in _weekdays_for(scope, series.weekday):
            for start in _starts_in(window, duration, series.start_time):
                for r in rooms:
                    k = SlotKey(wd, start, str(r.get("calendar_resource_id") or ""))
                    keys.setdefault(k, tier)

    def _slot_state(k: SlotKey, m: date) -> tuple[bool, list[str]]:
        d = m + timedelta(days=k.weekday)
        t0 = _at(d, k.start)
        t1 = t0 + timedelta(minutes=duration)
        if d < eff or t0 <= now:
            return False, []
        wb = cleaned[m]
        if _overlaps(wb.rooms.get(k.room_id) or [], t0, t1):
            return False, []
        conflicts = [names.get(p.lower(), p) for p, rs in wb.people.items() if _overlaps(rs, t0, t1)]
        return True, conflicts

    def _room_rank(rid: str) -> tuple[int, int, int, str]:
        room = room_by_id.get(rid) or {}
        _, fit, excess, _ = _room_recommendation_sort_key(room, attendee_count=people_count)
        disp = str(room.get("display_name") or room.get("name") or "")
        return (0 if disp.lower() in preferred else 1, fit, excess, disp)

    def _dist(k: SlotKey) -> tuple[int, int]:
        return (abs(k.weekday - series.weekday), abs(_minutes(k.start) - _minutes(series.start_time)))

    stats: dict[SlotKey, tuple[int, int, dict[date, list[str]]]] = {}
    for k in keys:
        ok_weeks: dict[date, list[str]] = {}
        for m in weeks:
            ok, conf = _slot_state(k, m)
            if ok:
                ok_weeks[m] = conf
        if ok_weeks:
            stats[k] = (len(ok_weeks), sum(1 for c in ok_weeks.values() if c), ok_weeks)

    # 몇 주(약 20%, 최소 1주)만 예외로 빼면 되는 조합은 '거의 고정'으로 보고 우선순위(tier)로
    # 겨룬다 — 목요일 한 주가 막혔다고 시리즈 전체를 수요일로 옮기지 않게. 그보다 많이 비면
    # 덮는 주 수가 먼저다.
    total = len(weeks)
    allowance = max(1, total // 5)

    def _common_rank(k: SlotKey) -> tuple:
        n, conf, _ = stats[k]
        band = 0 if n >= total - allowance else 1
        return (band, keys[k] if band == 0 else 0, -n, keys[k], conf, _room_rank(k.room_id), _dist(k), k.start)

    def _option(k: SlotKey) -> CommonOption:
        room = room_by_id.get(k.room_id) or {}
        n, conf, _ = stats[k]
        return CommonOption(
            key=k,
            room_name=str(room.get("display_name") or room.get("name") or ""),
            capacity=int(room.get("capacity") or 0),
            tier=keys[k],
            available_weeks=n,
            total_weeks=len(weeks),
            conflict_weeks=conf,
        )

    ranked = sorted(stats, key=_common_rank)
    options: list[CommonOption] = []
    if ranked:
        best = ranked[0]
        picks = [best]
        # 1순위가 예외 주를 남기면, 모든 주가 되는 조합도 함께 보여 준다(고정성을 원할 수 있음).
        if stats[best][0] < total:
            full = [k for k in ranked if stats[k][0] == total]
            if full:
                picks.append(full[0])
        seen_times = {(k.weekday, k.start) for k in picks}
        for k in ranked:
            if len(picks) >= max_options:
                break
            if (k.weekday, k.start) not in seen_times:
                picks.append(k)
                seen_times.add((k.weekday, k.start))
        options = [_option(k) for k in picks[:max_options]]

    chosen: CommonOption | None = None
    if choice is not None and choice in stats:
        chosen = next((o for o in options if o.key == choice), None) or _option(choice)
    elif options:
        chosen = options[0]

    assignments: list[WeekAssignment] = []
    for m in weeks:
        occ = occ_by_week[m]
        wa = WeekAssignment(monday=m, instance_id=occ.instance_id, original_start=occ.start, key=None)
        if chosen and m in stats[chosen.key][2]:
            wa.key = chosen.key
            wa.conflicts = stats[chosen.key][2][m]
        else:
            # 같은 시각 다른 회의실 → 우선순위 순 다른 시각/요일
            def _exc_rank(k: SlotKey) -> tuple:
                same_time = chosen is not None and (k.weekday, k.start) == (chosen.key.weekday, chosen.key.start)
                return (0 if same_time else 1, keys[k], len(stats[k][2][m]), _room_rank(k.room_id), _dist(k))

            options_for_week = [k for k in stats if m in stats[k][2]]
            if options_for_week:
                k = min(options_for_week, key=_exc_rank)
                wa.key = k
                wa.conflicts = stats[k][2][m]
                wa.is_exception = chosen is not None
        if wa.key:
            room = room_by_id.get(wa.key.room_id) or {}
            wa.room_name = str(room.get("display_name") or room.get("name") or "")
            wa.capacity = int(room.get("capacity") or 0)
        assignments.append(wa)

    return RebookPlan(
        options=options, chosen=chosen, weeks=assignments, duration=duration, people_count=people_count
    )


# ---------- 실행 ----------
@dataclass
class ApplyResult:
    ok: bool
    mode: str  # split | master | instances
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    html_link: str = ""
    created_event_ids: list[str] = field(default_factory=list)


def _until_before(dt: datetime) -> str:
    """RRULE UNTIL — dt 직전(1초 전)을 UTC 로."""
    return (dt - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _set_rrule_part(rrule: str, key: str, value: str | None) -> str:
    body = rrule[len("RRULE:") :] if rrule.upper().startswith("RRULE:") else rrule
    parts = [p for p in body.split(";") if p and not p.upper().startswith(key + "=")]
    if value is not None:
        parts.append(f"{key}={value}")
    return "RRULE:" + ";".join(parts)


def _label(w: WeekAssignment, duration: int) -> str:
    d = w.new_date()
    if not d or not w.key:
        return f"{w.original_start.month}/{w.original_start.day}"
    end = (_at(d, w.key.start) + timedelta(minutes=duration)).strftime("%H:%M")
    return f"{d.month}/{d.day}({WEEKDAYS_KO[d.weekday()]}) {w.key.start}~{end} {w.room_name}"


def _room_location(rid: str, rooms: list[dict[str, Any]]) -> str:
    for r in rooms:
        if str(r.get("calendar_resource_id") or "") == rid:
            return str(r.get("name") or r.get("display_name") or "")
    return ""


def _patch_week(
    w: WeekAssignment,
    instance_id: str,
    *,
    duration: int,
    rooms: list[dict[str, Any]],
    access_token: str,
    calendar_id: str,
) -> CalendarResult:
    if w.key is None:
        # 배정 불가 주는 원래 시각으로 두되, 못 쓰는 회의실은 빼서 거절 응답이 쌓이지 않게 한다.
        return patch_event(
            calendar_id=calendar_id,
            event_id=instance_id,
            resource_emails=[],
            location="",
            send_updates="all",
            access_token=access_token,
        )
    d = w.new_date()
    assert d is not None
    start = _at(d, w.key.start)
    return patch_event(
        calendar_id=calendar_id,
        event_id=instance_id,
        start_iso=start.isoformat(),
        end_iso=(start + timedelta(minutes=duration)).isoformat(),
        resource_emails=[w.key.room_id],
        location=_room_location(w.key.room_id, rooms),
        send_updates="all",
        access_token=access_token,
    )


def _patch_weeks(
    weeks: list[tuple[WeekAssignment, str]],
    *,
    duration: int,
    rooms: list[dict[str, Any]],
    access_token: str,
    calendar_id: str,
    result: ApplyResult,
) -> None:
    if not weeks:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            (w, pool.submit(
                _patch_week, w, iid, duration=duration, rooms=rooms,
                access_token=access_token, calendar_id=calendar_id,
            ))
            for w, iid in weeks
        ]
        for w, f in futures:
            try:
                res = f.result()
            except Exception:
                logger.warning("정기회의 인스턴스 수정 실패", exc_info=True)
                res = CalendarResult(ok=False)
            (result.done if res.ok else result.failed).append(_label(w, duration))


def apply_rebook(
    series: SeriesCandidate,
    plan: RebookPlan,
    *,
    rooms: list[dict[str, Any]],
    access_token: str,
    calendar_id: str = "primary",
) -> ApplyResult:
    """공통 슬롯이 있으면 '이후 반복 전체'를 옮긴다(시리즈 분할) — 확인 기간 이후 회차도 따라온다.
    분할이 불가하면(COUNT 규칙·권한 등) 확인 기간 안의 회차만 개별 수정한다."""
    master_res = get_event(calendar_id=calendar_id, event_id=series.series_id, access_token=access_token)
    master = (master_res.created_event or {}) if master_res.ok else {}
    rrules = [r for r in master.get("recurrence") or [] if str(r).upper().startswith("RRULE:")]
    chosen = plan.chosen
    first_week = plan.weeks[0] if plan.weeks else None
    can_split = bool(chosen and rrules and first_week and "COUNT=" not in rrules[0].upper())

    if can_split and chosen and first_week:
        result = _apply_split(
            series, plan, master, rrules[0], rooms=rooms, access_token=access_token, calendar_id=calendar_id
        )
        # 하나라도 바뀌었으면(새 시리즈 생성 등) 회차별 수정으로 폴백하지 않는다 — 중복 수정 방지.
        if result.ok or result.done:
            return result
        logger.warning("정기회의 시리즈 분할 실패 → 회차별 수정으로 폴백: %s", result.failed)

    result = ApplyResult(ok=True, mode="instances")
    _patch_weeks(
        [(w, w.instance_id) for w in plan.weeks],
        duration=plan.duration, rooms=rooms, access_token=access_token,
        calendar_id=calendar_id, result=result,
    )
    result.ok = not result.failed
    if plan.weeks:
        last = plan.weeks[-1].original_start
        result.notes.append(f"{last.month}/{last.day} 이후 회차는 기존 설정 그대로예요.")
    return result


def _apply_split(
    series: SeriesCandidate,
    plan: RebookPlan,
    master: dict[str, Any],
    rrule: str,
    *,
    rooms: list[dict[str, Any]],
    access_token: str,
    calendar_id: str,
) -> ApplyResult:
    chosen = plan.chosen
    assert chosen is not None
    # 새 시리즈는 공통 슬롯이 실제로 배정된 첫 주부터 시작한다. 그 앞 주(예: 목→화로 옮기는데
    # 첫 주 화요일이 적용 시작일 이전)는 기존 시리즈에 남겨 회차별로 고친다.
    split_at = next((i for i, w in enumerate(plan.weeks) if w.key == chosen.key), None)
    if split_at is None:
        return ApplyResult(ok=False, mode="split", failed=["공통 시간 배정 주 없음"])
    lead, tail = plan.weeks[:split_at], plan.weeks[split_at:]
    first = tail[0]
    new_rrule = _set_rrule_part(rrule, "BYDAY", _RRULE_DAYS[chosen.key.weekday])
    start = _at(first.monday + timedelta(days=chosen.key.weekday), chosen.key.start)
    end = start + timedelta(minutes=plan.duration)
    master_start = _parse_dt(str((master.get("start") or {}).get("dateTime") or ""))
    people = [
        str(a.get("email") or "")
        for a in master.get("attendees") or []
        if not a.get("resource") and str(a.get("email") or "")
    ]
    location = _room_location(chosen.key.room_id, rooms)

    # 시리즈가 확인 기간 안에서 시작했다면 남길 과거 회차가 없다 → 원본을 그대로 옮긴다.
    if master_start and master_start >= plan.weeks[0].original_start:
        if lead:
            return ApplyResult(ok=False, mode="master", failed=["앞선 주 처리 불가"])
        res = patch_event(
            calendar_id=calendar_id,
            event_id=series.series_id,
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
            resource_emails=[chosen.key.room_id],
            location=location,
            recurrence=[new_rrule],
            send_updates="all",
            access_token=access_token,
        )
        result = ApplyResult(ok=res.ok, mode="master")
        new_id = series.series_id
        if not res.ok:
            result.failed.append("반복 일정 원본 수정")
            return result
    else:
        created = create_event(
            calendar_id=calendar_id,
            summary=str(master.get("summary") or series.summary),
            start_iso=start.isoformat(),
            end_iso=end.isoformat(),
            attendees=people,
            resource_emails=[chosen.key.room_id],
            location=location,
            description=str(master.get("description") or ""),
            recurrence=[new_rrule],
            conference_data=master.get("conferenceData") or None,
            send_updates="all",
            access_token=access_token,
        )
        result = ApplyResult(ok=created.ok, mode="split")
        if not created.ok:
            result.failed.append("새 반복 일정 생성")
            return result
        new_event = created.created_event or {}
        new_id = str(new_event.get("id") or "")
        result.html_link = str(new_event.get("htmlLink") or "")
        result.created_event_ids.append(new_id)
        # 새 시리즈를 만든 뒤에 기존 시리즈를 끊는다 — 생성이 실패하면 기존 일정이 그대로 남도록.
        truncated = patch_event(
            calendar_id=calendar_id,
            event_id=series.series_id,
            recurrence=[_set_rrule_part(rrule, "UNTIL", _until_before(first.original_start))],
            send_updates="all",
            access_token=access_token,
        )
        if not truncated.ok:
            result.failed.append(
                "기존 반복 일정 종료 처리(새 일정은 만들어졌어요 — 기존 일정의 이후 회차를 직접 삭제해 주세요)"
            )
    result.done.append(
        f"매주 {WEEKDAYS_KO[chosen.key.weekday]} {chosen.key.start}~{end.strftime('%H:%M')} "
        f"{chosen.room_name}(으)로 반복 일정 이동"
    )
    # 앞선 주는 기존 시리즈 회차 그대로 id 가 유효하다.
    _patch_weeks(
        [(w, w.instance_id) for w in lead], duration=plan.duration, rooms=rooms,
        access_token=access_token, calendar_id=calendar_id, result=result,
    )

    special = [w for w in tail if w.is_exception or w.key is None]
    if special and new_id and new_id != "dry-run" and not is_dry_run():
        last = tail[-1].monday + timedelta(days=7)
        inst = list_instances(
            calendar_id=calendar_id,
            event_id=new_id,
            time_min=_at(first.monday, "00:00").isoformat(),
            time_max=_at(last, "00:00").isoformat(),
            access_token=access_token,
        )
        by_week: dict[date, str] = {}
        for ev in inst.events if inst.ok else []:
            s = _parse_dt(ev.get("start"))
            if s:
                by_week.setdefault(s.date() - timedelta(days=s.weekday()), str(ev.get("id") or ""))
        todo = [(w, by_week[w.monday]) for w in special if w.monday in by_week]
        missing = [w for w in special if w.monday not in by_week]
        _patch_weeks(
            todo, duration=plan.duration, rooms=rooms, access_token=access_token,
            calendar_id=calendar_id, result=result,
        )
        result.failed.extend(_label(w, plan.duration) for w in missing)
    elif special:
        result.notes.append(f"(테스트 모드) 예외 {len(special)}주는 실제 수정하지 않았어요.")
    result.ok = not result.failed
    return result
