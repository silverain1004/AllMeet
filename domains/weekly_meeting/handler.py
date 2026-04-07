"""주간업무보고 세팅 핸들러."""

from __future__ import annotations

import json
from typing import Any

from domains.weekly_meeting.cards import (
    build_folder_schema_card,
    build_setup_completed_card,
    build_team_setup_card,
    build_template_card,
)
from firestore.team_config import (
    get_team_config,
    make_team_id,
    parse_folder_schema,
    parse_team_members,
    parse_template_page_id,
    upsert_team_config,
)

def _safe_form_value(form_inputs: dict[str, Any], key: str) -> str:
    raw = form_inputs.get(key)
    if isinstance(raw, dict):
        string_inputs = raw.get("stringInputs") or {}
        values = string_inputs.get("value") or []
        if isinstance(values, list) and values:
            return str(values[0]).strip()
    if raw is None:
        return ""
    return str(raw).strip()


def handle_weekly_meeting(user_message: str, chat_event: dict[str, Any] | None = None) -> dict[str, Any]:
    """메시지 진입 시 팀 설정 카드(1/3)를 반환."""
    out = build_team_setup_card()
    out["text"] = "주간업무보고 세팅을 시작합니다. 먼저 팀 정보를 입력해 주세요."
    return out


def handle_weekly_meeting_action(
    *,
    invoked_function: str,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    chat_event: dict[str, Any],
) -> dict[str, Any]:
    """카드 액션 이벤트를 처리하고 다음 카드 또는 완료 카드를 반환."""
    user = (chat_event or {}).get("user") or {}
    user_context = {
        "name": user.get("displayName") or "",
        "email": user.get("email") or "",
        "department": "미지정",
    }
    space_id = ((chat_event or {}).get("space") or {}).get("name", "")

    if invoked_function == "wm_save_team":
        team_name = _safe_form_value(form_inputs, "team_name")
        if not team_name:
            return {"text": "팀명은 필수입니다. 팀명을 입력해 주세요."}

        members_raw = _safe_form_value(form_inputs, "team_members")
        team_members = parse_team_members(members_raw)
        if not team_members:
            return {"text": "팀원은 최소 1명 이상 입력해 주세요."}

        team_id = make_team_id(team_name)
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"team_members": team_members, "setup_completed": False},
        )
        return build_folder_schema_card(team_id=team_id, team_name=team_name)

    if invoked_function == "wm_save_folder":
        team_id = (parameters.get("team_id") or "").strip()
        if not team_id:
            return {"text": "팀 식별자가 없습니다. 처음부터 다시 진행해 주세요."}

        schema_text = _safe_form_value(form_inputs, "folder_schema")
        try:
            folder_schema = parse_folder_schema(schema_text)
        except (ValueError, json.JSONDecodeError) as e:
            return {"text": f"폴더 구조 형식이 올바르지 않습니다: {e}"}
        if not folder_schema:
            return {"text": "폴더 구조는 최소 1레벨 이상 입력해 주세요."}

        existing = get_team_config(team_id) or {}
        team_name = str(existing.get("team_name") or team_id)
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"folder_schema": folder_schema, "setup_completed": False},
        )
        return build_template_card(team_id=team_id)

    if invoked_function == "wm_save_template":
        team_id = (parameters.get("team_id") or "").strip()
        if not team_id:
            return {"text": "팀 식별자가 없습니다. 처음부터 다시 진행해 주세요."}
        template_page_url = _safe_form_value(form_inputs, "template_page_url")
        if not template_page_url:
            return {"text": "템플릿 링크를 입력해 주세요."}
        template_page_id = parse_template_page_id(template_page_url)
        if template_page_id is None:
            return {"text": "Confluence 템플릿 링크 형식을 확인해 주세요."}

        existing = get_team_config(team_id) or {}
        team_name = str(existing.get("team_name") or team_id)
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={
                "template_page_url": template_page_url,
                "template_page_id": template_page_id,
                "setup_completed": True,
            },
        )
        return build_setup_completed_card(team_name=team_name)

    return {"text": "지원하지 않는 주간업무보고 카드 액션입니다."}
