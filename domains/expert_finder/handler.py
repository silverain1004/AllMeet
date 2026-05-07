"""전문가 찾기 — 구글 챗 cardsV2 샘플로 분기 확인."""

from __future__ import annotations

import html
from typing import Any


def _card(
    card_id: str,
    title: str,
    subtitle: str,
    body_html: str,
    image_url: str | None = None,
) -> dict[str, Any]:
    """cardsV2 한 장분의 최소 구조 (ui_builder.py 와 동일 최상위 키)."""
    header: dict[str, Any] = {"title": title, "subtitle": subtitle}
    if image_url:
        header["imageUrl"] = image_url
    return {
        "cardsV2": [
            {
                "cardId": card_id,
                "card": {
                    "header": header,
                    "sections": [
                        {
                            "widgets": [
                                {"textParagraph": {"text": body_html}},
                            ]
                        }
                    ],
                },
            }
        ]
    }


def handle_expert_finder(user_message: str) -> dict[str, Any]:
    """사내 전문가 검색·추천. 현재는 샘플 카드 + text 로 라우팅만 확인합니다."""
    preview = html.escape((user_message or "").strip()[:120])
    body = (
        f"<b>[샘플] expert_finder 분기</b><br>"
        f"입력 미리보기: <font color=\"#1a73e8\">{preview or '(비어 있음)'}</font><br><br>"
        "실제 연동 시: 사내 프로필/스킬 검색 결과를 여기에 표시합니다."
    )
    out = _card(
        "allmeet_sample_expert",
        "사내 전문가 찾기 (샘플)",
        "UserIntent.EXPERT_FINDER",
        body,
        image_url="https://www.gstatic.com/images/icons/material/system/2x/person_search_gm_blue_48dp.png",
    )
    out["text"] = "전문가 찾기 플로우로 라우팅되었습니다. (샘플 카드)"
    return out
