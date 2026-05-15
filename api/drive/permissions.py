"""Google Drive Shared Drive — 멤버십 검증 + 자동 권한 부여.

두 가지 케이스:
1. ``check_sa_member(drive_id)`` — 봇 SA 가 이미 그 Shared Drive 의 멤버인지 검증
   (drives.get 호출 → 200 = 멤버, 404 = 미멤버 또는 미존재)
2. ``grant_sa_reader(drive_id, granter_email)`` — granter 의 user OAuth 토큰으로
   봇 SA 를 reader 로 등록 (granter 가 Drive Manager 여야 함)

UX:
- handler 에서 저장 직전에 ``check_sa_member`` → 미멤버 시 ``grant_sa_reader`` 시도
- granter OAuth 미동의 → ``AuthRequiredError`` (호출 측에서 동의 카드로 유도)
- granter 가 Manager 아님 → 403 → 사용자에게 수동 추가 안내
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from api._auth.service_account import get_access_token
from api._auth.user_oauth import AuthRequiredError, get_user_access_token

logger = logging.getLogger(__name__)

_SA_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_USER_GRANT_SCOPE = "https://www.googleapis.com/auth/drive"


def check_sa_member(drive_id: str) -> dict[str, Any]:
    """봇 SA 가 ``drive_id`` 의 멤버인지 확인.

    Returns:
        ``{"ok": bool, "name": str, "error_kind": str}``
        - ok=True 면 ``name`` 채워짐 (Shared Drive 표시명)
        - ok=False 인 ``error_kind``:
          ``"not_found_or_not_member"`` (404) — 미멤버 또는 미존재
          ``"auth_error"`` (401/403) — SA 자격 문제
          ``"http_error"`` — 그 외
    """
    if not drive_id:
        return {"ok": False, "name": "", "error_kind": "drive_id_missing"}
    url = f"https://www.googleapis.com/drive/v3/drives/{urllib.parse.quote(drive_id)}?fields=id,name"
    try:
        token = get_access_token([_SA_SCOPE])
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"ok": True, "name": str(data.get("name") or ""), "error_kind": ""}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "name": "", "error_kind": "not_found_or_not_member"}
        if e.code in (401, 403):
            return {"ok": False, "name": "", "error_kind": "auth_error"}
        logger.warning("drives.get HTTP %s drive_id=%s", e.code, drive_id)
        return {"ok": False, "name": "", "error_kind": "http_error"}
    except Exception as e:
        logger.warning("drives.get 실패 drive_id=%s: %s", drive_id, e)
        return {"ok": False, "name": "", "error_kind": "http_error"}


def grant_sa_reader(*, drive_id: str, granter_email: str, sa_email: str) -> dict[str, Any]:
    """``granter_email`` 의 OAuth 토큰으로 ``sa_email`` 을 reader 로 추가.

    Args:
        drive_id: Shared Drive ID
        granter_email: Drive Manager 권한 보유자 (현재 챗 사용자)
        sa_email: 봇 SA 이메일

    Returns:
        ``{"ok": bool, "error_kind": str}``
        - ok=True — 권한 부여 성공 (또는 이미 멤버라 idempotent)
        - error_kind:
          ``"oauth_required"`` — granter OAuth 미동의 또는 drive scope 누락
          ``"forbidden"`` (403) — granter 가 Manager 아님
          ``"not_found"`` (404) — drive 미존재 또는 granter 도 멤버 아님
          ``"http_error"`` — 그 외
    """
    if not (drive_id and granter_email and sa_email):
        return {"ok": False, "error_kind": "missing_args"}

    try:
        token = get_user_access_token(granter_email, [_USER_GRANT_SCOPE])
    except AuthRequiredError as e:
        logger.info("grant_sa_reader OAuth 필요 granter=%s reason=%s", granter_email, e.reason)
        return {"ok": False, "error_kind": "oauth_required"}
    except Exception as e:
        logger.warning("grant_sa_reader OAuth 발급 실패 granter=%s: %s", granter_email, e)
        return {"ok": False, "error_kind": "oauth_required"}

    url = (
        f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(drive_id)}/permissions"
        "?supportsAllDrives=true&sendNotificationEmail=false"
    )
    body = json.dumps({"type": "user", "role": "reader", "emailAddress": sa_email}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return {"ok": True, "error_kind": ""}
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code == 403:
            logger.warning("grant_sa_reader 403 (granter Manager 아님?) drive_id=%s body=%s", drive_id, body_text)
            return {"ok": False, "error_kind": "forbidden"}
        if e.code == 404:
            logger.warning("grant_sa_reader 404 drive_id=%s body=%s", drive_id, body_text)
            return {"ok": False, "error_kind": "not_found"}
        logger.warning("grant_sa_reader HTTP %s drive_id=%s body=%s", e.code, drive_id, body_text)
        return {"ok": False, "error_kind": "http_error"}
    except Exception as e:
        logger.warning("grant_sa_reader 예외 drive_id=%s: %s", drive_id, e)
        return {"ok": False, "error_kind": "http_error"}
