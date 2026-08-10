"""자연어로 주간회의 페이지 수동 생성 — 채팅 진입점.

흐름:
1. ``handle_weekly_page_create`` 가 팀 해석 후 즉시 "생성 중" 카드 반환.
2. 백그라운드 thread 가 ``run_weekly_page_job`` 실행.
3. 결과 카드를 Google Chat REST API 로 같은 스페이스에 push.

스케줄러 ``/trigger/weekly-page`` 와 동일한 생성 로직을 쓰되, 채팅에서 팀 단위로 즉시 실행한다.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any

from api.chat.messages import post_message_to_space
from domains.weekly_meeting.cards import (
    build_page_create_in_progress_card,
    build_page_create_result_card,
    build_page_create_team_needed_card,
)
from firestore.team_config import (
    find_team_by_email,
    get_team_config,
    get_team_list,
    make_team_id,
    normalize_team_id,
)

logger = logging.getLogger(__name__)

_TEAM_SUFFIX_RE = re.compile(r"([A-Za-z0-9가-힣]+팀)")


def resolve_team_id_from_message(
    user_message: str,
    user_email: str = "",
    *,
    teams: list[dict[str, str]] | None = None,
) -> str | None:
    """발화·이메일에서 팀 ID 해석.

    우선순위:
      1) 등록 팀 name/id 가 발화에 포함 (긴 이름 먼저 — 제조ERP2팀 > ERP2팀)
      2) 발화 속 ``*팀`` → ``make_team_id`` 후 config 존재 확인
      3) ``find_team_by_email(user_email)``
    """
    text = (user_message or "").strip()
    compact = text.replace(" ", "")

    registered = teams if teams is not None else get_team_list()
    # 긴 이름 우선 매칭
    ranked = sorted(
        [t for t in registered if isinstance(t, dict)],
        key=lambda t: len(str(t.get("name") or "")),
        reverse=True,
    )
    alnum = re.sub(r"[^A-Za-z0-9]", "", compact).upper()
    for team in ranked:
        tid = normalize_team_id(str(team.get("id") or ""))
        name = str(team.get("name") or "").strip()
        if not tid:
            continue
        if name and (name in text or name.replace(" ", "") in compact):
            return tid
        if tid and tid in alnum:
            return tid

    for m in _TEAM_SUFFIX_RE.finditer(text):
        candidate = make_team_id(m.group(1))
        if candidate and get_team_config(candidate):
            return candidate

    if user_email:
        return find_team_by_email(user_email)
    return None


def handle_weekly_page_create(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``OO팀 주간보고페이지 만들어줘`` 트리거 진입점. main.py dispatch 에서 호출."""
    space_name = ((chat_event or {}).get("space") or {}).get("name") or ""
    user = (chat_event or {}).get("user") or {}
    user_email = str(user.get("email") or "").strip()

    if not space_name:
        return {"text": "스페이스 정보를 가져오지 못했어요. (Google Chat 이벤트 형식 확인 필요)"}

    teams = get_team_list()
    team_id = resolve_team_id_from_message(user_message, user_email, teams=teams)
    if not team_id:
        return build_page_create_team_needed_card(teams)

    cfg = get_team_config(team_id) or {}
    team_name = str(cfg.get("team_name") or team_id)

    threading.Thread(
        target=_run_page_create_background,
        args=(space_name, team_id, team_name),
        daemon=False,
    ).start()

    return build_page_create_in_progress_card(team_name)


def _run_page_create_background(space_name: str, team_id: str, team_name: str) -> None:
    """백그라운드: run_weekly_page_job → Chat REST push."""
    ok = False
    detail = ""
    try:
        from domains.weekly_meeting.page_creator import run_weekly_page_job

        detail = run_weekly_page_job(team_id)
        ok = True
        logger.info("weekly_page_create 완료 [%s]: %s", team_id, detail)
    except Exception as exc:
        logger.exception("weekly_page_create 실패 [%s]", team_id)
        detail = str(exc) or "알 수 없는 오류"

    card = build_page_create_result_card(team_name=team_name, ok=ok, detail=detail)
    try:
        post_message_to_space(space_name=space_name, payload=card)
    except Exception:
        logger.exception("weekly_page_create 결과 푸시 실패 [%s]", team_id)
