"""기존 일정 수정·삭제 / 대리 실행 권한 / 백업·되돌리기 (오프라인, 캘린더·Firestore mock)."""

from __future__ import annotations

from datetime import datetime

import pytest

from domains.schedule_management.calendar_client import KST, CalendarResult

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
MEMBERS = [
    {"name": "김철수", "email": "kim@x.com", "team_id": "ERP2", "team_name": "ERP2팀", "nickname": []},
    {"name": "이영희", "email": "lee@x.com", "team_id": "ERP2", "team_name": "ERP2팀", "nickname": []},
    {"name": "박민수", "email": "park@x.com", "team_id": "PC2", "team_name": "PC2팀", "nickname": []},
]
ROOMS = [
    {"display_name": "London", "name": "VNTG 서울 London (8)", "capacity": 8, "calendar_resource_id": "london@r", "office": "seoul"},
    {"display_name": "Paris", "name": "VNTG 서울 Paris (6)", "capacity": 6, "calendar_resource_id": "paris@r", "office": "seoul"},
]


def _req(text):
    from domains.schedule_management.event_edit import extract_edit_request

    return extract_edit_request(text, members=MEMBERS, now=NOW, use_llm=False)


# ---------- 해석 ----------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("내일 3시 ERP2 회의 4시로 옮겨줘",
         {"operation": "modify", "keyword": "ERP2", "target_date": "2026-10-02", "target_time": "15:00", "new_start": "16:00"}),
        ("주간회의 10/15만 취소해줘",
         {"operation": "delete", "keyword": "주간회의", "target_date": "2026-10-15", "scope": "this"}),
        ("주간회의 11월부터 없애줘",
         {"operation": "delete", "target_date": "2026-11-01", "scope": "following"}),
        ("목요일 주간회의 회의실 Paris로 바꿔줘", {"operation": "modify", "new_room": "Paris"}),
        ("내일 고객사 미팅 모레 2시로 미뤄줘",
         {"keyword": "고객사", "target_date": "2026-10-02", "new_date": "2026-10-03", "new_start": "14:00"}),
        ("내일 10시 회의 삭제해줘", {"operation": "delete", "target_date": "2026-10-02", "target_time": "10:00"}),
        ("ERP2팀 주간회의 1시간 반으로 늘려줘", {"operation": "modify", "duration_minutes": 90}),
        ("10/8 주간회의 전체 삭제", {"operation": "delete", "scope": "all", "target_date": "2026-10-08"}),
    ],
)
def test_extract_edit_request(text, expected):
    req = _req(text)
    for k, v in expected.items():
        assert getattr(req, k) == v, (k, getattr(req, k))


def test_llm_only_fills_gaps_and_is_validated():
    from domains.schedule_management.event_edit import extract_edit_request

    req = extract_edit_request(
        "그 회의 좀 바꿔줘", members=MEMBERS, now=NOW, room_names=["Paris"],
        llm=lambda p: {"keyword": "디자인 리뷰", "target_date": "2020-01-01", "new_start": "25:00",
                       "new_room": "Paris", "scope": "weird"},
    )
    assert req.keyword == "디자인 리뷰" and req.target_date == "" and req.new_start == ""
    assert req.new_room == "Paris" and req.scope == ""


@pytest.mark.parametrize(
    "text,route",
    [
        ("내일 3시 ERP2 회의 4시로 옮겨줘", "edit"),
        ("주간회의 10/15만 취소해줘", "edit"),
        ("회의실 예약 취소해줘", "edit"),
        ("ERP2팀 주간회의 10월부터 London 대신 다른 회의실로 쭉 옮겨줘", "series"),
        ("내일 회의실 예약해줘", None),
        ("주간회의 팀 등록", None),
        ("지난주 회의록 찾아서 요약해줘", None),
    ],
)
def test_routing(text, route):
    from domains.routing.patterns import looks_like_event_edit, looks_like_series_rebook

    got = "series" if looks_like_series_rebook(text) else "edit" if looks_like_event_edit(text) else None
    assert got == route


