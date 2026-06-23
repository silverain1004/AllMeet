"""에이전트 전용 Vertex Gemini 호출 + JSON 추출 헬퍼.

daily_chat.chat._get_generative_model 과 같은 패턴이지만, 플래너/repair 는 항상
JSON 한 덩어리를 출력하도록 generation_config 를 별도로 둔다 (temperature 낮게).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from config.settings import ALLMEET_CHAT_MODEL, ALLMEET_PLANNER_MODEL, LOCATION, PROJECT_ID

logger = logging.getLogger(__name__)

# 플래너/자가검수/복구 전용 모델명(설정에서 노출). 호출 측은 model=PLANNER_MODEL 로 지정한다.
PLANNER_MODEL = ALLMEET_PLANNER_MODEL

# 모델명 → GenerativeModel 싱글톤 캐시. plan(pro)·content(flash) 가 공존하므로 모델별로 둔다.
_models: dict[str, Any] = {}
_vertex_inited = False


def _get_model(model_name: str | None = None):
    """모델명별 GenerativeModel 싱글톤 (최초 호출 시 초기화).

    ``model_name`` 미지정 시 ALLMEET_CHAT_MODEL(flash) 사용.
    """
    global _vertex_inited
    name = (model_name or "").strip() or ALLMEET_CHAT_MODEL
    cached = _models.get(name)
    if cached is not None:
        return cached

    import vertexai
    from vertexai.generative_models import GenerativeModel

    if not _vertex_inited:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _vertex_inited = True
    model = GenerativeModel(name)
    _models[name] = model
    return model


def generate_json(prompt: str, *, model: str | None = None) -> dict[str, Any]:
    """프롬프트를 보내고 응답에서 첫 JSON 오브젝트를 파싱해 반환. 실패 시 {}.

    LLM 자격증명/네트워크 문제로 호출 자체가 실패하면 빈 dict 를 돌려, 호출 측이
    안전한 폴백(키워드 라우팅 등)으로 빠질 수 있게 한다.

    ``model`` 로 모델을 지정한다(플래너는 PLANNER_MODEL). 미지정 시 flash.

    gemini-2.5 계열(thinking)은 출력 토큰이 부족하면 thinking 에 토큰을 소진해 본문이
    빈 채로 잘릴 수 있으므로, JSON 강제(response_mime_type) + 넉넉한 토큰을 둔다.
    """
    if not PROJECT_ID or not (prompt or "").strip():
        return {}
    used = (model or "").strip() or ALLMEET_CHAT_MODEL
    try:
        from vertexai.generative_models import GenerationConfig

        resp = _get_model(used).generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.1,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        text = str(getattr(resp, "text", "") or "")
    except Exception as e:  # noqa: BLE001 - 폴백을 위해 광범위 캐치
        logger.warning("agent LLM 호출 실패 model=%s prompt_chars=%d: %s", used, len(prompt), e)
        return {}
    if not text.strip():
        logger.warning(
            "agent LLM 빈 응답 model=%s prompt_chars=%d finish_reason=%s",
            used, len(prompt), _finish_reason(resp),
        )
        return {}
    logger.info("agent LLM ok model=%s prompt_chars=%d resp_chars=%d", used, len(prompt), len(text))
    return _extract_json(text)


def _finish_reason(resp: Any) -> str:
    """빈 응답 디버깅용 — 응답의 finish_reason 을 안전하게 추출."""
    try:
        return str(resp.candidates[0].finish_reason)
    except Exception:  # noqa: BLE001
        return "?"


def generate_text(
    prompt: str,
    *,
    max_output_tokens: int = 1200,
    temperature: float = 0.4,
    model: str | None = None,
) -> str:
    """프롬프트를 보내고 응답 텍스트(평문)를 반환. 실패 시 빈 문자열.

    plan/repair 의 ``generate_json`` 과 달리 콘텐츠 생성용으로, per-call
    ``generation_config`` 로 토큰/temperature 를 높여 호출한다. ``model`` 미지정 시 flash.
    """
    if not PROJECT_ID or not (prompt or "").strip():
        return ""
    used = (model or "").strip() or ALLMEET_CHAT_MODEL
    try:
        from vertexai.generative_models import GenerationConfig

        resp = _get_model(used).generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=temperature, max_output_tokens=max_output_tokens
            ),
        )
        return str(getattr(resp, "text", "") or "").strip()
    except Exception as e:  # noqa: BLE001 - 폴백을 위해 광범위 캐치
        logger.warning("agent generate_text 실패 model=%s: %s", used, e)
        return ""


def _extract_json(text: str) -> dict[str, Any]:
    """텍스트에서 최외곽 ``{...}`` 를 찾아 json.loads. 실패 시 {}.

    코드펜스(```json ... ```)로 감싸진 경우도 처리한다.
    """
    if not text:
        return {}
    stripped = text.strip()
    # 코드펜스 제거
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    m = re.search(r"\{[\s\S]*\}", stripped)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError) as e:
        logger.warning("agent JSON 파싱 실패: %s", e)
        return {}
