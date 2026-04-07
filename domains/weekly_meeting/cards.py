"""주간업무보고 세팅용 Google Chat cardsV2 빌더."""

from __future__ import annotations

import html
from typing import Any


def _wrap_card(card_id: str, header: dict[str, Any], widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "actionResponse": {"type": "NEW_CARD"},
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


def build_team_setup_card() -> dict[str, Any]:
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
    )


def build_folder_schema_card(team_id: str, team_name: str) -> dict[str, Any]:
    escaped_name = html.escape(team_name)
    widgets = [
        {"textParagraph": {"text": f"<b>2/3 폴더 구조 설정</b><br>대상 팀: <b>{escaped_name}</b>"}},
        {
            "textInput": {
                "name": "folder_schema",
                "label": "폴더 구조(JSON 배열)",
                "hintText": '[{"level":1,"name":"YYYY년"},{"level":2,"name":"Q분기"},{"level":3,"name":"주간업무보고"}]',
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
        {"title": "주간업무보고 세팅", "subtitle": "Step 2 - 폴더 구조"},
        widgets,
    )


def build_template_card(team_id: str) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>3/3 템플릿 설정</b><br>사용할 Confluence 템플릿 페이지 링크를 붙여넣어 주세요."}},
        {
            "textInput": {
                "name": "template_page_url",
                "label": "템플릿 링크",
                "hintText": "예: https://.../wiki/spaces/PC2/pages/2274952548/...",
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
    )


def build_setup_completed_card(team_name: str) -> dict[str, Any]:
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
    )
