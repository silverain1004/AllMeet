"""후보별 점수 계산.

PLAN.md §5.1 `scoring.py`.

소스·역할별 가중치 × 최근성 가중. 한 사람·한 소스당 최대 10 hit cap (노이즈 폭증 방지).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

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


def score_candidates(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """hit 리스트 → 후보별 집계 dict 리스트 (점수 내림차순).

    Args:
        hits: ``search_public`` 의 hit 리스트.

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
        hit_score = weight * recency
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

    out = list(by_email.values())
    out.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        "expert_finder scoring candidates=%d top_score=%s",
        len(out),
        f"{out[0]['score']:.2f}" if out else "0",
    )
    return out


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