def test_edit_routes_to_schedule_management():
    from domains.routing.intent import deterministic_intent

    assert deterministic_intent("내일 3시 ERP2 회의 4시로 옮겨줘") == "schedule_management"
    assert deterministic_intent("주간회의 10/15만 취소해줘") == "schedule_management"


# ---------- 대상 찾기 ----------
def _ev(eid, start, end, *, summary="ERP2팀 주간회의", sid="", rooms=("london@r",), organizer="kim@x.com", me=True):
    return {"id": eid, "summary": summary, "start": start, "end": end, "recurring_event_id": sid,
            "attendees": [{"email": "kim@x.com"}, {"email": "lee@x.com"}] + [{"email": r, "resource": True} for r in rooms],
            "organizer_email": organizer, "organizer_self": me, "status": "confirmed"}


def test_find_target_events_filters_and_dedupes_series(monkeypatch):
    import domains.schedule_management.event_edit as ee

    events = [
        _ev("a_1", "2026-10-08T10:00:00+09:00", "2026-10-08T11:00:00+09:00", sid="a"),
        _ev("a_2", "2026-10-15T10:00:00+09:00", "2026-10-15T11:00:00+09:00", sid="a"),
        _ev("x", "2026-10-09T10:00:00+09:00", "2026-10-09T11:00:00+09:00", summary="점심"),
        {"id": "allday", "summary": "ERP2팀 주간회의", "start": "2026-10-10", "end": "2026-10-11"},
    ]
    monkeypatch.setattr(ee, "list_events_all", lambda **kw: CalendarResult(ok=True, events=events))
    req = ee.EditRequest(keyword="주간회의")
    found = ee.find_target_events(req, access_token="t", rooms=ROOMS, now=NOW)
    assert [e["id"] for e in found] == ["a_1"]  # 날짜 미지정 → 반복 일정은 가장 가까운 회차 하나


# ---------- 실행 ----------
@pytest.fixture
def calls(monkeypatch):
    import domains.schedule_management.event_edit as ee

    log = []
    master = {"id": "s1", "summary": "ERP2팀 주간회의", "start": {"dateTime": "2026-01-08T10:00:00+09:00"},
              "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
              "attendees": [{"email": "kim@x.com"}, {"email": "london@r", "resource": True}],
              "conferenceData": {"conferenceId": "abc"}}
    monkeypatch.setattr(ee, "get_event", lambda **kw: CalendarResult(ok=True, created_event=master))
    monkeypatch.setattr(ee, "patch_event", lambda **kw: log.append(("patch", kw)) or CalendarResult(ok=True))
    monkeypatch.setattr(ee, "delete_event", lambda **kw: log.append(("delete", kw)) or CalendarResult(ok=True))
    monkeypatch.setattr(ee, "create_event", lambda **kw: log.append(("create", kw))
                        or CalendarResult(ok=True, created_event={"id": "new1"}))
    return log


OCC = _ev("s1_20261015T010000Z", "2026-10-15T10:00:00+09:00", "2026-10-15T11:00:00+09:00", sid="s1")


def _plan(**kw):
    from domains.schedule_management.event_edit import EditPlan

    base = dict(operation="modify", scope="this", event_id=OCC["id"], series_id="s1")
    base.update(kw)
    return EditPlan(**base)


def test_modify_this_patches_instance_with_room(calls):
    from domains.schedule_management.event_edit import apply_edit

    res = apply_edit(OCC, _plan(new_start="16:00", new_room_id="paris@r"), rooms=ROOMS, access_token="t")
    assert res.ok
    (kind, kw), = calls
    assert kind == "patch" and kw["event_id"] == OCC["id"]
    assert kw["start_iso"].startswith("2026-10-15T16:00") and kw["end_iso"].startswith("2026-10-15T17:00")
    assert kw["resource_emails"] == ["paris@r"] and kw["send_updates"] == "all"


def test_delete_this_and_all(calls):
    from domains.schedule_management.event_edit import apply_edit

    apply_edit(OCC, _plan(operation="delete"), rooms=ROOMS, access_token="t")
    apply_edit(OCC, _plan(operation="delete", scope="all"), rooms=ROOMS, access_token="t")
    assert [c[1]["event_id"] for c in calls] == [OCC["id"], "s1"]


