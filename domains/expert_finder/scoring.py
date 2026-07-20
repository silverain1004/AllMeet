"""후보별 점수 계산.

PLAN.md §5.1 `scoring.py`.

소스·역할별 가중치 × 최근성 가중 × 키워드 관련성 가중.
hit_score = role_weight × recency_factor × relevance_factor
한 사람·한 소스당 최대 5 hit cap (노이즈 폭증 방지).
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Vertex AI 텍스트 임베딩 — 키워드 ↔ 문서 제목 코사인 유사도 계산용.
_EMBEDDING_MODEL_NAME = "text-multilingual-embedding-002"
_emb_lock = threading.Lock()
_emb_model = None


def _get_emb_model():
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


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return dot / norm if norm > 0 else 0.0


def _compute_title_similarities(keyword: str, titles: list[str]) -> dict[str, float]:
    """키워드와 제목 목록의 코사인 유사도 계산. 실패 시 빈 dict 반환 → 호출 측이 1.0으로 폴백."""
    unique_titles = list({t for t in titles if t})
    if not keyword or not unique_titles:
        return {}
    try:
        model = _get_emb_model()
        # 배포 SDK 의 get_embeddings 는 task_type 인자를 받지 않는다(TypeError) → 빼고 호출.
        kw_vec = model.get_embeddings([keyword])[0].values
        title_vecs = model.get_embeddings(unique_titles)
        return {t: _cosine_sim(kw_vec, tv.values) for t, tv in zip(unique_titles, title_vecs)}
    except Exception as e:
        logger.warning("expert_finder embedding 유사도 실패 (폴백 1.0): %s", e)
        return {}


def _relevance_factor(similarity: float) -> float:
    """코사인 유사도(0~1) → 가중치 승수.
    선형 매핑: sim 0.0 → 0.3 (관련 없음 패널티), sim 1.0 → 1.5 (관련성 높음 부스트).
    """
    return 0.3 + similarity * 1.2


# (source, role) → base_weight. 누락 키는 1.0 (그 외 카테고리).
_ROLE_WEIGHTS: dict[tuple[str, str], float] = {
    ("drive", "owner"): 3.0,
    ("drive", "modifier"): 2.0,
    ("confluence", "creator"): 3.0,
    ("confluence", "modifier"): 2.0,
    ("calendar", "organizer"): 2.0,
    ("calendar", "creator"): 2.0,
    ("gmail", "sender"): 2.0,  # 본인 발신 메일만 hit (받은 메일은 search 단계 skip).
    ("personal_calendar", "organizer"): 2.0,  # 본인 주최 일정.
    ("personal_calendar", "creator"): 2.0,
}
_DEFAULT_WEIGHT = 1.0

# 한 사람·한 소스당 최대 카운트 hit 수.
# 10 → 5 로 축소 (M2 디버깅): 회의록 정리하는 사람이 같은 종류 페이지 10개로 폭주하는 문제 완화.
_PER_SOURCE_CAP = 5


def score_candidates(hits: list[dict[str, Any]], keyword: str = "") -> list[dict[str, Any]]:
    """hit 리스트 → 후보별 집계 dict 리스트 (점수 내림차순).

    Args:
        hits: ``search_public`` 의 hit 리스트.
        keyword: 검색 키워드. 제공 시 제목 임베딩 유사도를 점수에 반영.

    Returns:
        ``[{
            "email": "...",
            "name": "...",                   # hit 중 가장 잘 채워진 displayName
            "score": float,
            "hits_by_source": {"drive": 3, "confluence": 1, ...},
            "evidence": [hit, hit, ...]      # 점수 높은 순 최대 5개
        }]``
    """
    if not hits:
        return []

    # 키워드 ↔ 제목 유사도 일괄 계산 (실패 시 빈 dict → relevance=1.0 폴백).
    title_sim: dict[str, float] = {}
    if keyword:
        titles = [h.get("title") or "" for h in hits]
        title_sim = _compute_title_similarities(keyword, titles)

    by_email: dict[str, dict[str, Any]] = {}
    # 같은 사람·같은 소스 hit 카운트 (cap 적용용)
    source_count: dict[tuple[str, str], int] = {}

    # 점수 높은 hit 가 evidence 우선 노출되도록 hit 별 점수 미리 계산해 정렬.
    enriched: list[tuple[float, dict[str, Any]]] = []
    for h in hits:
        email = (h.get("email") or "").strip().lower()
        if not email:
            continue  # 신원 미해석 hit 은 스코어링에서 제외
        weight = _ROLE_WEIGHTS.get((h.get("source") or "", h.get("role") or ""), _DEFAULT_WEIGHT)
        recency = _recency_factor(h.get("when") or "")
        title = h.get("title") or ""
        relevance = _relevance_factor(title_sim[title]) if title in title_sim else 1.0
        hit_score = weight * recency * relevance
        enriched.append((hit_score, h))

    enriched.sort(key=lambda x: x[0], reverse=True)

    for hit_score, h in enriched:
        email = (h.get("email") or "").strip().lower()
        source = h.get("source") or ""
        key = (email, source)
        if source_count.get(key, 0) >= _PER_SOURCE_CAP:
            continue
        source_count[key] = source_count.get(key, 0) + 1

        entry = by_email.setdefault(
            email,
            {
                "email": email,
                "name": "",
                "score": 0.0,
                "hits_by_source": {},
                "evidence": [],
            },
        )
        entry["score"] += hit_score
        entry["hits_by_source"][source] = entry["hits_by_source"].get(source, 0) + 1
        name = (h.get("name") or "").strip()
        if name and not entry["name"]:
            entry["name"] = name
        if len(entry["evidence"]) < 5:
            entry["evidence"].append(h)

    out = _merge_same_person(list(by_email.values()))
    out.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        "expert_finder scoring candidates=%d top_score=%s",
        len(out),
        f"{out[0]['score']:.2f}" if out else "0",
    )
    return out


def _merge_same_person(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 이름(비어있지 않은) 후보를 하나로 병합 — 동일인이 여러 이메일로 중복 노출되는 것 방지.

    (예: 한 사람이 팀 설정에 여러 이메일로 들어가 있거나 displayName 해석 차이로 2건이 잡힐 때
    rank 2·3 에 같은 사람이 뜨던 문제.) 이름이 없으면 병합하지 않고(이메일 단위) 그대로 둔다.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    passthrough: list[dict[str, Any]] = []
    for e in entries:
        nm = (e.get("name") or "").strip().lower()
        if not nm:
            passthrough.append(e)
            continue
        if nm not in merged:
            merged[nm] = dict(e)
            merged[nm]["hits_by_source"] = dict(e.get("hits_by_source") or {})
            merged[nm]["evidence"] = list(e.get("evidence") or [])
            order.append(nm)
            continue
        tgt = merged[nm]
        tgt["score"] = tgt.get("score", 0.0) + e.get("score", 0.0)
        for src, cnt in (e.get("hits_by_source") or {}).items():
            tgt["hits_by_source"][src] = tgt["hits_by_source"].get(src, 0) + cnt
        if not (tgt.get("email") or "").strip() and (e.get("email") or "").strip():
            tgt["email"] = e["email"]
        tgt["evidence"] = ((tgt.get("evidence") or []) + (e.get("evidence") or []))[:5]
    return [merged[k] for k in order] + passthrough


# 최근성 가중치 — when (ISO8601) 기준.
def _recency_factor(when_iso: str) -> float:
    if not when_iso:
        return 1.0
    try:
        s = when_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return 1.0
    delta_days = (datetime.now(timezone.utc) - dt).days
    if delta_days < 0:
        # 미래 일정 (계획) — 현재로 간주.
        return 1.5
    if delta_days <= 30:
        return 1.5
    if delta_days <= 90:
        return 1.0
    return 0.5
