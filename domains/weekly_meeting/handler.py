"""주간 회의 / 팀·인원 등록 — 구글 챗 cardsV2 샘플로 분기 확인."""

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
    """cardsV2 한 장분의 최소 구조."""
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


def handle_weekly_meeting(user_message: str) -> dict[str, Any]:
    """실제 등록 로직 전까지 샘플 카드로 의도만 확인합니다."""
    preview = html.escape((user_message or "").strip()[:120])
    body = (
        f"<b>[샘플] weekly_meeting 분기</b><br>"
        f"입력 미리보기: <font color=\"#9334e6\">{preview or '(비어 있음)'}</font><br><br>"
        "실제 연동 시: 팀/회의실/인원 등록 폼 또는 상태를 표시합니다."
    )
    out = _card(
        "allmeet_sample_weekly",
        "주간 회의·팀 등록 (샘플)",
        "UserIntent.WEEKLY_MEETING",
        body,
        image_url="https://www.gstatic.com/images/icons/material/system/2x/groups_gm_blue_48dp.png",
    )
    out["text"] = "주간 회의·등록 플로우로 라우팅되었습니다. (샘플 카드)"
    return out
