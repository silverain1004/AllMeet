"""Vertex Gemini 추천 멘트 생성 — 상위 Top 3 후보 + 근거.

PLAN.md §5.1 `recommend.py`.

호출 측 (handler) 에서 ``recommend(keyword, scored)`` 호출 → ``{experts: [{email, reason}]}``.
실패 시 None 반환 → 카드는 raw evidence 만으로 표시.

JSON schema 단순화: reason 만 LLM 생성, evidence 는 scored 객체에 이미 있으니 카드 빌더에서
email 로 매칭해 결합.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from config.settings import ALLMEET_DRAFT_MODEL, LOCATION, PROJECT_ID

logger = logging.getLogger(__name__)

_vertex_lock = threading.Lock()
_vertex_model = None


def recommend(keyword: str, scored: list[dict[str, Any]]) -> dict[str, Any] | None:
    """상위 Top 3 후보 + 근거를 Vertex 에 넘겨 추천 멘트 생성.

    Args:
        keyword: 검색 키워드 (예: "MES 개발").
        scored: ``score_candidates`` 결과 (점수 내림차순).

    Returns:
        ``{"experts": [{"email": "...", "reason": "..."}]}`` — 성공.
        ``None`` — Vertex 호출/파싱 실패. 호출 측은 raw 만으로 카드 빌드.
    """
    if not scored:
        return None
    top3 = scored[:3]

    try:
        model = _get_vertex_model()
        from vertexai.generative_models import GenerationConfig

        prompt = _build_prompt(keyword, top3)
        res = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                max_output_tokens=2048,
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
        text = (res.text or "").strip()
        if not text:
            logger.warning("Vertex 추천 멘트 빈 응답")
            return None
        parsed = json.loads(text)
        logger.info(
            "expert_finder recommend experts=%d",
            len(parsed.get("experts") or []),
        )
        return parsed
    except Exception as e:
        logger.warning("Vertex 추천 멘트 실패: %s", e)
        return None


# 프롬프트 — 각 후보의 근거 자료를 정리해 LLM 에 넘김. 답변은 reason 만.
def _build_prompt(keyword: str, top3: list[dict[str, Any]]) -> str:
    lines = [
        "역할: 사내 전문가 추천 어시스턴트.",
        f'키워드 "{keyword}" 의 전문가 후보 {len(top3)}명에 대해, 각 후보의 근거 자료(문서·메일·일정 제목)를',
        "보고 왜 이 사람이 이 주제의 전문가인지 1~2 문장 한국어로 자연어 추천 멘트를 작성하세요.",
        "",
        "규칙:",
        "- 근거 자료 제목에서 보이는 구체적 활동을 인용하세요 (예: '~~ 협의를 주도', '~~ 시스템 구축에 참여').",
        "- 추측·과장 금지. 자료에 없는 사실 만들지 말 것.",
        "- 'XX팀 소속' 같은 메타 정보 반복 X (카드에 이미 표시됨).",
        "",
        "후보 정보:",
    ]
    for i, e in enumerate(top3, 1):
        score = float(e.get("score") or 0.0)
        lines.append(f"\n{i}. {e.get('email')} (점수 {score:.1f})")
        evidence = e.get("evidence") or []
        for h in evidence[:5]:
            src = h.get("source") or "?"
            title = (h.get("title") or "").strip()
            when = (h.get("when") or "")[:10]
            lines.append(f"   - [{src}] {title} ({when})")

    lines.append("")
    lines.append("출력: JSON. experts 배열 — 각 항목은 {email, reason}. email 은 위 후보 email 그대로.")
    return "\n".join(lines)


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "experts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["email", "reason"],
            },
        },
    },
    "required": ["experts"],
}


# Vertex 클라이언트 싱글톤 (스레드 안전).
def _get_vertex_model():
    global _vertex_model
    if _vertex_model is not None:
        return _vertex_model
    with _vertex_lock:
        if _vertex_model is not None:
            return _vertex_model
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _vertex_model = GenerativeModel(ALLMEET_DRAFT_MODEL)
    return _vertex_model
