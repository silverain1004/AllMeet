"""일일 할일 브리핑 Google Chat cardsV2 카드 빌더."""

from __future__ import annotations

import html
from typing import Any


def build_briefing_card(
    *,
    user_name: str,
    today_str: str,
    summary: str,
    tasks: list[dict[str, Any]],
    errors: dict[str, str],
) -> dict[str, Any]:
    """우선순위별 할일 목록 카드.

    섹션: 🔴 URGENT / 🟡 ACTION / 🟢 FYI (NOISE는 제외)
    """
    urgent = [t for t in tasks if t.get("priority") == "urgent"]
    action = [t for t in tasks if t.get("priority") == "action"]
    fyi    = [t for t in tasks if t.get("priority") == "fyi"]

    sections: list[dict[str, Any]] = []

    # 헤더
    sections.append({
        "widgets": [{
            "textParagraph": {
                "text": (
                    f"<b>{html.escape(user_name)}</b>님, 오늘의 할 일입니다 ({html.escape(today_str)})<br>"
                    f"{html.escape(summary)}"
                )
            }
        }]
    })

    if urgent:
        sections.append(_task_section("🔴 긴급 · 오늘 꼭 처리하세요", urgent))

    if action:
        sections.append(_task_section("🟡 처리 필요 · 답장이나 조치", action))

    if fyi:
        sections.append(_task_section("🟢 참고만 · 읽어두면 좋아요", fyi, collapsible=True))

    if not urgent and not action and not fyi:
        sections.append({
            "widgets": [{"textParagraph": {"text": "어제 할일 항목을 찾지 못했어요."}}]
        })

    # 데이터 수집 오류 안내 — 기술 코드는 노출하지 않음.
    # 설정/권한 미비(auth_required·space_key_missing 등)는 사용자가 손쓸 수 없으니 숨기고,
    # 그 외 일시적 오류만 일반 문구로 부드럽게 안내.
    _SILENT_ERRORS = {"", "auth_required", "space_key_missing"}
    if any(v not in _SILENT_ERRORS for v in errors.values()):
        sections.append({
            "widgets": [{
                "textParagraph": {
                    "text": "<font color=\"#888888\">일부 항목은 잠시 불러오지 못했어요.</font>"
                }
            }]
        })

    return {
        "cardsV2": [{
            "cardId": "daily_briefing",
            "card": {"sections": sections},
        }]
    }


def build_no_data_card(
    user_name: str,
    today_str: str,
    *,
    user_email: str = "",
    space_name: str = "",
) -> dict[str, Any]:
    """어제 활동 데이터를 찾지 못함 — 데이터 재연결 버튼 포함 카드.

    Google Chat ``text`` 필드는 HTML 을 렌더링하지 않아 ``<b>`` 태그가 그대로
    노출됨. cardsV2 textParagraph(=HTML 지원)로 안내하고, OAuth 동의 흐름(설정의
    "내 데이터 연결"과 동일 로직)을 여는 openLink 버튼을 제공한다.
    """
    name = html.escape(user_name)
    date = html.escape(today_str)
    widgets: list[dict[str, Any]] = [
        {
            "textParagraph": {
                "text": (
                    f"<b>📋 {name}님, 오늘 할 일 브리핑</b> ({date})<br><br>"
                    "매일 아침, 어제의 Gmail · Drive · Confluence · 캘린더를 모아 "
                    "<b>오늘 처리할 일을 우선순위로 정리</b>해 드리는 기능이에요.<br><br>"
                    "아직 데이터가 연결되지 않아 어제 활동을 가져오지 못했어요. "
                    "아래에서 연결하면 내일 아침부터 브리핑이 도착합니다."
                )
            }
        }
    ]

    if user_email:
        from domains.weekly_meeting.oauth_callback import (
            build_authorization_url,
            encode_state,
        )

        auth_url = build_authorization_url(
            user_email_hint=user_email,
            state=encode_state(user_email=user_email, space_name=space_name),
        )
        widgets.append({
            "buttonList": {
                "buttons": [{
                    "text": "🔗 내 데이터 연결",
                    "onClick": {"openLink": {"url": auth_url}},
                }]
            }
        })
    else:
        widgets.append({
            "textParagraph": {
                "text": (
                    "<font color=\"#888888\">챗에서 ⚙️ 설정 → 내 데이터 연결을 "
                    "눌러 다시 연결해 주세요.</font>"
                )
            }
        })

    return {
        "cardsV2": [{
            "cardId": "daily_briefing_no_data",
            "card": {
                "header": {"title": "AllMeet", "subtitle": "데이터 연결 필요"},
                "sections": [{"widgets": widgets}],
            },
        }]
    }


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

_SOURCE_ICON = {
    "gmail":     "📧",
    "drive":     "📁",
    "confluence": "📄",
    "calendar":  "📅",
}


def _truncate(s: str, limit: int) -> str:
    """긴 문장이 카드 폭을 늘리지 않도록 길이 제한 (말줄임)."""
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _task_section(
    header: str, tasks: list[dict[str, Any]], collapsible: bool = False
) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = []
    for t in tasks:
        icon = _SOURCE_ICON.get(str(t.get("source") or ""), "•")
        title = html.escape(_truncate(str(t.get("title") or ""), 50))
        reason = html.escape(_truncate(str(t.get("reason") or ""), 70))
        deadline = str(t.get("deadline_hint") or "").strip()
        reply_tag = " <b>↩답장필요</b>" if t.get("response_required") else ""

        text = f"{icon}{reply_tag} {title}"
        if deadline:
            text += f" <font color=\"#cc0000\">→ {html.escape(deadline)}</font>"
        if reason:
            text += f"<br><font color=\"#666666\">{reason}</font>"

        widgets.append({"textParagraph": {"text": text}})

    section: dict[str, Any] = {
        "header": header,
        "widgets": widgets,
    }
    if collapsible:
        section["collapsible"] = True
        section["uncollapsibleWidgetsCount"] = 0
    return section
