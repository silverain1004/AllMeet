"""Confluence Cloud Basic auth — `config/team_list.global` 에서 자격 정보 read.

봇 계정 (user_email) + API token 조합으로 Basic auth 헤더 생성.
api_confluence/pages.py 등이 이 헬퍼로 헤더만 받아 REST 호출.
"""

from __future__ import annotations

import base64

from firestore.team_config import get_token_for


def get_confluence_auth(user_email: str = "") -> dict[str, str]:
    """Confluence base URL + Basic auth 헤더 dict.

    Args:
        user_email: 미래에 팀별 토큰 사용 시 분기용. 현재는 글로벌 1개라 무시.

    Returns:
        ``{"base_url": "https://vntg.atlassian.net", "auth_header": "Basic ..."}``

    Raises:
        RuntimeError: 글로벌 설정이 비어 있으면 ``confluence_global_config_incomplete``.
    """
    token_info = get_token_for(user_email or "")
    base_url = token_info.get("url") or ""
    user = token_info.get("user_email") or ""
    api_token = token_info.get("api_token") or ""
    if not (base_url and user and api_token):
        raise RuntimeError("confluence_global_config_incomplete")
    raw = f"{user}:{api_token}".encode("utf-8")
    return {
        "base_url": base_url,
        "auth_header": f"Basic {base64.b64encode(raw).decode('ascii')}",
    }