def test_delete_following_truncates_rrule(calls):
    from domains.schedule_management.event_edit import apply_edit

    res = apply_edit(OCC, _plan(operation="delete", scope="following"), rooms=ROOMS, access_token="t")
    assert res.ok
    (kind, kw), = calls
    assert kind == "patch" and kw["event_id"] == "s1"
    assert kw["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TH;UNTIL=20261015T005959Z"]


def test_modify_all_shifts_weekday(calls):
    from domains.schedule_management.event_edit import apply_edit

    res = apply_edit(OCC, _plan(scope="all", new_date="2026-10-14", new_start="14:00"), rooms=ROOMS, access_token="t")
    assert res.ok
    kw = calls[0][1]
    assert kw["event_id"] == "s1" and kw["start_iso"].startswith("2026-01-07T14:00")  # 원본 첫 회차도 하루 당김
    assert "BYDAY=WE" in kw["recurrence"][0]


def test_modify_following_creates_then_truncates(calls):
    from domains.schedule_management.event_edit import apply_edit

    res = apply_edit(OCC, _plan(scope="following", new_start="15:00"), rooms=ROOMS, access_token="t")
    assert res.ok and res.created_event_ids == ["new1"]
    assert [c[0] for c in calls] == ["create", "patch"]
    create = calls[0][1]
    assert create["start_iso"].startswith("2026-10-15T15:00") and create["resource_emails"] == ["london@r"]
    assert create["conference_data"] == {"conferenceId": "abc"}
    assert "UNTIL=20261015T005959Z" in calls[1][1]["recurrence"][0]


def test_check_room_free_ignores_own_slot(monkeypatch):
    import domains.schedule_management.calendar_client as cc
    from domains.schedule_management.event_edit import check_room_free

    busy = {"london@r": [{"start": "2026-10-15T09:30:00+09:00", "end": "2026-10-15T11:00:00+09:00"}]}
    monkeypatch.setattr(cc, "freebusy_query", lambda **kw: CalendarResult(ok=True, busy=busy))
    # 10:30 으로 30분 미루기 — 10~11시는 자기 자신, 9:30~10:00 은 다른 예약이지만 안 겹침
    assert check_room_free(OCC, _plan(new_start="10:30"), access_token="t") is True
    # 9:30 으로 당기기 — 9:30~10:00 다른 예약과 겹침
    assert check_room_free(OCC, _plan(new_start="09:30"), access_token="t") is False


# ---------- 권한 ----------
@pytest.mark.parametrize(
    "requester,organizer_self,linked,expect",
    [
        ("kim@x.com", True, {"kim@x.com"}, ("self", "kim@x.com")),
        ("lee@x.com", False, {"kim@x.com", "lee@x.com"}, ("organizer", "kim@x.com")),  # 참석자+팀원
        ("lee@x.com", False, {"lee@x.com"}, ("guest", "lee@x.com")),  # 주최자 미연결 → 내 토큰으로 시도
        ("park@x.com", False, {"kim@x.com", "park@x.com"}, ("not_permitted", "")),  # 다른 팀·비참석자
    ],
)
def test_resolve_write_auth(monkeypatch, requester, organizer_self, linked, expect):
    import domains.schedule_management.event_auth as ea

    monkeypatch.setattr(ea, "is_oauth_linked", lambda e: e in linked)
    monkeypatch.setattr(ea, "get_user_access_token", lambda e: f"tok:{e}" if e in linked else None)
    auth = ea.resolve_write_auth(requester_email=requester, organizer_email="kim@x.com",
                                 organizer_self=organizer_self and requester == "kim@x.com",
                                 attendee_emails=["kim@x.com", "lee@x.com"], members=MEMBERS)
    assert (auth.via or auth.error, auth.acting_as) == expect


def test_same_team_non_attendee_is_permitted():
    from domains.schedule_management.event_auth import is_permitted

    members = MEMBERS + [{"name": "최", "email": "choi@x.com", "team_id": "ERP2"}]
    assert is_permitted(requester_email="choi@x.com", organizer_email="kim@x.com", attendee_emails=[], members=members)


