"""Atlassian Cloud user lookup — 이메일 → accountId 변환.

GDPR 이후 Confluence CQL 은 ``contributor.email`` 을 받지 않고 ``contributor.accountid`` 만
받아서, 매 호출마다 한 번 accountId 를 해석해야 함. 같은 Atlassian 인스턴스의 Jira REST
``/rest/api/3/user/search?query=<email>`` 가 이메일이 privacy 로 가려진 사용자도 server-side
매칭해 주므로 가장 안정적.

결과는 ``firestore.confluence_users`` 에 캐싱 (이메일은 거의 변하지 않음).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from api._auth.confluence_basic import get_confluence_auth
from firestore import confluence_users as cache

logger = logging.getLogger(__name__)


def resolve_account_id(user_email: str) -> dict[str, str] | None:
    """이메일 → ``{"account_id", "display_name"}`` (Firestore 캐시 우선).

    Returns:
        dict — 해석 성공.
        None — 매칭되는 사용자 없음 / API 오류 / 인증 실패.
    """
    if not user_email:
        return None

    cached = cache.get_cached(user_email)
    if cached and cached.get("account_id"):
        return {
            "account_id": str(cached.get("account_id") or ""),
            "display_name": str(cached.get("display_name") or ""),
        }

    try:
        auth = get_confluence_auth(user_email)
    except Exception as e:
        logger.warning("confluence resolve_account_id auth 실패: %s", e)
        return None

    base_url = auth["base_url"]
    query = urllib.parse.urlencode({"query": user_email, "maxResults": "5"})
    url = f"{base_url}/rest/api/3/user/search?{query}"

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", auth["auth_header"])
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.warning(
            "confluence resolve_account_id HTTP %s email=%s body=%s",
            e.code,
            user_email,
            body,
        )
        return None
    except Exception as e:
        logger.warning("confluence resolve_account_id 실패 email=%s: %s", user_email, e)
        return None

    data = _safe_json_list(payload)
    needle = user_email.strip().lower()
    chosen: dict[str, Any] | None = None
    for item in data:
        if not isinstance(item, dict):
            continue
        item_email = str(item.get("emailAddress") or "").strip().lower()
        if item_email and item_email == needle:
            chosen = item
            break
    if chosen is None and data:
        # email 이 privacy 로 가려졌어도 query 매칭 결과 1건이면 그걸 채택.
        first = data[0] if isinstance(data[0], dict) else None
        if first and first.get("accountId"):
            chosen = first

    if not chosen:
        logger.warning("confluence resolve_account_id 매칭 실패 email=%s", user_email)
        return None

    account_id = str(chosen.get("accountId") or "")
    display_name = str(chosen.get("displayName") or "")
    if not account_id:
        return None

    try:
        cache.save(user_email, account_id=account_id, display_name=display_name)
    except Exception as e:
        logger.warning("confluence_users 캐시 저장 실패 email=%s: %s", user_email, e)

    return {"account_id": account_id, "display_name": display_name}


def resolve_email_by_account_id(account_id: str) -> str | None:
    """accountId → email 역해석.

    expert_finder 용. Confluence 응답에는 ``version.by.accountId`` 만 들어오는데,
    OAuth 동의자 풀과 합집합하려면 email 이 필요. 이미 ``resolve_account_id`` 호출로
    Firestore ``confluence_users/{email}`` 에 ``account_id`` 필드 캐시된 사람은
    inverted lookup 한 방으로 해결.

    캐시에 없는 사람은 None 반환 — 호출 측에서 displayName 폴백 (team_members 매칭) 으로 우회.
    Atlassian user-by-accountId API 도 emailAddress 가 Workspace privacy 정책으로
    가려져 빈 값 오는 경우가 많아 ROI 낮음.

    Returns:
        str — 매칭된 email.
        None — 캐시에 없음 (호출 측 폴백 필요).
    """
    if not account_id:
        return None
    from firestore.writes import get_client

    db = get_client()
    try:
        docs = (
            db.collection("confluence_users")
            .where("account_id", "==", account_id)
            .limit(1)
            .stream()
        )
        for snap in docs:
            email = (snap.id or "").strip()
            if email:
                return email
    except Exception as e:
        logger.warning("resolve_email_by_account_id 캐시 lookup 실패: %s", e)
    return None


def _safe_json_list(payload: str) -> list[Any]:
    try:
        data = json.loads(payload or "[]")
    except Exception:
        return []
    if isinstance(data, list):
        return data
    return []
