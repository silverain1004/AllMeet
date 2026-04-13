"""주간업무보고 세팅용 Google Chat cardsV2 빌더."""

from __future__ import annotations

import html
from typing import Any


def _wrap_card(
    card_id: str,
    header: dict[str, Any],
    widgets: list[dict[str, Any]],
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    out = {
        "cardsV2": [
            {
                "cardId": card_id,
                "card": {
                    "header": header,
                    "sections": [{"widgets": widgets}],
                },
            }
        ],
    }
    if include_action_response:
        # Google Chat CARD_CLICKED 응답에서는 UPDATE_MESSAGE가 유효합니다.
        out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def build_team_setup_card(*, include_action_response: bool = False) -> dict[str, Any]:
    widgets = [
        {
            "textParagraph": {
                "text": "<b>1/3 팀 설정</b><br>팀명과 팀원을 입력해 주세요. 팀원은 쉼표(,) 또는 줄바꿈으로 구분할 수 있습니다."
            }
        },
        {"textInput": {"name": "team_name", "label": "팀명", "hintText": "예: PC2팀"}},
        {
            "textInput": {
                "name": "team_members",
                "label": "팀원 목록",
                "hintText": "예: 홍길동, 김철수, 이영희",
                "type": "MULTIPLE_LINE",
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "다음: 폴더 구조",
                        "onClick": {"action": {"function": "wm_save_team"}},
                    }
                ]
            }
        },
    ]
    return _wrap_card(
        "wm_team_setup",
        {"title": "주간업무보고 세팅", "subtitle": "Step 1 - 팀 설정"},
        widgets,
        include_action_response=include_action_response,
    )


def build_folder_schema_card(team_id: str, team_name: str, *, include_action_response: bool = False) -> dict[str, Any]:
    escaped_name = html.escape(team_name)
    widgets = [
        {
            "textParagraph": {
                "text": f"<b>2/3 루트 페이지 설정</b><br>대상 팀: <b>{escaped_name}</b><br>"
                "루트 페이지 ID를 한 줄에 하나씩 입력해 주세요. 줄을 추가/삭제하면 단계 수가 자동으로 반영됩니다."
            }
        },
        {
            "textInput": {
                "name": "root_page_ids",
                "label": "루트 페이지 ID 목록",
                "hintText": "예:\n123456789\n234567890\n345678901",
                "type": "MULTIPLE_LINE",
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "다음: 템플릿 링크",
                        "onClick": {
                            "action": {
                                "function": "wm_save_folder",
                                "parameters": [{"key": "team_id", "value": team_id}],
                            }
                        },
                    }
                ]
            }
        },
    ]
    return _wrap_card(
        "wm_folder_setup",
        {"title": "주간업무보고 세팅", "subtitle": "Step 2 - 루트 페이지 ID"},
        widgets,
        include_action_response=include_action_response,
    )


def build_template_card(
    team_id: str,
    *,
    include_action_response: bool = False,
    suggested_space_key: str = "",
) -> dict[str, Any]:
    space_hint = "예: PLATFORM"
    if suggested_space_key:
        space_hint = f"자동 추출값: {html.escape(suggested_space_key)} (필요 시 수정)"
    widgets = [
        {
            "textParagraph": {
                "text": "<b>3/3 템플릿 설정</b><br>템플릿 링크를 입력하면 스페이스 키는 자동 추출됩니다. "
                "자동 추출이 다르면 아래 스페이스 키를 직접 수정해 주세요."
            }
        },
        {
            "textInput": {
                "name": "template_page_url",
                "label": "템플릿 링크",
                "hintText": "예: https://.../wiki/spaces/PC2/pages/2274952548/...",
            }
        },
        {
            "textInput": {
                "name": "confluence_space_key",
                "label": "Confluence 스페이스 키",
                "hintText": space_hint,
                "value": suggested_space_key,
            }
        },
        {
            "buttonList": {
                "buttons": [
                    {
                        "text": "세팅 완료",
                        "onClick": {
                            "action": {
                                "function": "wm_save_template",
                                "parameters": [{"key": "team_id", "value": team_id}],
                            }
                        },
                    }
                ]
            }
        },
    ]
    return _wrap_card(
        "wm_template_setup",
        {"title": "주간업무보고 세팅", "subtitle": "Step 3 - 템플릿 링크"},
        widgets,
        include_action_response=include_action_response,
    )


def build_setup_completed_card(team_name: str, *, include_action_response: bool = False) -> dict[str, Any]:
    escaped_name = html.escape(team_name)
    widgets = [
        {
            "textParagraph": {
                "text": f"✅ <b>{escaped_name}</b> 팀의 주간업무보고 세팅이 완료되었습니다.<br>이제 동일 팀 기준으로 자동화 실행을 시작할 수 있습니다."
            }
        }
    ]
    return _wrap_card(
        "wm_setup_done",
        {"title": "주간업무보고 세팅 완료", "subtitle": "MVP 설정 저장 완료"},
        widgets,
        include_action_response=include_action_response,
    )