# ---------- 백업·복원 ----------
def test_restore_deletes_created_then_restores_master_first(monkeypatch):
    import json

    import domains.schedule_management.event_backup as eb

    log = []
    monkeypatch.setattr(eb, "delete_event", lambda **kw: log.append(("delete", kw["event_id"])) or CalendarResult(ok=True))
    monkeypatch.setattr(eb, "patch_event_raw", lambda **kw: log.append(("patch", kw["event_id"], kw["body"])) or CalendarResult(ok=True))
    inst = {"id": "s1_x", "summary": "회의", "start": {"dateTime": "2026-10-15T10:00:00+09:00"},
            "attendees": [{"email": "kim@x.com", "responseStatus": "accepted"}, {"email": "london@r", "resource": True}]}
    master = {"id": "s1", "summary": "회의", "recurrence": ["RRULE:FREQ=WEEKLY"], "start": {"dateTime": "x"}}
    backup = {"calendar_id": "primary", "events_json": json.dumps([inst, master]), "created_event_ids": ["new1"]}
    res = eb.restore_backup(backup, access_token="t")
    assert res.ok
    assert [x[:2] for x in log] == [("delete", "new1"), ("patch", "s1"), ("patch", "s1_x")]
    body = log[2][2]
    assert body["status"] == "confirmed"
    assert body["attendees"] == [{"email": "kim@x.com"}, {"email": "london@r", "resource": True}]


# ---------- 흐름(편집 → 최종 확인 → 실행 → 되돌리기) ----------
def _wire(monkeypatch, *, requester="kim@x.com", organizer_self=True):
    import domains.schedule_management.event_auth as ea
    import domains.schedule_management.event_edit_flow as ef
    import domains.schedule_management.series_flow as sf

    linked = {"kim@x.com", "lee@x.com", "park@x.com"}
    for mod in (sf, ea, ef):
        if hasattr(mod, "is_oauth_linked"):
            monkeypatch.setattr(mod, "is_oauth_linked", lambda e: e in linked)
        monkeypatch.setattr(mod, "get_user_access_token", lambda e: f"tok:{e}" if e in linked else None)
    monkeypatch.setattr(ef, "get_all_members", lambda: MEMBERS)
    monkeypatch.setattr(ef, "get_rooms", lambda: ROOMS)
    occ = dict(OCC, organizer_self=organizer_self)
    monkeypatch.setattr(ef, "find_target_events", lambda req, **kw: [occ])
    monkeypatch.setattr(ef, "load_event", lambda eid, **kw: occ)
    monkeypatch.setattr(ef, "check_room_free", lambda *a, **k: True)
    state = {"backups": [], "applied": [], "marks": []}
    monkeypatch.setattr(ef, "snapshot_events", lambda ids, **kw: [{"id": i} for i in ids])
    monkeypatch.setattr(ef, "save_backup", lambda **kw: state["backups"].append(kw) or "bk9")
    monkeypatch.setattr(ef, "mark_backup", lambda *a, **kw: state["marks"].append((a, kw)))

    def fake_apply(event, plan, **kw):
        from domains.schedule_management.event_edit import EditResult

        state["applied"].append((plan, kw))
        return EditResult(ok=True, done=["changed"])

    monkeypatch.setattr(ef, "apply_edit", fake_apply)
    return ef, state, {"user": {"email": requester}}


def _action(card, function):
    for sec in card["cardsV2"][0]["card"]["sections"]:
        for w in sec["widgets"]:
            for b in (w.get("buttonList") or {}).get("buttons") or []:
                a = b.get("onClick", {}).get("action", {})
                if a.get("function") == function:
                    return {x["key"]: x["value"] for x in a.get("parameters") or []}
    return None


def _form(**kw):
    return {k: {"stringInputs": {"value": [v]}} for k, v in kw.items()}


