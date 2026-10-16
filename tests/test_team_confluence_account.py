"""팀별 Confluence 계정 오버라이드 — 주간회의 페이지 작성자를 팀마다 다르게."""
from __future__ import annotations

import base64
from datetime import datetime
from unittest.mock import MagicMock, patch

_GLOBAL = {
    "url": "https://vntg.atlassian.net",
    "user_email": "global@vntgcorp.com",
    "api_token": "global-token",
}


def _decode(auth_header: str) -> str:
    return base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")


def test_get_confluence_auth_uses_global_by_default():
    from api._auth import confluence_basic as cb

    with patch.object(cb, "get_token_for", return_value=dict(_GLOBAL)):
        auth = cb.get_confluence_auth()
    assert _decode(auth["auth_header"]) == "global@vntgcorp.com:global-token"


def test_get_confluence_auth_account_override():
    from api._auth import confluence_basic as cb

    with patch.object(cb, "get_token_for", return_value=dict(_GLOBAL)):
        auth = cb.get_confluence_auth(account_email="me@vntgcorp.com", api_token="my-token")
    assert auth["base_url"] == "https://vntg.atlassian.net"
    assert _decode(auth["auth_header"]) == "me@vntgcorp.com:my-token"


def test_get_confluence_auth_partial_override_falls_back_to_global():
    from api._auth import confluence_basic as cb

    with patch.object(cb, "get_token_for", return_value=dict(_GLOBAL)):
        auth = cb.get_confluence_auth(account_email="me@vntgcorp.com", api_token="")
    assert _decode(auth["auth_header"]) == "global@vntgcorp.com:global-token"


def test_team_confluence_client_uses_team_account():
    from domains.weekly_meeting import page_creator as pc

    cfg = {"confluence_user_email": "me@vntgcorp.com", "confluence_api_token": "my-token"}
    with patch.object(pc, "ConfluenceClient") as cls:
        pc._team_confluence_client("PC2", cfg)
    cls.assert_called_once_with(account_email="me@vntgcorp.com", api_token="my-token")


def test_team_confluence_client_without_override_uses_global():
    from domains.weekly_meeting import page_creator as pc

    with patch.object(pc, "ConfluenceClient") as cls:
        pc._team_confluence_client("MES2", {"user_email": "global@vntgcorp.com", "api_token": "x"})
    cls.assert_called_once_with()


def test_create_from_template_creates_page_with_team_account():
    from domains.weekly_meeting import page_creator as pc

    cfg = {
        "team_name": "PC2팀",
        "root_pages": ["123"],
        "template_page_id": "999",
        "confluence_space_key": "PC2",
        "team_members": [],
        "confluence_user_email": "me@vntgcorp.com",
        "confluence_api_token": "my-token",
    }
    client = MagicMock()
    client.get_page.return_value = {"body": {"storage": {"value": "<p>{{TEAM}}</p>"}}}
    client.create_page.return_value = {"id": "new1"}

    with patch.object(pc, "_find_reference_date", return_value=datetime(2026, 10, 12)), patch.object(
        pc, "ConfluenceClient", return_value=client
    ) as cls, patch.object(
        pc, "get_previous_weekly_report_content", return_value={"page_id": None}
    ), patch.object(
        pc, "_resolve_target_folder", return_value="q1"
    ), patch.object(
        pc, "_find_page_in_folder", return_value=None
    ):
        result = pc._create_from_template("PC2", cfg)

    cls.assert_called_once_with(account_email="me@vntgcorp.com", api_token="my-token")
    client.create_page.assert_called_once()
    assert "페이지 생성 완료" in result


def test_team_confluence_client_falls_back_to_global_when_team_auth_fails():
    """팀 계정 토큰 만료·철회(401 등) → 글로벌 계정으로 생성."""
    from domains.weekly_meeting import page_creator as pc

    team_client, global_client = MagicMock(), MagicMock()
    team_client.is_authenticated.return_value = False
    cfg = {"confluence_user_email": "me@vntgcorp.com", "confluence_api_token": "expired"}
    with patch.object(pc, "ConfluenceClient", side_effect=[team_client, global_client]) as cls:
        client = pc._team_confluence_client("PC2", cfg)

    assert client is global_client
    assert cls.call_args_list[1] == ((), {})


def test_team_confluence_client_keeps_team_account_when_auth_ok():
    from domains.weekly_meeting import page_creator as pc

    team_client = MagicMock()
    team_client.is_authenticated.return_value = True
    cfg = {"confluence_user_email": "me@vntgcorp.com", "confluence_api_token": "ok"}
    with patch.object(pc, "ConfluenceClient", return_value=team_client) as cls:
        client = pc._team_confluence_client("PC2", cfg)

    assert client is team_client
    cls.assert_called_once()


def test_is_authenticated_false_on_401_and_network_error():
    from api.confluence import client as cc

    with patch.object(cc, "get_confluence_auth", return_value={"base_url": "https://x", "auth_header": "Basic y"}):
        c = cc.ConfluenceClient()
    with patch.object(cc.requests, "get", return_value=MagicMock(status_code=401)):
        assert c.is_authenticated() is False
    with patch.object(cc.requests, "get", side_effect=cc.requests.ConnectionError("down")):
        assert c.is_authenticated() is False
    with patch.object(cc.requests, "get", return_value=MagicMock(status_code=200)):
        assert c.is_authenticated() is True
