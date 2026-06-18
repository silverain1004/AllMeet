"""일일 할일 브리핑 자동 DM 발송.

Cloud Scheduler 트리거:
  POST /trigger/daily-briefing  → send_daily_briefing_to_all()

동작:
  · 평일 09:00 KST: OAuth 동의 팀원 전원에게 오늘의 할일 DM
  · ThreadPoolExecutor 병렬 처리 — 총 소요시간 ≈ 1명 처리 시간
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from domains.weekly_report.auto_notify import (
    _collect_members_by_team,
    _get_space_name_by_email,
)

logger = logging.getLogger(__name__)

_MAX_WORKERS = 10


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def send_daily_briefing_to_all() -> dict[str, Any]:
    """평일 09:00 KST — OAuth 동의 팀원 전원에게 오늘의 할일 DM 발송 (병렬)."""
    teams = _collect_members_by_team()
    logger.info("[daily_brief] 대상 팀 %d개", len(teams))

    all_members: list[tuple[dict[str, str], dict[str, Any]]] = [
        (member, team)
        for team in teams
        for member in team["members"]
    ]

    result: dict[str, list[str]] = {
        "sent": [], "skipped_no_space": [], "skipped_no_oauth": [],
        "skipped_error": [],
    }

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                _send_briefing_to_user,
                member["email"],
                member["name"],
                team,
            ): member["email"]
            for member, team in all_members
        }
        for future in as_completed(futures):
            email = futures[future]
            try:
                status = future.result()
            except Exception as e:
                logger.warning("[daily_brief] future 예외 user=%s: %s", email, e)
                status = "skipped_error"
            result[status].append(email)

    logger.info(
        "[daily_brief] 완료 — 발송: %d, space없음: %d, oauth없음: %d, 오류: %d",
        len(result["sent"]), len(result["skipped_no_space"]),
        len(result["skipped_no_oauth"]), len(result["skipped_error"]),
    )
    return result


# ---------------------------------------------------------------------------
# 내부 — 단일 사용자
# ---------------------------------------------------------------------------

def _send_briefing_to_user(
    user_email: str, user_name: str, team: dict[str, Any]
) -> str:
    """단일 사용자 브리핑 DM → 결과 키."""
    space_name = _get_space_name_by_email(user_email)
    if not space_name:
        logger.info("[daily_brief] space 없음: %s", user_email)
        return "skipped_no_space"

    try:
        from domains.daily_brief.collect import collect_for_user
        raw, errors = collect_for_user(user_email=user_email, team_index=team)
    except Exception as e:
        logger.warning("[daily_brief] 수집 실패 user=%s: %s", user_email, e)
        return "skipped_error"

    # OAuth 미동의 — gmail + drive + confluence 모두 auth_required 면 스킵
    auth_fail_keys = {"gmail", "drive", "personal_drive", "confluence"}
    all_auth_fail = all(
        errors.get(k) == "auth_required" for k in auth_fail_keys
    )
    if all_auth_fail and not any(raw.get(k) for k in ("gmail", "drive", "personal_drive", "confluence")):
        logger.info("[daily_brief] OAuth 미동의 스킵: %s", user_email)
        return "skipped_no_oauth"

    try:
        payload = _analyze_and_build_card(
            user_email=user_email, user_name=user_name, raw=raw, errors=errors,
            space_name=space_name,
        )
    except Exception as e:
        logger.warning("[daily_brief] 분석/카드 빌드 실패 user=%s: %s", user_email, e)
        return "skipped_error"

    try:
        from api.chat.messages import post_message_to_space
        ok = post_message_to_space(space_name=space_name, payload=payload)
        if ok:
            logger.info("[daily_brief] DM 발송 완료: %s", user_email)
            return "sent"
        logger.warning("[daily_brief] DM 발송 응답 오류: %s", user_email)
        return "skipped_error"
    except Exception as e:
        logger.warning("[daily_brief] DM 발송 예외 user=%s: %s", user_email, e)
        return "skipped_error"


def _analyze_and_build_card(
    *, user_email: str, user_name: str, raw: dict[str, Any], errors: dict[str, str],
    space_name: str = "",
) -> dict[str, Any]:
    """2차 LLM 분석 → 카드 빌드."""
    from domains.daily_brief.cards import build_briefing_card, build_no_data_card
    from domains.daily_brief.prompts import BRIEFING_RESPONSE_SCHEMA, build_briefing_prompt
    from domains.weekly_report.draft import _get_vertex_model

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    has_data = any(bool(raw.get(k)) for k in ("gmail", "drive", "personal_drive", "confluence", "today_calendar"))
    if not has_data:
        return build_no_data_card(
            user_name, today_str, user_email=user_email, space_name=space_name,
        )

    prompt = build_briefing_prompt(
        user_name=user_name,
        user_email=user_email,
        today_str=today_str,
        raw=raw,
    )

    tasks: list[dict[str, Any]] = []
    summary = ""
    try:
        from vertexai.generative_models import GenerationConfig
        model = _get_vertex_model()
        res = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=BRIEFING_RESPONSE_SCHEMA,
            ),
        )
        text = (res.text or "").strip()
        if text:
            data = json.loads(text)
            tasks = data.get("tasks") or []
            summary = str(data.get("summary") or "")
    except Exception as e:
        logger.warning("[daily_brief] 2차 LLM 분석 실패 user=%s: %s", user_email, e)

    return build_briefing_card(
        user_name=user_name,
        today_str=today_str,
        summary=summary,
        tasks=tasks,
        errors=errors,
    )
