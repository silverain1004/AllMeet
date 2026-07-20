"""신규 에이전트 도구 4종 + Tool.examples few-shot 배선 테스트.

이미 구현된 역량(회의실·Drive·작성자별 수정)을 도구로 노출한 것이라, 원본 함수는 mock 한다.
"""

from __future__ import annotations

from unittest.mock import patch

from api.confluence.pages import ListPagesResult
from api.drive.files import ListFilesResult


# ---------------------------------------------------------------------------
# 등록 + side_effect
# ---------------------------------------------------------------------------


def test_new_tools_registered_readonly():
    from domains.agent.tools import get_tool

    for name in (
        "list_meeting_rooms",
        "search_drive_files",
        "list_confluence_pages_by_author",
        "list_drive_files_by_author",
    ):
        tool = get_tool(name)
        assert tool is not None, name
        assert tool.side_effect is False


# ---------------------------------------------------------------------------
# 각 래퍼 정규화
# ---------------------------------------------------------------------------


def test_list_meeting_rooms_maps_resource_id_to_calendar_id():
    from domains.agent import registry

    fake_rooms = [
        {
            "name": "3층 대회의실",
            "display_name": "3층 대회의실",
            "capacity": 10,
            "equipment": ["TV", "화이트보드"],
            "location": "3F",
            "calendar_resource_id": "room-123@resource.calendar.google.com",
        }
    ]
    with patch("domains.schedule_management.rooms_store.get_rooms", return_value=fake_rooms):
        out = registry._run_list_meeting_rooms()
    assert out["ok"] is True and out["found"] is True and out["count"] == 1
    room = out["rooms"][0]
    assert room["calendar_id"] == "room-123@resource.calendar.google.com"
    assert room["name"] == "3층 대회의실"


def test_search_drive_files_normalizes_and_requires_query():
    from domains.agent import registry

    assert registry._run_search_drive_files()["error_kind"] == "empty_query"
    with patch(
        "api.drive.files.list_files_by_query",
        return_value=ListFilesResult(ok=True, files=[{"id": "f1", "name": "벡터DB 정리.gdoc"}]),
    ):
        out = registry._run_search_drive_files(query="벡터DB")
    assert out["ok"] is True and out["found"] is True and out["count"] == 1
    assert out["files"][0]["name"] == "벡터DB 정리.gdoc"


def test_search_drive_files_propagates_error():
    from domains.agent import registry

    with patch(
        "api.drive.files.list_files_by_query",
        return_value=ListFilesResult(ok=False, error_kind="auth_error"),
    ):
        out = registry._run_search_drive_files(query="x")
    assert out["ok"] is False and out["error_kind"] == "auth_error"


def test_list_confluence_pages_by_author_requires_args_and_normalizes():
    from domains.agent import registry

    assert registry._run_list_confluence_pages_by_author(space_key="PC2")["error_kind"] == "missing_args"
    with patch(
        "api.confluence.pages.list_pages_modified",
        return_value=ListPagesResult(ok=True, pages=[{"id": "p1", "title": "주간회의"}]),
    ):
        out = registry._run_list_confluence_pages_by_author(
            space_key="PC2",
            user_email="me@corp.com",
            time_min="2026-06-01T00:00:00Z",
            time_max="2026-06-30T23:59:59Z",
        )
    assert out["ok"] is True and out["found"] is True
    assert out["pages"][0]["id"] == "p1"


def test_list_drive_files_by_author_requires_args():
    from domains.agent import registry

    assert registry._run_list_drive_files_by_author(time_min="t")["error_kind"] == "missing_args"
    with patch(
        "api.drive.files.list_files_modified",
        return_value=ListFilesResult(ok=True, files=[]),
    ):
        out = registry._run_list_drive_files_by_author(
            modified_by_email="me@corp.com",
            time_min="2026-06-17T00:00:00Z",
            time_max="2026-06-24T00:00:00Z",
        )
    assert out["ok"] is True and out["found"] is False and out["count"] == 0


# ---------------------------------------------------------------------------
# Tool.examples few-shot 배선
# ---------------------------------------------------------------------------


def test_examples_serialized_only_when_present():
    from domains.agent.tools import Tool, _serialize

    with_ex = Tool(name="x", description="d", examples=[{"when": "a", "args": {}}])
    without_ex = Tool(name="y", description="d")
    assert "examples" in _serialize(with_ex)
    assert "examples" not in _serialize(without_ex)


def test_score_tool_counts_example_when_text():
    from domains.agent.tools import Tool, _score_tool, _tokens

    tool = Tool(name="foo", description="bar", examples=[{"when": "벡터DB 자료 찾기", "args": {}}])
    # 'foo bar' 엔 없는 키워드가 example.when 으로 매칭돼 점수가 잡혀야 한다
    assert _score_tool(tool, _tokens("벡터DB")) >= 1


def test_registered_new_tools_expose_examples():
    from domains.agent.tools import get_tool

    assert get_tool("list_meeting_rooms").examples
    assert get_tool("search_drive_files").examples


# ---------------------------------------------------------------------------
# 회귀: 도구가 늘어도 generate_content 가 가지치기로 누락되지 않음
# ---------------------------------------------------------------------------


def test_select_catalog_default_budget_raised():
    """도구 확장(15→19)으로 가지치기가 켜지는 회귀를 막기 위해 default max_tools 를 상향했는지."""
    import inspect

    from domains.agent.tools import select_catalog

    default = inspect.signature(select_catalog).parameters["max_tools"].default
    assert default >= 20


def test_generate_content_not_pruned_within_budget():
    """예산 내(등록수 ≤ max_tools)에서는 키워드 미스에도 범용 도구가 살아있다.

    (전역 레지스트리는 다른 테스트의 더미 도구로 오염될 수 있어 절대 개수에 의존하지 않고,
    현재 등록 수를 budget 으로 줘 '가지치기 미발동' 경로를 검증한다.)
    """
    from domains.agent.tools import list_tools, select_catalog

    n = len(list_tools())
    names = [t["name"] for t in select_catalog("xyzzy 전혀 관련 없는 요청", max_tools=n)]
    assert "generate_content" in names


def test_select_catalog_retrieves_room_tool_by_keyword_when_pruning():
    from domains.agent.tools import select_catalog

    # 가지치기를 강제(작은 max_tools)해도 '회의실' 키워드로 list_meeting_rooms 회수
    names = [t["name"] for t in select_catalog("회의실 예약하고 싶어", max_tools=8)]
    assert "list_meeting_rooms" in names
