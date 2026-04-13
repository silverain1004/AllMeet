"""주간업무보고 세팅 핸들러."""

from __future__ import annotations

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
    parse_confluence_space_key,
    parse_root_page_ids,
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
        return build_folder_schema_card(
            team_id=team_id,
            team_name=team_name,
            include_action_response=True,
        )

    if invoked_function == "wm_save_folder":
        team_id = (parameters.get("team_id") or "").strip()
        if not team_id:
            return {"text": "팀 식별자가 없습니다. 처음부터 다시 진행해 주세요."}

        root_pages_raw = _safe_form_value(form_inputs, "root_page_ids")
        try:
            root_pages = parse_root_page_ids(root_pages_raw)
        except ValueError as e:
            return {"text": f"루트 페이지 ID 형식이 올바르지 않습니다: {e}"}
        if not root_pages:
            return {"text": "루트 페이지 ID를 최소 1개 이상 입력해 주세요."}

        existing = get_team_config(team_id) or {}
        team_name = str(existing.get("team_name") or team_id)
        upsert_team_config(
            team_id=team_id,
            team_name=team_name,
            space_id=space_id,
            user_context=user_context,
            updates={"root_pages": root_pages, "setup_completed": False},
        )
        existing_space_key = str(existing.get("confluence_space_key") or "")
        return build_template_card(
            team_id=team_id,
            include_action_response=True,
            suggested_space_key=existing_space_key,
        )

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
        manual_space_key = _safe_form_value(form_inputs, "confluence_space_key")
        parsed_space_key = parse_confluence_space_key(template_page_url)
        confluence_space_key = parse_confluence_space_key(manual_space_key) or parsed_space_key
        if confluence_space_key is None:
            return {"text": "Confluence 스페이스 키를 찾지 못했습니다. 스페이스 키를 직접 입력해 주세요."}

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
                "confluence_space_key": confluence_space_key,
                "setup_completed": True,
            },
        )
        return build_setup_completed_card(team_name=team_name, include_action_response=True)

    return {"text": "지원하지 않는 주간업무보고 카드 액션입니다."}
