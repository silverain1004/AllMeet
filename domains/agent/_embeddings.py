"""에이전트용 텍스트 임베딩 + 코사인 유사도.

``expert_finder/scoring.py`` 와 동일한 Vertex 모델(text-multilingual-embedding-002)·패턴을
재사용한다(추후 scoring.py 도 이 모듈로 흡수 가능 — 이번 범위 밖). 실패 시 None/0.0 으로
안전하게 degrade 해, 호출 측(레시피 메모리)이 임베딩 없이도 본 흐름을 막지 않게 한다.
"""

from __future__ import annotations

import logging
import math
import threading

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL_NAME = "text-multilingual-embedding-002"
_emb_lock = threading.Lock()
_emb_model = None


def _get_model():
    global _emb_model
    if _emb_model is not None:
        return _emb_model
    with _emb_lock:
        if _emb_model is not None:
            return _emb_model
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        from config.settings import LOCATION, PROJECT_ID

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        _emb_model = TextEmbeddingModel.from_pretrained(_EMBEDDING_MODEL_NAME)
    return _emb_model


def embed(text: str, *, is_query: bool = False) -> list[float] | None:
    """텍스트 임베딩 벡터. 빈 입력/실패 시 None(안전 degrade).

    is_query=True 면 RETRIEVAL_QUERY(회상 질의), False 면 RETRIEVAL_DOCUMENT(저장 문서).
    """
    t = (text or "").strip()
    if not t:
        return None
    try:
        model = _get_model()
        # 배포 환경의 vertexai SDK 는 get_embeddings 에 task_type 인자를 받지 않는다
        # (kwarg 전달 시 TypeError). task_type 없이 호출해도 코사인 유사도엔 충분하다.
        # (is_query 는 API 호환을 위해 남겨두되 현재 사용하지 않음.)
        vec = model.get_embeddings([t])[0].values
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001 - 폴백을 위해 광범위 캐치
        logger.warning("embed 실패 (None 폴백): %s", e)
        return None


def cosine_sim(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0
