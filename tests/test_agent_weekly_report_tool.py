"""find_team_weekly_report 도구 — 케이스 3·4(주간보고 검색 품질) 회귀 방지.

키워드 검색 대신 팀 폴더·날짜 기준으로 최신 주간보고 페이지를 정확히 찾는다.
외부 API(Confluence)·Firestore 는 모두 monkeypatch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


def _fake_client(title: str = "2026-06-19 PC2팀 주간회의") -> MagicMock:
    c = MagicMock()
    c.get_page.return_value = {"title": title}
    c._base_url = "https://vntg.atlassian.net"
    return c


def test_find_team_weekly_report_found_with_team_name():
    from domains.agent import registry

    with patch(
        "firestore.team_config.get_team_from_index",
        return_value={
            "name": "PC2",
            "report_root_page_id": "2275213400",
            "space_key": "PC2",
            "root_pages": [],
        },
    ), patch(
        "api.confluence.previous_report.get_latest_weekly_report_page_id_for_team_root",
        return_value="999",
    ) as latest, patch(
        "api.confluence.pages.get_page_body", return_value="이번 주 주요 공지와 프로젝트 현황..."
    ), patch(
        "api.confluence.client.ConfluenceClient", return_value=_fake_client()
    ):
        out = registry._run_find_team_weekly_report(team_name="PC2팀", requester_email="u@x.com")

    assert out["ok"] is True and out["found"] is True
    assert out["page_id"] == "999"
    assert out["content"].startswith("이번 주")
    assert out["title"] == "2026-06-19 PC2팀 주간회의"
    # web_link 은 root 폴더(2275213400)가 아니라 페이지 ID(999)를 가리킨다.
    assert out["web_link"] == "https://vntg.atlassian.net/wiki/spaces/PC2/pages/999"
    # 팀 폴더 root(2275213400)로 최신 페이지를 조회했는지
    assert latest.call_args.args[1] == "2275213400"


def test_find_team_weekly_report_uses_requester_email_and_root_pages():
    from domains.agent import registry

    with patch("firestore.team_config.find_team_by_email", return_value="PC2") as fbe, patch(
        "firestore.team_config.get_team_from_index",
        return_value={"name": "PC2", "root_pages": [{"page_id": "111"}], "report_root_page_id": ""},
    ), patch(
        "api.confluence.previous_report.get_latest_weekly_report_page_id_for_team_root",
        return_value="222",
    ) as latest, patch(
        "api.confluence.pages.get_page_body", return_value="본문"
    ), patch(
        "api.confluence.client.ConfluenceClient", return_value=_fake_client(title="T")
    ):
        out = registry._run_find_team_weekly_report(requester_email="u@x.com")

    fbe.assert_called_once_with("u@x.com")
    assert out["found"] is True
    assert out["page_id"] == "222"
    # root_pages[0] 가 report_root_page_id 보다 우선
    assert latest.call_args.args[1] == "111"


def test_find_team_weekly_report_no_team_returns_not_found():
    from domains.agent import registry

    out = registry._run_find_team_weekly_report()
    assert out["ok"] is True
    assert out["found"] is False


def test_find_team_weekly_report_no_report_root_returns_not_found():
    from domains.agent import registry

    with patch(
        "firestore.team_config.get_team_from_index",
        return_value={"name": "PC2", "root_pages": [], "report_root_page_id": ""},
    ):
        out = registry._run_find_team_weekly_report(team_name="PC2팀")
    assert out["ok"] is True
    assert out["found"] is False
    assert "report_root" in out["detail"]


def test_find_team_weekly_report_registered_readonly():
    from domains.agent.tools import get_tool

    tool = get_tool("find_team_weekly_report")
    assert tool is not None
    assert tool.side_effect is False


def test_planner_prompt_steers_to_weekly_report_tool():
    from domains.agent.prompts import build_planner_prompt

    prompt = build_planner_prompt(
        user_message="PC2팀 최근 주간보고 요약해줘",
        catalog=[{"name": "find_team_weekly_report", "description": "..."}],
        today="2026-06-23",
        user_name="U",
        user_email="u@x.com",
    )
    assert "find_team_weekly_report" in prompt


def test_dry_run_chain_find_weekly_report_then_summarize(monkeypatch):
    """케이스 3·4 happy path — find_team_weekly_report → generate_content($ref content)."""
    from domains.agent import orchestrator

    steps = [
        {"n": 1, "tool": "find_team_weekly_report", "args": {"team_name": "PC2팀"}, "side_effect": False},
        {
            "n": 2,
            "tool": "generate_content",
            "args": {
                "instruction": "핵심 요약",
                "source": {"$ref": "1.content"},
                "content_type": "summary",
                "require_source": True,
            },
            "side_effect": False,
        },
    ]

    with patch(
        "firestore.team_config.get_team_from_index",
        return_value={"name": "PC2", "report_root_page_id": "2275213400", "space_key": "PC2"},
    ), patch(
        "api.confluence.previous_report.get_latest_weekly_report_page_id_for_team_root",
        return_value="999",
    ), patch(
        "api.confluence.pages.get_page_body", return_value="공지: ... 프로젝트 현황: ..."
    ), patch(
        "api.confluence.client.ConfluenceClient", return_value=_fake_client()
    ), patch(
        "domains.agent._llm.generate_text", return_value="- 핵심 1\n- 핵심 2"
    ):
        out = orchestrator.run_loop(goal="PC2팀 주간보고 요약", steps=steps)

    assert out["outcome"] == orchestrator.OUTCOME_DONE
    assert out["results"][1]["found"] is True
    assert "핵심 1" in out["results"][2]["content"]
