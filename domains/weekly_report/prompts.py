"""주간보고초안 Vertex Gemini 프롬프트 + JSON response_schema."""

from __future__ import annotations

from typing import Any

_SYSTEM = (
    "당신은 'All-Meet' 의 주간보고 초안 작성 도우미입니다.\n"
    "사용자의 한 주 활동(회의 / 작성 문서 / Confluence 페이지 / 메일) 데이터를 받아\n"
    "주간회의에 보고할 수 있는 초안을 한국어로 정리합니다.\n\n"
    "규칙:\n"
    "- 각 항목을 간결하게 분류하세요. 회의는 'X 회의 진행', 페이지/문서는 내용 분석 후\n"
    "  'X 매뉴얼 작성' / 'X 회의록 정리' / 'X 가이드 업데이트' 등 자연스러운 한국어로.\n"
    "- 빈 섹션이면 그 섹션은 빈 배열로 두세요.\n"
    "- summary 는 그 사람의 한 주 핵심 성과·활동을 2~3문장으로 종합."
)


def build_draft_prompt(*, user_name: str, meeting_date: str, raw: dict[str, Any]) -> str:
    """raw 데이터를 텍스트로 풀어 프롬프트로 만들기."""
    lines: list[str] = [_SYSTEM, "", f"## 사용자: {user_name}", f"## 주간회의 일자: {meeting_date}", "", "## 수집된 데이터", ""]

    cal = raw.get("calendar") or []
    lines.append(f"### 회의 이력 ({len(cal)}건)")
    for e in cal:
        summary = (e.get("summary") or "-").strip()
        start = (e.get("start") or "-")[:10]
        lines.append(f"- {summary} | {start}")
    lines.append("")

    drv = raw.get("drive") or []
    lines.append(f"### Drive 파일 작성/수정 ({len(drv)}건)")
    for f in drv:
        name = (f.get("name") or "-").strip()
        mime = (f.get("mime_type") or "-").strip()
        modified = (f.get("modified_time") or "-")[:10]
        lines.append(f"- {name} ({mime}) | {modified}")
    lines.append("")

    pgs = raw.get("confluence") or []
    lines.append(f"### Confluence 페이지 ({len(pgs)}건)")
    for p in pgs:
        title = (p.get("title") or "-").strip()
        modified = (p.get("modified_time") or "-")[:10]
        lines.append(f"- {title} | {modified}")
    lines.append("")

    mls = raw.get("gmail") or []
    if mls:
        lines.append(f"### 메일 ({len(mls)}건)")
        for m in mls:
            subject = (m.get("subject") or "-").strip()
            sender = (m.get("from") or "-").strip()
            date = (m.get("date") or "-").strip()
            lines.append(f"- {subject} ({sender}) | {date}")
        lines.append("")

    lines.append("이 데이터를 분석해 JSON schema 에 맞게 출력하세요.")
    return "\n".join(lines)


# Gemini structured output schema (response_mime_type=application/json + response_schema).
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "meetings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "summary": {"type": "STRING"},
                    "date": {"type": "STRING"},
                },
            },
        },
        "documents": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "type": {"type": "STRING"},
                    "modified_at": {"type": "STRING"},
                },
            },
        },
        "pages": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "type": {"type": "STRING"},
                    "modified_at": {"type": "STRING"},
                },
            },
        },
        "emails": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "subject": {"type": "STRING"},
                    "sent_at": {"type": "STRING"},
                    "counterparty": {"type": "STRING"},
                },
            },
        },
        "summary": {"type": "STRING"},
    },
    "required": ["summary"],
}
