"""전사 공용 자원 풀 빌더.

`firestore.team_config.get_team_list()` 의 모든 팀을 순회해 각 팀의
``shared_drive_ids`` · ``confluence_space_key`` · ``calendar_id`` 를 dedupe 합집합으로 모음.
``team_members`` 도 함께 모아 Confluence displayName → email 폴백 lookup 에 활용.

PLAN.md §5.1 `public_sources.py` 항목.
"""

from __future__ import annotations

import logging
from typing import Any

from firestore.team_config import get_team_config, get_team_list

logger = logging.getLogger(__name__)


def build_public_sources() -> dict[str, Any]:
    """모든 팀 settings 합집합 + team_members 풀.

    Returns:
        ``{
            "shared_drive_ids": [...],            # dedupe 한 Shared Drive ID 리스트
            "confluence_space_keys": [...],       # dedupe 한 Confluence space key 리스트
            "calendar_ids": [...],                # dedupe 한 공용 Calendar ID 리스트
            "member_pool": [                       # displayName → email 폴백용
                {"name": "...", "nickname": [...], "email": "...", "team_id": "...", "team_name": "..."}
            ],
        }``
    """
    drive_ids: set[str] = set()
    space_keys: set[str] = set()
    calendar_ids: set[str] = set()
    members: list[dict[str, Any]] = []

    try:
        teams = get_team_list() or []
    except Exception as e:
        logger.warning("get_team_list 실패: %s", e)
        teams = []

    for team in teams:
        team_id = str((team or {}).get("id") or "").strip()
        team_name = str((team or {}).get("name") or "").strip()
        if not team_id:
            continue
        try:
            config = get_team_config(team_id) or {}
        except Exception as e:
            logger.warning("get_team_config 실패 team_id=%s: %s", team_id, e)
            continue

        for did in config.get("shared_drive_ids") or []:
            sd = str(did or "").strip()
            if sd:
                drive_ids.add(sd)
        for key_field in ("confluence_space_key", "space_key"):
            sk = str(config.get(key_field) or "").strip()
            if sk:
                space_keys.add(sk)
        cid = str(config.get("calendar_id") or "").strip()
        if cid:
            calendar_ids.add(cid)

        for m in config.get("team_members") or []:
            if not isinstance(m, dict):
                continue
            members.append(
                {
                    "name": str(m.get("name") or "").strip(),
                    "nickname": [str(n or "").strip() for n in (m.get("nickname") or []) if str(n or "").strip()],
                    "email": str(m.get("email") or "").strip(),
                    "team_id": team_id,
                    "team_name": team_name,
                }
            )

    result = {
        "shared_drive_ids": sorted(drive_ids),
        "confluence_space_keys": sorted(space_keys),
        "calendar_ids": sorted(calendar_ids),
        "member_pool": members,
    }
    logger.info(
        "expert_finder public_sources teams=%d drives=%d spaces=%d calendars=%d members=%d",
        len(teams),
        len(result["shared_drive_ids"]),
        len(result["confluence_space_keys"]),
        len(result["calendar_ids"]),
        len(result["member_pool"]),
    )
    return result


def lookup_email_by_display_name(display_name: str, member_pool: list[dict[str, Any]]) -> str | None:
    """displayName → email 폴백 매칭.

    Confluence ``version.by.accountId`` 의 email 해석이 실패했을 때,
    ``version.by.displayName`` 을 ``team_config.team_members`` 의 ``name`` · ``nickname`` 과
    부분일치 매칭. case-insensitive.

    Returns:
        매칭된 멤버의 email. 없으면 None.
    """
    needle = (display_name or "").strip().lower()
    if not needle or not member_pool:
        return None
    # 정확 매칭만 — 과거 loose substring(needle in name) 매칭은 동명이인·부분일치로 다른 사람의
    # 이메일을 잘못 반환해(→ 팀 오표시·중복) 버그를 냈다. 이름 또는 닉네임이 정확히 같을 때만 매칭.
    for m in member_pool:
        email = (m.get("email") or "").strip()
        if not email:
            continue
        if (m.get("name") or "").strip().lower() == needle:
            return email
        for nick in m.get("nickname") or []:
            if (nick or "").strip().lower() == needle:
                return email
    return None


def find_member_by_email(email: str, member_pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    """email → 멤버 정보 (소속팀·표시명) lookup. 카드 표시용."""
    needle = (email or "").strip().lower()
    if not needle:
        return None
    for m in member_pool:
        if (m.get("email") or "").strip().lower() == needle:
            return m
    return None
