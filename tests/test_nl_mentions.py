"""멘션 기반 파서 — 참석자 제외/팀 일괄 추가/회의실·지역 + 날짜·시간 파싱 엣지케이스."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

KST = timezone(timedelta(hours=9))

MEMBERS = [
    {"name": "김민수", "email": "minsu@x.com", "nickname": [], "team_id": "PC2", "team_name": "PC2팀"},
    {"name": "이지은", "email": "jieun@x.com", "nickname": [], "team_id": "PC2", "team_name": "PC2팀"},
    {"name": "박서준", "email": "seojun@x.com", "nickname": [], "team_id": "ERP2", "team_name": "ERP2팀"},
]
MEMBERS_WITH_IJI = MEMBERS + [
    {"name": "이지", "email": "iji@x.com", "nickname": [], "team_id": "PC2", "team_name": "PC2팀"},
]
MEMBERS_WITH_MES2 = MEMBERS + [
    {"name": "김민", "email": "kimmin@x.com", "nickname": ["mes2kim"], "team_id": "MES2", "team_name": "MES2팀"},
    {"name": "정준호", "email": "junho@x.com", "nickname": [], "team_id": "MES2", "team_name": "MES2팀"},
]


def _emails(msg: str, members=MEMBERS, sender: str = "") -> set[str]:
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state(msg, members=members, sender_email=sender)
    return {a["email"] for a in state["attendees"]}


@pytest.mark.parametrize(
    "msg, expected",
    [
        ("내일 3시 PC2팀 회의 잡아줘", set()),
        ("이지은이랑 회의는 말고 미팅", {"jieun@x.com"}),
        ("내일 3시 PC2팀 팀원들이랑 회의 잡아줘, 김민수는 빼고", {"jieun@x.com"}),
        ("PC2팀원들이랑 회의", {"minsu@x.com", "jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수만 빼고", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수씨는 제외", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수님은 빼고", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수도 빼고", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수님 빼고", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수는 빼주고", {"jieun@x.com"}),
        ("PC2팀 팀원들, minsu@x.com은 빼고", {"jieun@x.com"}),
        ("PC2팀 팀원들이랑 회의, 김민수랑 이지은은 빼고", set()),
        ("PC2팀 팀원들이랑 회의, 김민수, 이지은 빼고", set()),
        ("PC2팀 팀원들이랑 회의, 김민수와 이지은 빼고", set()),
        ("내일 3시 ERP2팀 말고 PC2팀 팀원들이랑 회의", {"minsu@x.com", "jieun@x.com"}),
        ("내일 3시 ERP2팀 빼고 PC2팀 팀원들이랑 회의", {"minsu@x.com", "jieun@x.com"}),
        ("내일 3시 PC2팀 팀원들 빼고 이지은만 회의", {"jieun@x.com"}),
        (
            "내일 3시 PC2팀 팀원들이랑 ERP2팀 박서준이랑 회의",
            {"minsu@x.com", "jieun@x.com", "seojun@x.com"},
        ),
        ("내일 3시 PC2팀이랑 ERP2팀 전체 회의", {"minsu@x.com", "jieun@x.com", "seojun@x.com"}),
        ("내일 ERP 이슈 점검 2시에 팀원들이랑 회의", set()),
        ("내일 3시 전체 회의, 노트북 PC 2대 준비", set()),
        ("내일 3시 pc2lead@x.com 모두 초대 회의", {"pc2lead@x.com"}),
        ("내일 3시 PC2팀 팀원 김민수랑 회의", {"minsu@x.com"}),
        ("내일 3시 PC2팀 회의, 전체적으로 30분만", set()),
        ("stranger@x.com 빼고 김민수랑 회의", {"minsu@x.com"}),
        ("내일 3시 seoul.kim@x.com 초대해서 회의", {"seoul.kim@x.com"}),
        ("PC2팀 회의 잡아줘, 팀원들 모두 초대", {"minsu@x.com", "jieun@x.com"}),
    ],
)
def test_attendee_resolution(msg, expected):
    assert _emails(msg) == expected


def test_exclusion_does_not_hit_prefix_named_member():
    """'이지은은 빼고'가 '이지'까지 제외하지 않는다."""
    assert _emails("PC2팀 팀원들이랑 회의, 이지은은 빼고", MEMBERS_WITH_IJI) == {"minsu@x.com", "iji@x.com"}


def test_our_team_requires_collective_marker():
    assert _emails("우리팀 팀원들이랑 회의", sender="minsu@x.com") == {"minsu@x.com", "jieun@x.com"}
    assert _emails("우리팀 회의", sender="minsu@x.com") == set()
    assert _emails("우리팀 팀원들이랑 회의", sender="nobody@x.com") == set()


@pytest.mark.parametrize(
    "msg, room, region",
    [
        ("내일 3시 서울로, T-Room 말고 회의", "", "seoul"),
        ("내일 3시 서울에서 오는 손님과 군산 회의실 회의", "", "gunsan"),
        ("내일 3시 서울 군산 둘 다 가능", "", ""),
        ("내일 3시 Paris 말고 Bali로", "Bali", "seoul"),
        ("내일 3시 V-Room 말고 T-Room으로", "T-Room", "gunsan"),
        ("내일 3시 발리로 잡아줘", "Bali", "seoul"),
        ("내일 3시 런던 말고 파리", "Paris", "seoul"),
        ("내일 3시 seoul.kim@x.com 초대해서 회의", "", ""),
        ("내일 3시 7층으로 회의", "", "seoul"),
        ("내일 3시 17층 회의", "", ""),
    ],
)
def test_room_and_region(msg, room, region):
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state(msg, members=MEMBERS)
    assert state["room_name_keyword"] == room
    assert state["room_region"] == region


def test_box_team_resolver_is_word_boundary_safe():
    from domains.schedule_management.handler import _find_team_members
    from domains.schedule_management.mentions import exact_member_matches

    assert _find_team_members("PC2팀", MEMBERS) and len(_find_team_members("PC2팀", MEMBERS)) == 2
    assert _find_team_members("PC20", MEMBERS) == []
    assert _find_team_members("erp2.lead", MEMBERS) == []
    assert _find_team_members("PC 2", MEMBERS) == []
    assert _find_team_members("mes2kim", MEMBERS_WITH_MES2) == []
    assert [m["email"] for m in exact_member_matches("mes2kim", MEMBERS_WITH_MES2)] == ["kimmin@x.com"]


@pytest.fixture
def monday(monkeypatch):
    fixed = datetime(2026, 9, 21, 9, 0, tzinfo=KST)  # 월요일
    from domains.schedule_management import conversation

    monkeypatch.setattr(conversation, "_now_kst", lambda: fixed)
    return fixed


@pytest.mark.parametrize(
    "msg, expected",
    [
        ("다음주 화요일 3시 회의", "2026-09-29"),
        ("다음 주 화요일 3시 회의", "2026-09-29"),
        ("다다음주 화요일 3시 회의", "2026-10-06"),
        ("이번주 금요일 3시 회의", "2026-09-25"),
        ("금요일 3시 회의", "2026-09-25"),
        ("월요일 3시 회의", "2026-09-28"),
        ("내일모레 10시 회의", "2026-09-23"),
        ("내일 10시 회의", "2026-09-22"),
        ("9/30 10시 회의", "2026-09-30"),
    ],
)
def test_dates(monday, msg, expected):
    from domains.schedule_management.conversation import extract_compose_state

    assert extract_compose_state(msg, members=[])["meeting_date"] == expected


@pytest.mark.parametrize(
    "msg, time, end, duration",
    [
        ("내일 3시 30분에 회의", "15:30", "", 0),
        ("내일 3시 회의 1시간 반", "15:00", "", 90),
        ("내일 3시 회의 2시간", "15:00", "", 120),
        ("내일 30분만 회의", "", "", 30),
        ("내일 3시부터 5시까지 회의실 잡아줘", "15:00", "17:00", 120),
        ("내일 오후 3시~5시 회의", "15:00", "17:00", 120),
        ("내일 15:00-16:30 회의", "15:00", "16:30", 90),
        ("내일 오전 10시부터 11시 30분까지 회의", "10:00", "11:30", 90),
    ],
)
def test_times_and_durations(msg, time, end, duration):
    from domains.schedule_management.conversation import extract_compose_state

    state = extract_compose_state(msg, members=[])
    assert state["meeting_time"] == time
    assert state["meeting_end_time"] == end
    assert int(state.get("duration_minutes") or 0) == duration
    if end:
        assert state["duration_mode"] == "custom"


@pytest.mark.parametrize(
    "msg, expected",
    [
        ("내일 점심 먹고 회의", "13:00"),
        ("내일 퇴근 전에 잠깐 회의", "17:00"),
        ("내일 오전 중에 회의", "10:00"),
        ("내일 오후 늦게 회의", "16:00"),
        ("내일 오후 회의", "14:00"),
    ],
)
def test_daypart_defaults(msg, expected):
    from domains.schedule_management.conversation import extract_compose_state

    assert extract_compose_state(msg, members=[])["meeting_time"] == expected


def test_find_slot_flag_and_headcount():
    from domains.schedule_management.conversation import extract_compose_state
    from domains.schedule_management.rooms import _attendee_count_for_state

    state = extract_compose_state("PC2팀 팀원들 다 되는 시간에 내일 1시간 회의, 5명", members=MEMBERS)
    assert state["find_slot"] is True
    assert state["duration_minutes"] == 60
    assert state["attendee_count"] == 4
    assert state["attendee_headcount"] == 5
    # 추천기는 버킷(4)이 아니라 실제 인원(5) 기준
    assert _attendee_count_for_state(state) == 5


def test_merge_carries_end_time_headcount_and_find_slot():
    from domains.schedule_management.compose_state import compose_state_from, empty_compose_state, state_to_button_params
    from domains.schedule_management.handler import _merge_extracted_state

    out = _merge_extracted_state(
        empty_compose_state(),
        {
            "meeting_time": "15:00",
            "meeting_end_time": "17:00",
            "duration_minutes": 120,
            "duration_mode": "custom",
            "attendee_count": 4,
            "attendee_headcount": 5,
            "find_slot": True,
        },
    )
    assert out["meeting_end_time"] == "17:00"
    assert out["duration_mode"] == "custom"
    assert out["attendee_headcount"] == 5
    assert out["find_slot"] is True
    # 버튼 파라미터 라운드트립
    back = compose_state_from(state_to_button_params(out), {})
    assert back["attendee_headcount"] == 5
    assert back["find_slot"] is True
