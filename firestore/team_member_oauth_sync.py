"""사용자 OAuth 동의/철회 시 모든 팀의 `team_members[].oauth_status` 동기화 (Phase 2).

토큰 본체는 `oauth_tokens/{email}` 한 곳에만 저장. 팀 멤버 필드는 status 플래그만 동기화 —
UI 카드에서 "연결된 팀원" 빠르게 표시하기 위해.
"""

from __future__ import annotations

from .team_config import find_all_teams_by_email
from .writes import get_client

_CONFIG_COLLECTION = "config"


def sync_oauth_status(*, user_email: str, status: str) -> int:
    """user_email 이 속한 모든 팀의 `team_members[].oauth_status` 갱신.

    Args:
        user_email: 대상 사용자 이메일.
        status: ``"linked"`` / ``"expired"`` / ``"revoked"`` / ``None`` (멤버 필드에서 제거).

    Returns:
        업데이트된 팀 개수.
    """
    if not user_email:
        return 0
    db = get_client()
    team_ids = find_all_teams_by_email(user_email)
    updated = 0
    for team_id in team_ids:
        ref = db.collection(_CONFIG_COLLECTION).document(team_id)
        snap = ref.get()
        if not snap.exists:
            continue
        data = snap.to_dict() or {}
        members = data.get("team_members") or []
        changed = False
        for m in members:
            if not isinstance(m, dict):
                continue
            if str(m.get("email") or "").strip() == user_email.strip():
                m["oauth_status"] = status
                changed = True
        if changed:
            ref.update({"team_members": members})
            updated += 1
    return updated
