"""OAuth 동의 callback 처리 (Phase 2).

흐름:
1. 사용자가 챗에서 ``wm_oauth_link`` 카드 클릭 → 동의 URL 발급
2. 브라우저에서 동의 → Google 이 ``OAUTH_REDIRECT_URI`` 로 redirect (?code=...&state=...)
3. 이 모듈이 그 GET 요청 받아 token 교환 → ``oauth_tokens/{email}`` 저장 → 멤버 sync
4. 사용자에게 "연결 완료" 페이지 응답.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from api.chat.messages import post_message_to_space
from config.settings import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI
from firestore.oauth_tokens import save_token
from firestore.team_member_oauth_sync import sync_oauth_status

logger = logging.getLogger(__name__)

# 이 스코프들은 OAuth consent screen 등록 + Client ID 발급 시 함께 화이트리스트.
# drive (full) 는 Shared Drive 에 봇 SA 를 reader 로 등록(permissions.create) 하기 위해 필요.
# readonly 로는 권한 부여 호출이 invalid_scope 로 거부됨.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "openid",
    "email",
]


def encode_state(*, user_email: str, space_name: str = "") -> str:
    """state 직렬화 — 동의 후 같은 스페이스에 인사 메시지를 push 하기 위함.

    포맷: ``email:<email>|space:<spaces/XXX>`` (공백 없는 ``|`` 구분). 단순 텍스트라 nonce
    검증은 없음 — 운영에선 server-side session 권장.
    """
    parts = [f"email:{user_email}"]
    s = (space_name or "").strip()
    if s:
        parts.append(f"space:{s}")
    return "|".join(parts)


def build_authorization_url(*, user_email_hint: str = "", state: str = "") -> str:
    """동의 URL 생성. 카드에서 사용자에게 이 URL 열도록 안내."""
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # refresh_token 항상 받도록
        "include_granted_scopes": "true",
    }
    if user_email_hint:
        params["login_hint"] = user_email_hint
    if state:
        params["state"] = state
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def handle_oauth_callback(request: Any) -> tuple[str, int, dict[str, str]]:
    """``GET /oauth/callback`` — Google 의 redirect 처리.

    state 파라미터에 user_email 인코딩 (단순화 — 운영은 nonce + Firestore session).
    """
    if not (OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET and OAUTH_REDIRECT_URI):
        return _html_response("OAuth client 가 설정되지 않았어요. (운영자: 환경변수 확인)", 500)

    code = request.args.get("code") or ""
    error = request.args.get("error") or ""
    state = request.args.get("state") or ""

    if error:
        logger.warning("OAuth 동의 거부: %s", error)
        return _html_response(f"동의가 취소되었어요. ({error})", 400)
    if not code:
        return _html_response("인증 코드가 없어요. 다시 시도해 주세요.", 400)

    # state 에서 user_email + space_name 추출 (운영은 별도 session storage 권장)
    user_email, space_name = _decode_state(state)

    # Token 교환
    token_data = _exchange_code(code)
    if not token_data:
        return _html_response("토큰 교환에 실패했어요. 잠시 후 다시 시도해 주세요.", 500)

    refresh_token = token_data.get("refresh_token") or ""
    if not refresh_token:
        return _html_response(
            "refresh_token 이 발급되지 않았어요. Google 계정의 봇 권한을 먼저 해제 후 다시 시도해 주세요.",
            400,
        )

    if not user_email:
        # state 에 없으면 id_token 의 email claim 으로 폴백
        user_email = _email_from_id_token(token_data.get("id_token") or "")

    if not user_email:
        return _html_response(
            "사용자 이메일을 확인하지 못했어요. 챗에서 다시 '내 데이터 연결' 을 눌러주세요.",
            400,
        )

    save_token(
        user_email=user_email,
        refresh_token=refresh_token,
        scopes=token_data.get("scope", "").split() or SCOPES,
    )
    # 재동의면 새 refresh_token 으로 옛 access_token 이 폐기됨 — 이 인스턴스 캐시 즉시 제거.
    from api._auth.user_oauth import invalidate_cache

    invalidate_cache(user_email)
    synced = sync_oauth_status(user_email=user_email, status="linked")
    logger.info("OAuth 연결 완료: %s (sync=%d teams)", user_email, synced)

    # 챗 스페이스로 환영/능력 안내 메시지 push — 사용자가 챗 돌아왔을 때 자연스러운 다음 단계.
    if space_name:
        from domains.daily_chat.home_menu import build_home_menu_card

        pushed = post_message_to_space(
            space_name=space_name,
            payload=build_home_menu_card(),
        )
        if not pushed:
            logger.warning("OAuth 완료 후 챗 push 실패: space=%s", space_name)

    return _html_response(
        f"<h2>연결 완료 ✅</h2>"
        f"<p><b>{user_email}</b> 의 Gmail · 개인 Calendar · 내 Drive 가 봇과 연결되었어요.</p>"
        f"<p>이 창을 닫고 챗으로 돌아가 주세요. 챗에 안내 메시지가 도착해 있을 거예요.</p>",
        200,
    )


def _exchange_code(code: str) -> dict[str, Any] | None:
    """authorization code → access/refresh token 교환."""
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("OAuth token 교환 실패: %s", e)
        return None


def _decode_state(state: str) -> tuple[str, str]:
    """``encode_state`` 의 역변환. (user_email, space_name) 튜플. 누락은 빈 문자열."""
    email = ""
    space = ""
    for part in (state or "").split("|"):
        token = part.strip()
        if token.startswith("email:"):
            email = token[len("email:") :].strip()
        elif token.startswith("space:"):
            space = token[len("space:") :].strip()
    return email, space


def _email_from_id_token(id_token: str) -> str:
    """id_token (JWT) 의 email claim 추출 — 검증 없이 payload 만 (callback 단계에서는 OK)."""
    try:
        import base64

        parts = id_token.split(".")
        if len(parts) != 3:
            return ""
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return str(data.get("email") or "")
    except Exception:
        return ""


def _html_response(body: str, status: int) -> tuple[str, int, dict[str, str]]:
    html = f"""<!doctype html><html><head>
<meta charset="utf-8"><title>All-Meet OAuth</title>
<style>body{{font-family:system-ui;max-width:560px;margin:60px auto;padding:0 20px;color:#222}}</style>
</head><body>{body}</body></html>"""
    return html, status, {"Content-Type": "text/html; charset=utf-8"}
