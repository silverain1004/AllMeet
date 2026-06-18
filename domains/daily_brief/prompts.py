"""일일 할일 브리핑 Vertex Gemini 프롬프트 + JSON response_schema.

2-pass 구조:
  1차 (SELECT_SCHEMA): 제목 목록 → 본문 읽어야 할 Gmail ID / Confluence ID 선별
  2차 (BRIEFING_RESPONSE_SCHEMA): 제목+본문 전체 → URGENT/ACTION/FYI/NOISE 분류
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 1차 pass — 본문 선별
# ---------------------------------------------------------------------------

SELECT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "gmail_ids":      {"type": "ARRAY", "items": {"type": "STRING"}},
        "confluence_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["gmail_ids", "confluence_ids"],
}

_SELECT_SYSTEM = (
    "당신은 오늘의 할일 분석 도우미입니다.\n"
    "아래는 사용자의 어제 활동 목록입니다 (제목·발신자만).\n"
    "오늘 할일을 정확히 분석하기 위해 '본문'이 추가로 필요한 항목의 ID를 골라주세요.\n\n"
    "본문이 필요한 기준:\n"
    "- 제목만으로 할일·처리 여부 판단이 불가능한 것\n"
    "- 답장/승인/검토 요청 가능성이 있는 것\n"
    "- 마감이나 긴급 처리 여부가 불분명한 것\n\n"
    "본문이 불필요한 기준:\n"
    "- 제목이 명확히 정보성(공지, 뉴스레터, 자동알림, 일정 상태 표시)\n"
    "- 주간회의록, 스탠드업, 점심 등 업무 무관 일정\n\n"
    "결과는 JSON으로만 반환하세요."
)


def build_select_prompt(meta: dict[str, Any]) -> str:
    """1차 pass 프롬프트 — 제목/발신자 목록 → 본문 필요 ID 선별."""
    lines: list[str] = [_SELECT_SYSTEM, ""]

    gmail = meta.get("gmail") or []
    if gmail:
        lines.append(f"## Gmail ({len(gmail)}건)")
        for m in gmail:
            lines.append(
                f"- id={m.get('id', '')} | 제목: {m.get('subject', '-')} | 발신: {m.get('from', '-')}"
            )
        lines.append("")

    confluence = meta.get("confluence") or []
    if confluence:
        lines.append(f"## Confluence 수정 페이지 ({len(confluence)}건)")
        for p in confluence:
            lines.append(f"- id={p.get('id', '')} | 제목: {p.get('title', '-')}")
        lines.append("")

    drive = meta.get("drive") or []
    personal_drive = meta.get("personal_drive") or []
    all_drive = drive + personal_drive
    if all_drive:
        lines.append(f"## Drive 수정 파일 ({len(all_drive)}건, 본문 fetch 불가 — 참고만)")
        for f in all_drive:
            lines.append(f"- {f.get('name', '-')} | 수정: {(f.get('modified_time') or '')[:10]}")
        lines.append("")

    calendar = meta.get("today_calendar") or []
    if calendar:
        lines.append(f"## 오늘 일정 ({len(calendar)}건, description 이미 포함 — 별도 선별 불필요)")
        for e in calendar:
            lines.append(f"- {e.get('summary', '-')} | {(e.get('start') or '')[:16]}")
        lines.append("")

    lines.append("위 목록에서 본문이 필요한 항목의 ID를 JSON으로 반환하세요.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2차 pass — 할일 분석
# ---------------------------------------------------------------------------

BRIEFING_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tasks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title":             {"type": "STRING"},
                    "priority":          {"type": "STRING"},
                    "source":            {"type": "STRING"},
                    "response_required": {"type": "BOOLEAN"},
                    "reason":            {"type": "STRING"},
                    "deadline_hint":     {"type": "STRING"},
                },
                "required": ["title", "priority", "source", "response_required", "reason", "deadline_hint"],
            },
        },
        "summary": {"type": "STRING"},
    },
    "required": ["tasks", "summary"],
}

_BRIEFING_SYSTEM = (
    "당신은 'All-Meet'의 일일 할일 브리핑 도우미입니다.\n"
    "사용자의 어제 활동(Gmail·Drive·Confluence)과 오늘 일정을 보고\n"
    "'오늘 해야 할 일'을 한국어로 추출·우선순위 정렬합니다.\n\n"
    "priority 분류 기준:\n"
    "- urgent: 오늘 반드시 처리 (마감 임박, 팀장/임원 요청, 시스템 장애 등)\n"
    "- action: 답장·검토·처리 필요하지만 긴급하지 않음 (코드리뷰, 미팅 팔로업 등)\n"
    "- fyi: 읽기만 하면 됨 (정보 공유, 참고용 공지)\n"
    "- noise: 업무 무관 (뉴스레터, 자동알림, 광고, 상태 표시 일정) — 카드에서 제외됨\n\n"
    "response_required: Gmail 메일에서 답장이 필요한 경우 true.\n"
    "deadline_hint: 메일·문서에서 명시된 기한만 (예: '오늘까지', '6/20까지'). 없으면 빈 문자열.\n"
    "source: gmail / drive / confluence / calendar 중 하나.\n\n"
    "규칙:\n"
    "- 본인 발신 메일은 본인 행위이므로 상대방 처리 필요 아님 (response_required=false).\n"
    "- noise로 분류된 항목은 reason을 빈 문자열로 두세요.\n"
    "- 데이터에 없는 정보는 절대 추측하지 마세요.\n"
    "- summary는 오늘 핵심 할일을 한 줄로 (예: '긴급 답장 2건, 검토 3건, 오전 스탠드업').\n"
)


def build_briefing_prompt(
    *, user_name: str, user_email: str, today_str: str, raw: dict[str, Any]
) -> str:
    """2차 pass 프롬프트 — 제목+본문 전체 → URGENT/ACTION/FYI/NOISE 분류."""
    lines: list[str] = [
        _BRIEFING_SYSTEM,
        "",
        f"## 사용자: {user_name}",
        f"## 사용자 이메일: {user_email}",
        f"## 오늘 날짜: {today_str}",
        "",
        "## 수집된 데이터",
        "",
    ]

    gmail = raw.get("gmail") or []
    if gmail:
        lines.append(f"### Gmail 수신 메일 ({len(gmail)}건)")
        for m in gmail:
            subject = (m.get("subject") or "-").strip()
            sender = (m.get("from") or "-").strip()
            date = (m.get("date") or "-").strip()
            body = (m.get("body") or "").strip()
            lines.append(f"- 제목: {subject} | 발신: {sender} | 날짜: {date}")
            if body:
                lines.append(f"  본문: {body[:500]}")
        lines.append("")

    drive = raw.get("drive") or []
    personal_drive = raw.get("personal_drive") or []
    all_drive = drive + personal_drive
    if all_drive:
        lines.append(f"### Drive 수정 파일 ({len(all_drive)}건)")
        for f in all_drive:
            name = (f.get("name") or "-").strip()
            modified = (f.get("modified_time") or "-")[:10]
            lines.append(f"- {name} | 수정: {modified}")
        lines.append("")

    confluence = raw.get("confluence") or []
    if confluence:
        lines.append(f"### Confluence 수정 페이지 ({len(confluence)}건)")
        for p in confluence:
            title = (p.get("title") or "-").strip()
            modified = (p.get("modified_time") or "-")[:10]
            body = (p.get("body") or "").strip()
            lines.append(f"- 제목: {title} | 수정: {modified}")
            if body:
                lines.append(f"  내용: {body[:500]}")
        lines.append("")

    calendar = raw.get("today_calendar") or []
    if calendar:
        lines.append(f"### 오늘 일정 ({len(calendar)}건)")
        for e in calendar:
            summary = (e.get("summary") or "-").strip()
            start = (e.get("start") or "-")[:16]
            desc = (e.get("description") or "").strip()
            lines.append(f"- {summary} | {start}")
            if desc:
                lines.append(f"  설명: {desc[:300]}")
        lines.append("")

    lines.append("이 데이터를 분석해 JSON schema에 맞게 출력하세요.")
    return "\n".join(lines)
