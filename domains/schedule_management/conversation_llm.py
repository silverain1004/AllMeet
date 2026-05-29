"""Vertex LLM 기반 compose state 추출 (선택)."""

from __future__ import annotations

import json
import re
from typing import Any

from config.settings import ALLMEET_CHAT_MODEL, LOCATION, PROJECT_ID


def llm_extract_compose_state(
    user_message: str,
    *,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    if not (PROJECT_ID and user_message.strip()):
        return {}
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel
    except ImportError:
        return {}

    member_hint = ", ".join(
        f"{m.get('name')}<{m.get('email')}>" for m in members[:30] if m.get("name")
    )
    prompt = f"""사용자 메시지에서 회의 예약 정보를 JSON으로만 추출하세요.
필드: meeting_date(YYYY-MM-DD), meeting_time(HH:MM), title, duration_minutes(정수), auto_meet(불리언), attendee_emails(배열).
알 수 없으면 null. 팀원 후보: {member_hint}

메시지: {user_message}
"""
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    model = GenerativeModel(ALLMEET_CHAT_MODEL)
    resp = model.generate_content(
        prompt,
        generation_config={"temperature": 0.1, "max_output_tokens": 512},
    )
    text = str(getattr(resp, "text", "") or "")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    data = json.loads(m.group(0))
    out: dict[str, Any] = {}
    for key in ("meeting_date", "meeting_time", "title"):
        if data.get(key):
            out[key] = str(data[key])
    if data.get("duration_minutes"):
        try:
            out["duration_minutes"] = int(data["duration_minutes"])
        except (TypeError, ValueError):
            pass
    if data.get("auto_meet"):
        out["auto_meet"] = True
    emails = data.get("attendee_emails") or []
    if isinstance(emails, list):
        out["attendees"] = [{"name": "", "email": str(e)} for e in emails if e]
    return out
