"""정기회의 재배치 — 명세 추출 · 라우팅 · 솔버 · 실행(모두 오프라인, 캘린더/LLM mock)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from domains.schedule_management.calendar_client import KST, CalendarResult

USER_REQUEST = (
    "기존 ERP2팀의 주간회의는 매주 목요일에 London 회의실 고정 시간으로 잡혀있었는데, 해당 회의실이 "
    "프로젝트 때문에 장기적으로 사용이 불가한 상황이야. 그래서 기존에  등록되어있는 ERP2팀 주간회의 기존 "
    "일정 수정이 필요해. 10월부터 이후로 쭉. 그래서  회의실과 시간이 가능한 일시를 찾고 싶어. 가장 좋은 "
    "방법은 오후 시간대로 고정인데, 회의실이 없을 수도 있으니까 일단 최대한 가능한 시간대면 좋겠어. 그리고 "
    "기존 일정의 참석인원은 많지만  6명인 회의실도 괜찮으니까 일단 회의실 예약이 우선이야."
)
TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)

MEMBERS = [
    {"name": "김철수", "email": "kim@x.com", "team_id": "ERP2", "team_name": "ERP2팀", "nickname": []},
    {"name": "이영희", "email": "lee@x.com", "team_id": "ERP2", "team_name": "ERP2팀", "nickname": []},
    {"name": "박민수", "email": "park@x.com", "team_id": "PC2", "team_name": "PC2팀", "nickname": []},
]


def _room(name: str, cap: int, rid: str, office: str = "seoul") -> dict:
    return {"display_name": name, "name": f"VNTG 서울 {name} ({cap})", "capacity": cap,
            "calendar_resource_id": rid, "office": office}


LONDON = _room("London", 8, "london@r")
PARIS = _room("Paris", 6, "paris@r")
HAWAII = _room("Hawaii", 6, "hawaii@r")
SEOUL = _room("Seoul", 20, "seoul@r")
GUNSAN = _room("Jeju", 10, "jeju@r", office="gunsan")
ROOMS = [LONDON, PARIS, HAWAII, SEOUL, GUNSAN]


# ---------- 명세 추출 ----------
def _spec(text=USER_REQUEST, llm=None, use_llm=False):
    from domains.schedule_management.series_spec import extract_series_spec

    return extract_series_spec(
        text, members=MEMBERS, sender_email="kim@x.com", today=TODAY,
        room_names=[r["display_name"] for r in ROOMS], llm=llm, use_llm=use_llm,
    )


def test_spec_from_user_request_rules_only():
    spec = _spec()
    assert spec.team_id == "ERP2"
    assert spec.title_keyword == "주간회의"
    assert spec.source_weekday == 3
    assert spec.source_room == "London" and spec.exclude_rooms == ["London"]
    assert spec.effective_from == "2026-10-01"
    assert spec.horizon_end == "2026-12-31"
    assert spec.preferred_window == ("13:00", "18:00")
    assert spec.allow_any_time and spec.allow_other_weekdays
    assert spec.min_capacity == 6


def test_spec_llm_fills_only_missing_and_is_validated():
    def fake_llm(prompt):
        assert "ERP2" in prompt
        return {
            "team_id": "NOPE",  # 모르는 팀 → 무시
            "source_room": "London",
            "preferred_rooms": ["Paris", "Atlantis"],  # 모르는 회의실 → 제거
            "effective_from": "2020-01-01",  # 과거 → 무시
            "preferred_window": ["06:00", "12:00"],  # 업무시간으로 잘림
            "allow_other_weekdays": False,
            "min_capacity": 4,
        }

    spec = _spec("우리팀 정기회의 회의실 좀 바꿔줘", llm=fake_llm, use_llm=True)
    assert spec.team_id == "ERP2"  # '우리팀' → 발신자 팀(규칙)
    assert spec.source_room == "London"
    assert spec.preferred_rooms == ["Paris"]
    assert spec.effective_from == "2026-10-01"
    assert spec.preferred_window == ("08:00", "12:00")
    assert spec.allow_other_weekdays is False
    assert spec.min_capacity == 4


def test_spec_rules_beat_llm():
    spec = _spec(USER_REQUEST, llm=lambda p: {"min_capacity": 2, "preferred_window": ["09:00", "11:00"]}, use_llm=True)
    assert spec.min_capacity == 6
    assert spec.preferred_window == ("13:00", "18:00")


def test_spec_llm_failure_degrades():
    def boom(prompt):
        raise RuntimeError("vertex down")

    assert _spec(USER_REQUEST, llm=boom, use_llm=True).team_id == "ERP2"


@pytest.mark.parametrize(
    "text,field,expected",
    [
        ("주간회의 11월 15일부터 다른 회의실로 옮겨줘", "effective_from", "2026-11-15"),
        ("주간회의 다음주부터 시간 변경해줘", "effective_from", "2026-10-05"),
        ("주간회의 11월까지만 회의실 바꿔줘", "horizon_end", "2026-11-30"),
        ("주간회의 오후에만 되는 시간으로 옮겨줘", "allow_any_time", False),
        ("주간회의 요일은 그대로 두고 시간만 바꿔줘", "allow_other_weekdays", False),
        ("매주 목요일 고정으로 잡힌 주간회의 회의실 변경", "allow_other_weekdays", True),
    ],
)
def test_spec_table(text, field, expected):
    assert getattr(_spec(text), field) == expected


def test_spec_json_roundtrip():
    from domains.schedule_management.series_spec import SeriesRebookSpec

    spec = _spec()
    back = SeriesRebookSpec.from_json(spec.to_json())
    assert back.preferred_window == spec.preferred_window
    assert back.exclude_rooms == spec.exclude_rooms and back.min_capacity == 6


# ---------- 라우팅 ----------
@pytest.mark.parametrize(
    "text,expected",
    [
        (USER_REQUEST, True),
        ("ERP2팀 주간회의 10월부터 다른 회의실로 옮겨줘", True),
        ("정기회의 다음달부터 시간 변경해줘", True),
        ("내일 주간회의 시간 4시로 변경해줘", False),  # 한 회차 수정 → event_edit
        ("주간회의 10월부터 삭제해줘", False),  # 삭제 → event_edit
        ("내일 3시 회의실 예약해줘", False),
        ("주간회의 팀 등록", False),
        ("주간회의 페이지 수정해줘", False),
        ("주간보고 일정 변경", False),
    ],
)
def test_looks_like_series_rebook(text, expected):
    from domains.routing.patterns import looks_like_series_rebook

    assert looks_like_series_rebook(text) is expected


def test_user_request_routes_to_schedule_management():
    from domains.routing.intent import deterministic_intent

    assert deterministic_intent(USER_REQUEST) == "schedule_management"


# ---------- 솔버 ----------
def _thursdays(n=4, start=date(2026, 10, 1)):
    return [start + timedelta(weeks=i) for i in range(n)]


def _series(n=4, people=("kim@x.com", "lee@x.com", "a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com", "f@x.com", "g@x.com")):
    from domains.schedule_management.series_rebook import Occurrence, SeriesCandidate

    occs = [
        Occurrence(instance_id=f"s1_{d:%Y%m%d}", start=datetime(d.year, d.month, d.day, 10, 0, tzinfo=KST),
                   end=datetime(d.year, d.month, d.day, 11, 0, tzinfo=KST), room_ids=["london@r"])
        for d in _thursdays(n)
    ]
    return SeriesCandidate(series_id="s1", summary="ERP2팀 주간회의", organizer_email="kim@x.com",
                           organizer_self=True, occurrences=occs, people=list(people), room_ids=["london@r"])


def _busy(series, room_blocks=None, people_blocks=None):
    """room_blocks: {(date, rid): [(HH:MM, HH:MM)]}"""
    from domains.schedule_management.series_rebook import WeekBusy, _at

    out = {}
    for occ in series.occurrences:
        out[occ.monday] = WeekBusy(rooms={r["calendar_resource_id"]: [] for r in ROOMS}, people={p: [] for p in series.people})
    for (d, rid), spans in (room_blocks or {}).items():
        m = d - timedelta(days=d.weekday())
        out[m].rooms[rid].extend((_at(d, a), _at(d, b)) for a, b in spans)
    for (d, p), spans in (people_blocks or {}).items():
        m = d - timedelta(days=d.weekday())
        out[m].people[p].extend((_at(d, a), _at(d, b)) for a, b in spans)
    return out


def _solve(series, busy, rooms=None, spec=None, **kw):
    from domains.schedule_management.series_rebook import candidate_rooms, solve

    spec = spec or _spec()
    usable = candidate_rooms(spec, series, rooms or ROOMS)
    return solve(spec, series, usable, busy, now=NOW, **kw)


def test_candidate_rooms_excludes_source_and_other_region_and_small():
    from domains.schedule_management.series_rebook import candidate_rooms

    tiny = _room("Closet", 4, "closet@r")
    names = [r["display_name"] for r in candidate_rooms(_spec(), _series(), ROOMS + [tiny])]
    assert names == ["Paris", "Hawaii", "Seoul"]


def test_all_weeks_free_picks_thursday_afternoon_fixed():
    s = _series()
    plan = _solve(s, _busy(s))
    k = plan.chosen.key
    assert k.weekday == 3 and k.start == "13:00"  # 오후창에서 원래 시각(10시)에 가장 가까운 시각
    # 9명 회의 → 20인실(Seoul)이 정원 충족이라 6인실보다 앞선다.
    assert plan.chosen.room_name == "Seoul"
    assert plan.chosen.available_weeks == 4
    assert not plan.exceptions and not plan.unassigned


def test_room_conflict_one_week_prefers_consistent_room():
    # Seoul 이 한 주 오후에 막히면, 예외를 두기보다 매주 되는 다른 회의실(6인실)로 고정한다.
    s = _series()
    d = _thursdays()[2]
    plan = _solve(s, _busy(s, room_blocks={(d, "seoul@r"): [("13:00", "18:00")]}))
    assert plan.chosen.key.start == "13:00" and plan.chosen.available_weeks == 4
    assert plan.chosen.room_name in ("Paris", "Hawaii")
    assert not plan.exceptions


def _thursday_blocked_week3():
    d = _thursdays()[2]
    return d, {(d, r["calendar_resource_id"]): [("08:00", "19:00")] for r in ROOMS}


def test_one_thursday_blocked_keeps_thursday_with_exception_week():
    s = _series()
    d, blocks = _thursday_blocked_week3()
    plan = _solve(s, _busy(s, room_blocks=blocks))
    # 한 주 막혔다고 시리즈 전체를 수요일로 옮기지 않는다 — 목요일 오후 고정 + 그 주만 예외
    assert plan.chosen.key.weekday == 3 and plan.chosen.key.start == "13:00"
    assert plan.chosen.available_weeks == 3
    exc = plan.exceptions
    assert len(exc) == 1 and exc[0].monday == d - timedelta(days=3)
    assert exc[0].key.weekday in (2, 4) and exc[0].key.start == "13:00"
    # 매주 되는(수/금) 조합도 대안으로 보인다
    assert any(o.available_weeks == 4 for o in plan.options)


def test_afternoon_all_blocked_falls_back_to_morning_same_weekday():
    s = _series()
    blocks = {(d, r["calendar_resource_id"]): [("13:00", "18:00")] for d in _thursdays() for r in ROOMS}
    plan = _solve(s, _busy(s, room_blocks=blocks))
    assert plan.chosen.key.weekday == 3
    assert plan.chosen.key.start == "10:00"  # 아무 시간 tier 에서 원래 시각


def test_thursday_fully_blocked_moves_to_other_weekday_afternoon():
    s = _series()
    blocks = {(d, r["calendar_resource_id"]): [("08:00", "19:00")] for d in _thursdays() for r in ROOMS}
    plan = _solve(s, _busy(s, room_blocks=blocks))
    assert plan.chosen.key.weekday in (2, 4)  # 목요일과 가장 가까운 수/금
    assert plan.chosen.key.start == "13:00"
    assert plan.chosen.available_weeks == 4


def test_first_week_before_effective_from_becomes_exception():
    # 목요일 전면 차단 → 수요일로 이동. 첫 주 수요일(9/30)은 적용일(10/1) 이전이라 그 주만 따로.
    s = _series()
    blocks = {(d, r["calendar_resource_id"]): [("08:00", "19:00")] for d in _thursdays() for r in ROOMS}
    blocks.update({(date(2026, 10, 2), r["calendar_resource_id"]): [("08:00", "19:00")] for r in ROOMS})
    plan = _solve(s, _busy(s, room_blocks=blocks))
    assert plan.chosen.key.weekday == 2
    assert plan.chosen.available_weeks == 3
    assert plan.weeks[0].key is None  # 첫 주는 수·목·금 모두 불가 (월·화는 적용일 이전)


def test_unassignable_week():
    s = _series()
    d = _thursdays()[1]
    m = d - timedelta(days=3)
    blocks = {(m + timedelta(days=i), r["calendar_resource_id"]): [("08:00", "19:00")] for i in range(5) for r in ROOMS}
    plan = _solve(s, _busy(s, room_blocks=blocks))
    assert [w.monday for w in plan.unassigned] == [m]


def test_own_occurrence_not_counted_as_attendee_conflict():
    s = _series()
    blocks = {(d, r["calendar_resource_id"]): [("13:00", "18:00")] for d in _thursdays() for r in ROOMS}
    # 모든 참석자가 원래 회의(10~11시) 때문에 바쁘다 — 자기 자신이므로 충돌로 보지 않는다.
    people = {(d, p): [("10:00", "11:00")] for d in _thursdays() for p in s.people}
    plan = _solve(s, _busy(s, room_blocks=blocks, people_blocks=people))
    assert plan.chosen.key.start == "10:00"
    assert all(not w.conflicts for w in plan.weeks)


def test_attendee_conflicts_are_warnings_and_tiebreak():
    s = _series()
    people = {(d, "lee@x.com"): [("13:00", "14:00")] for d in _thursdays()}
    plan = _solve(s, _busy(s, people_blocks=people), name_of={"lee@x.com": "이영희"})
    # 13:00·13:30 은 이영희(13~14시)와 겹치므로 같은 tier 의 겹치지 않는 14:00 이 이긴다.
    assert plan.chosen.key.start == "14:00"
    plan2 = _solve(s, _busy(s, people_blocks={(d, p): [("08:00", "19:00")] for d in _thursdays() for p in s.people}))
    # 전원이 하루 종일 바빠도 회의실 우선 — 후보는 나오고 경고만 붙는다.
    assert plan2.chosen is not None and plan2.weeks[0].conflicts


def test_afternoon_missing_one_week_still_preferred_over_full_morning():
    s = _series()
    d = _thursdays()[3]
    blocks = {(d, r["calendar_resource_id"]): [("13:00", "18:00")] for r in ROOMS}
    plan = _solve(s, _busy(s, room_blocks=blocks))
    # 오후 3/4주 + 예외 1주가 오전 4/4주보다 우선(오후 고정이 최선이라는 요청)
    assert plan.chosen.tier == 0 and plan.chosen.available_weeks == 3
    assert plan.exceptions[0].key.start == "10:00"  # 그 주는 같은 요일 원래 시각
    assert any(o.available_weeks == 4 for o in plan.options)


def test_mostly_blocked_afternoon_loses_to_coverage():
    s = _series(n=10)
    days = _thursdays(10)
    blocks = {(d, r["calendar_resource_id"]): [("13:00", "18:00")] for d in days[:5] for r in ROOMS}
    plan = _solve(s, _busy(s, room_blocks=blocks))
    assert plan.chosen.available_weeks == 10 and plan.chosen.key.start == "10:00"


def test_choice_is_respected():
    from domains.schedule_management.series_rebook import SlotKey

    s = _series()
    pick = SlotKey(3, "15:00", "paris@r")
    plan = _solve(s, _busy(s), choice=pick)
    assert plan.chosen.key == pick
    assert all(w.key == pick for w in plan.weeks)


# ---------- 시리즈 그룹핑 ----------
def test_group_series_scores_team_weekday_room():
    from domains.schedule_management.series_rebook import group_series

    def ev(sid, summary, d, rid):
        return {"id": f"{sid}_{d}", "recurring_event_id": sid, "summary": summary,
                "start": f"{d}T10:00:00+09:00", "end": f"{d}T11:00:00+09:00",
                "attendees": [{"email": "kim@x.com"}, {"email": rid, "resource": True}],
                "organizer_email": "kim@x.com", "organizer_self": True}

    events = [
        ev("pc2", "PC2팀 주간회의", "2026-10-01", "paris@r"),
        ev("erp2", "ERP2팀 주간회의", "2026-10-01", "london@r"),
        ev("erp2", "ERP2팀 주간회의", "2026-10-08", "london@r"),
        {"id": "single", "summary": "ERP2팀 주간회의 특별", "start": "2026-10-02T10:00:00+09:00",
         "end": "2026-10-02T11:00:00+09:00"},
    ]
    cands = group_series(events, spec=_spec(), rooms=ROOMS)
    assert [c.series_id for c in cands] == ["erp2", "pc2"]
    assert len(cands[0].occurrences) == 2


# ---------- 실행 ----------
def test_rrule_helpers():
    from domains.schedule_management.series_rebook import _set_rrule_part, _until_before

    r = _set_rrule_part("RRULE:FREQ=WEEKLY;BYDAY=TH;UNTIL=20270101T000000Z", "BYDAY", "WE")
    assert r.startswith("RRULE:") and "BYDAY=WE" in r and "BYDAY=TH" not in r and "UNTIL=" in r
    assert _until_before(datetime(2026, 10, 1, 10, 0, tzinfo=KST)) == "20261001T005959Z"


def test_apply_split_creates_new_series_then_truncates_and_patches_exceptions(monkeypatch):
    import domains.schedule_management.series_rebook as sr

    s = _series()
    d, blocks = _thursday_blocked_week3()
    plan = _solve(s, _busy(s, room_blocks=blocks))
    calls = []
    master = {"summary": "ERP2팀 주간회의", "start": {"dateTime": "2026-01-08T10:00:00+09:00"},
              "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
              "attendees": [{"email": "kim@x.com"}, {"email": "london@r", "resource": True}],
              "conferenceData": {"conferenceId": "abc-defg-hij"}}
    monkeypatch.setattr(sr, "is_dry_run", lambda: False)
    monkeypatch.setattr(sr, "get_event", lambda **kw: CalendarResult(ok=True, created_event=master))

    def fake_create(**kw):
        calls.append(("create", kw))
        return CalendarResult(ok=True, created_event={"id": "new1", "htmlLink": "https://cal/new1"})

    def fake_patch(**kw):
        calls.append(("patch", kw))
        return CalendarResult(ok=True, created_event={"id": kw["event_id"]})

    def fake_instances(**kw):
        return CalendarResult(ok=True, events=[
            {"id": f"new1_{t:%Y%m%d}", "start": f"{t}T13:00:00+09:00"} for t in _thursdays()
        ])

    monkeypatch.setattr(sr, "create_event", fake_create)
    monkeypatch.setattr(sr, "patch_event", fake_patch)
    monkeypatch.setattr(sr, "list_instances", fake_instances)

    res = sr.apply_rebook(s, plan, rooms=ROOMS, access_token="tok")
    assert res.ok and res.mode == "split", res
    kinds = [c[0] for c in calls]
    assert kinds[0] == "create"  # 새 시리즈를 먼저 만든다
    create_kw = calls[0][1]
    assert create_kw["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TH"]
    assert create_kw["resource_emails"] == ["seoul@r"]
    assert create_kw["conference_data"] == {"conferenceId": "abc-defg-hij"}
    assert create_kw["start_iso"].startswith("2026-10-01T13:00")
    trunc = calls[1][1]
    assert trunc["event_id"] == "s1" and "UNTIL=20261001T005959Z" in trunc["recurrence"][0]
    exc_patch = [c[1] for c in calls[2:]]
    assert len(exc_patch) == 1 and exc_patch[0]["event_id"] == f"new1_{d:%Y%m%d}"
    assert exc_patch[0]["resource_emails"] == ["seoul@r"]
    assert exc_patch[0]["start_iso"][:10] in ("2026-10-14", "2026-10-16")  # 그 주만 수/금


def test_apply_falls_back_to_instances_when_count_rule(monkeypatch):
    import domains.schedule_management.series_rebook as sr

    s = _series()
    plan = _solve(s, _busy(s))
    master = {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH;COUNT=30"], "start": {"dateTime": "2026-01-08T10:00:00+09:00"}}
    patched = []
    monkeypatch.setattr(sr, "get_event", lambda **kw: CalendarResult(ok=True, created_event=master))
    monkeypatch.setattr(sr, "create_event", lambda **kw: pytest.fail("COUNT 규칙은 분할하지 않는다"))
    monkeypatch.setattr(sr, "patch_event", lambda **kw: patched.append(kw) or CalendarResult(ok=True))
    res = sr.apply_rebook(s, plan, rooms=ROOMS, access_token="tok")
    assert res.mode == "instances" and res.ok
    assert sorted(p["event_id"] for p in patched) == sorted(o.instance_id for o in s.occurrences)


def test_apply_create_failure_falls_back_without_truncating(monkeypatch):
    import domains.schedule_management.series_rebook as sr

    s = _series()
    plan = _solve(s, _busy(s))
    master = {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"], "start": {"dateTime": "2026-01-08T10:00:00+09:00"}}
    patched = []
    monkeypatch.setattr(sr, "get_event", lambda **kw: CalendarResult(ok=True, created_event=master))
    monkeypatch.setattr(sr, "create_event", lambda **kw: CalendarResult(ok=False, error_kind="calendar_auth_error"))
    monkeypatch.setattr(sr, "patch_event", lambda **kw: patched.append(kw) or CalendarResult(ok=True))
    res = sr.apply_rebook(s, plan, rooms=ROOMS, access_token="tok")
    assert res.mode == "instances"
    assert all("recurrence" not in p or p["recurrence"] is None for p in patched)  # 기존 시리즈는 끊지 않음


# ---------- 카드 ----------
def test_card_renders_and_hides_confirm_for_non_organizer():
    from domains.schedule_management.series_cards import build_series_rebook_card

    s = _series()
    plan = _solve(s, _busy(s))
    rooms_by_id = {r["calendar_resource_id"]: r["display_name"] for r in ROOMS}
    card = build_series_rebook_card(spec=_spec(), series=s, plan=plan, rooms_by_id=rooms_by_id, others=[], can_edit=True)
    text = str(card)
    # 첫 카드에는 실행 버튼이 없고 '변경 내용 검토'(최종 확인 단계로 이동)만 있다.
    assert "sm_series_review" in text and "sm_series_confirm" not in text
    assert "London" in text and "이렇게 이해했어요" in text
    s.organizer_self = False
    s.organizer_email = "boss@x.com"
    card2 = build_series_rebook_card(spec=_spec(), series=s, plan=plan, rooms_by_id=rooms_by_id, others=[], can_edit=False)
    assert "sm_series_review" not in str(card2) and "boss@x.com" in str(card2)


# ---------- 핸들러 end-to-end (네트워크 전부 mock) ----------
LINKED = {"kim@x.com", "lee@x.com", "park@x.com"}


def _wire(monkeypatch, *, organizer_self=True, organizer="kim@x.com", linked=LINKED):
    import domains.schedule_management.event_auth as ea
    import domains.schedule_management.series_flow as sf
    import domains.schedule_management.series_rebook as sr

    for mod in (sf, ea):
        monkeypatch.setattr(mod, "is_oauth_linked", lambda email: email in linked)
        monkeypatch.setattr(mod, "get_user_access_token", lambda email: f"tok:{email}" if email in linked else None)
    monkeypatch.setattr(sf, "get_all_members", lambda: MEMBERS)
    monkeypatch.setattr(sf, "get_rooms", lambda: ROOMS)
    monkeypatch.setattr("domains.schedule_management.series_spec._llm_extract", lambda *a, **k: {})
    events = [
        {"id": f"s1_{d:%Y%m%d}", "recurring_event_id": "s1", "summary": "ERP2팀 주간회의",
         "start": f"{d}T10:00:00+09:00", "end": f"{d}T11:00:00+09:00",
         "attendees": [{"email": "kim@x.com"}, {"email": "lee@x.com"}, {"email": "london@r", "resource": True}],
         "organizer_email": organizer, "organizer_self": organizer_self, "status": "confirmed"}
        for d in _thursdays(13)
    ]
    monkeypatch.setattr(sr, "list_events_all", lambda **kw: CalendarResult(ok=True, events=events))
    monkeypatch.setattr(sr, "_freebusy_chunked", lambda ids, **kw: {i: [] for i in ids})
    monkeypatch.setattr(sr, "list_group_room_bookings", lambda **kw: [])
    sr._busy_cache.clear()
    backups = []
    monkeypatch.setattr(sf, "snapshot_events", lambda ids, **kw: [{"id": i} for i in ids])
    monkeypatch.setattr(sf, "save_backup", lambda **kw: backups.append(kw) or "bk1")
    monkeypatch.setattr(sf, "mark_backup", lambda *a, **kw: None)
    return sr, backups


def _find_action(card, function):
    for sec in card["cardsV2"][0]["card"]["sections"]:
        for w in sec["widgets"]:
            for b in (w.get("buttonList") or {}).get("buttons") or []:
                action = b.get("onClick", {}).get("action", {})
                if action.get("function") == function:
                    return {x["key"]: x["value"] for x in action.get("parameters") or []}
    return None


def test_handler_three_step_flow_review_then_confirm(monkeypatch):
    from domains.schedule_management.handler import (
        handle_schedule_management,
        handle_schedule_management_action,
    )
    import domains.schedule_management.series_flow as sf

    sr, backups = _wire(monkeypatch)
    applied = {}
    monkeypatch.setattr(sf, "apply_rebook", lambda series, plan, **kw: applied.update(plan=plan, **kw)
                        or sr.ApplyResult(ok=True, mode="split", done=["ok"], created_event_ids=["new1"]))
    event = {"user": {"email": "kim@x.com", "displayName": "김철수"}}

    # ① 검토 카드 — 아무것도 실행되지 않는다
    card = handle_schedule_management(USER_REQUEST, event)
    assert _find_action(card, "sm_series_confirm") is None
    p = _find_action(card, "sm_series_review")
    wd, start, _ = p["choice"].split("|")
    assert p["series_id"] == "s1" and wd == "3" and "13:00" <= start < "18:00"
    assert not applied and not backups

    # ② 최종 확인 카드 — 여전히 실행 안 됨
    review = handle_schedule_management_action(invoked_function="sm_series_review", parameters=p,
                                               form_inputs={}, chat_event=event)
    assert "최종 확인" in str(review) and "백업" in str(review)
    p2 = _find_action(review, "sm_series_confirm")
    assert p2 == p and not applied and not backups

    # ③ 최종 확인 → 백업 저장 후 실행, 결과에 되돌리기
    out = handle_schedule_management_action(invoked_function="sm_series_confirm", parameters=p2,
                                            form_inputs={}, chat_event=event)
    assert backups and backups[0]["acting_as"] == "kim@x.com" and backups[0]["operation"] == "series_rebook"
    assert applied["access_token"] == "tok:kim@x.com"
    assert applied["plan"].chosen.key.encode() == p["choice"] and len(applied["plan"].weeks) == 13
    assert _find_action(out, "sm_evt_restore_review") == {"backup_id": "bk1"}


def test_non_organizer_teammate_runs_with_organizer_token(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action
    import domains.schedule_management.series_flow as sf

    sr, backups = _wire(monkeypatch, organizer_self=False)
    used = {}
    monkeypatch.setattr(sf, "apply_rebook", lambda series, plan, **kw: used.update(kw)
                        or sr.ApplyResult(ok=True, mode="split", done=["ok"]))
    params = {"spec": _spec().to_json(), "series_id": "s1", "choice": "3|15:00|paris@r"}
    out = handle_schedule_management_action(invoked_function="sm_series_confirm", parameters=params,
                                            form_inputs={}, chat_event={"user": {"email": "lee@x.com"}})
    assert used["access_token"] == "tok:kim@x.com"  # 주최자 토큰으로 대신 실행
    assert backups[0]["requester_email"] == "lee@x.com" and backups[0]["acting_as"] == "kim@x.com"
    assert "주최자(kim@x.com) 권한" in str(out)


def test_outsider_is_blocked(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action
    import domains.schedule_management.series_flow as sf

    _wire(monkeypatch, organizer_self=False)
    monkeypatch.setattr(sf, "apply_rebook", lambda *a, **k: pytest.fail("참석자·팀원이 아니면 실행하지 않는다"))
    params = {"spec": _spec().to_json(), "series_id": "s1", "choice": "3|15:00|paris@r"}
    out = handle_schedule_management_action(invoked_function="sm_series_confirm", parameters=params,
                                            form_inputs={}, chat_event={"user": {"email": "park@x.com"}})
    assert "같은 팀이거나 참석자" in str(out)


def test_backup_failure_aborts(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action
    import domains.schedule_management.series_flow as sf

    _wire(monkeypatch)

    def boom(**kw):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(sf, "save_backup", boom)
    monkeypatch.setattr(sf, "apply_rebook", lambda *a, **k: pytest.fail("백업 실패 시 실행하지 않는다"))
    params = {"spec": _spec().to_json(), "series_id": "s1", "choice": "3|15:00|paris@r"}
    out = handle_schedule_management_action(invoked_function="sm_series_confirm", parameters=params,
                                            form_inputs={}, chat_event={"user": {"email": "kim@x.com"}})
    assert "백업에 실패" in str(out)


def test_handler_requires_oauth(monkeypatch):
    import domains.schedule_management.series_flow as sf
    from domains.schedule_management.handler import handle_schedule_management

    monkeypatch.setattr(sf, "is_oauth_linked", lambda email: False)
    monkeypatch.setattr("domains.schedule_management.handler._oauth_url", lambda ev: "https://oauth")
    out = handle_schedule_management(USER_REQUEST, {"user": {"email": "kim@x.com"}})
    assert "내 캘린더 연결" in str(out) and "https://oauth" in str(out)
