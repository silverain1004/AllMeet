"""Vertex LLM 기반 compose state 추출 (선택)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import ALLMEET_CHAT_MODEL, LOCATION, PROJECT_ID

KST = timezone(timedelta(hours=9))
_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


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
    # 기준일 없이 상대 날짜("오늘", "이번 주 금요일")를 주면 LLM이 임의 날짜를 환각한다.
    now = datetime.now(KST)
    today_hint = f"{now:%Y-%m-%d} ({_WEEKDAY_KO[now.weekday()]})"
    prompt = f"""사용자 메시지에서 회의 예약 정보를 JSON으로만 추출하세요.
오늘은 {today_hint} 입니다 (Asia/Seoul 기준). "오늘"·"내일"·"이번 주 금요일" 같은 상대 날짜는 반드시 이 날짜를 기준으로 계산하세요.
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
    # 환각 방어 — LLM이 낸 날짜가 형식 오류거나 오늘(KST) 이전이면 폐기
    if out.get("meeting_date"):
        try:
            parsed = datetime.strptime(out["meeting_date"], "%Y-%m-%d").date()
            if parsed < now.date():
                out.pop("meeting_date")
        except ValueError:
            out.pop("meeting_date")
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