def test_edit_flow_requires_review_then_final_confirm(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management, handle_schedule_management_action

    monkeypatch.setattr("domains.schedule_management.event_edit_flow.extract_edit_request",
                        lambda text, **kw: _req(text))  # LLM 없이 규칙만
    ef, state, ev = _wire(monkeypatch)
    card = handle_schedule_management("10/15 주간회의 4시로 옮겨줘", ev)
    assert _action(card, "sm_evt_apply") is None  # 첫 카드에서는 실행 불가
    p = _action(card, "sm_evt_review")
    review = handle_schedule_management_action(
        invoked_function="sm_evt_review", parameters=p,
        form_inputs=_form(new_date="2026-10-15", new_start="16:00", new_end="17:00", new_room_id="paris@r",
                          new_title="", scope="this"),
        chat_event=ev,
    )
    assert not state["applied"] and not state["backups"]
    text = str(review)
    assert "변경 전" in text and "변경 후" in text and "16:00~17:00" in text and "Paris" in text
    p2 = _action(review, "sm_evt_apply")
    assert p2["new_start"] == "16:00" and p2["new_room_id"] == "paris@r" and p2["scope"] == "this"
    out = handle_schedule_management_action(invoked_function="sm_evt_apply", parameters=p2, form_inputs={}, chat_event=ev)
    assert state["backups"][0]["operation"] == "edit_modify"
    assert state["applied"][0][1]["access_token"] == "tok:kim@x.com"
    assert _action(out, "sm_evt_restore_review") == {"backup_id": "bk9"}


def test_edit_review_rejects_no_change(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action

    ef, state, ev = _wire(monkeypatch)
    from domains.schedule_management.event_edit import EditRequest

    p = {"req": EditRequest(operation="modify").to_json(), "event_id": OCC["id"]}
    out = handle_schedule_management_action(
        invoked_function="sm_evt_review", parameters=p,
        form_inputs=_form(new_date="2026-10-15", new_start="10:00", new_end="11:00", new_room_id="", new_title=""),
        chat_event=ev,
    )
    assert "바뀐 내용이 없어요" in str(out)


def test_teammate_edit_uses_organizer_token_and_outsider_blocked(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action

    ef, state, _ = _wire(monkeypatch, organizer_self=False)
    params = {**_plan(operation="delete").params(), "req": "{}"}
    handle_schedule_management_action(invoked_function="sm_evt_apply", parameters=params, form_inputs={},
                                      chat_event={"user": {"email": "lee@x.com"}})
    assert state["applied"][0][1]["access_token"] == "tok:kim@x.com"
    assert state["backups"][0]["requester_email"] == "lee@x.com" and state["backups"][0]["acting_as"] == "kim@x.com"
    out = handle_schedule_management_action(invoked_function="sm_evt_apply", parameters=params, form_inputs={},
                                            chat_event={"user": {"email": "park@x.com"}})
    assert len(state["applied"]) == 1 and "같은 팀이거나 참석자" in str(out)


def test_restore_flow_review_then_restore_with_acting_token(monkeypatch):
    from domains.schedule_management.handler import handle_schedule_management_action

    ef, state, _ = _wire(monkeypatch)
    backup = {"requester_email": "lee@x.com", "acting_as": "kim@x.com", "operation": "edit_modify",
              "summary": "ERP2팀 주간회의", "event_ids": ["e1"], "status": "applied"}
    monkeypatch.setattr(ef, "get_backup", lambda bid: dict(backup))
    restored = []

    def fake_restore(b, *, access_token):
        from domains.schedule_management.event_backup import RestoreResult

        restored.append(access_token)
        return RestoreResult(ok=True, done=["복원"])

    monkeypatch.setattr(ef, "restore_backup", fake_restore)
    ev = {"user": {"email": "lee@x.com"}}
    review = handle_schedule_management_action(invoked_function="sm_evt_restore_review",
                                               parameters={"backup_id": "bk9"}, form_inputs={}, chat_event=ev)
    assert not restored and _action(review, "sm_evt_restore") == {"backup_id": "bk9"}
    out = handle_schedule_management_action(invoked_function="sm_evt_restore",
                                            parameters={"backup_id": "bk9"}, form_inputs={}, chat_event=ev)
    assert restored == ["tok:kim@x.com"] and "되돌렸어요" in str(out)
    denied = handle_schedule_management_action(invoked_function="sm_evt_restore",
                                               parameters={"backup_id": "bk9"}, form_inputs={},
                                               chat_event={"user": {"email": "park@x.com"}})
    assert len(restored) == 1 and "되돌릴 수 없어요" in str(denied)
