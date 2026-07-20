"""팀 공유 절차적 레시피 메모리 — Firestore ``agent_recipes`` 컬렉션.

검증된 성공 계획을 '레시피'(어떤 요청에 어떤 도구 순서가 통했는지)로 축적해, 유사한 미래
요청에 few-shot 으로 회상·주입한다. 한 팀원의 사용이 팀 전체 플래너를 똑똑하게 만든다.

``memory.py``(record/load/format) 패턴을 미러링하되, 의미 회상을 위해 임베딩(``_embeddings``)을
함께 저장한다. 스코프당 단일 문서 + ``recipes`` 배열 필드(중복 병합·prune 을 파이썬에서 처리).

가드레일: 검증 성공만 기록(호출 측이 보장) · args 값 미저장(키·$ref 구조만) · 중복 병합 +
success_count · 상한 prune · 회상 top-K + 유사도 임계값 · env 킬스위치 · 임베딩 실패 시 no-op.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from firestore.documents import document_ref

logger = logging.getLogger(__name__)

COLLECTION = "agent_recipes"

_DEDUP_SIM = 0.92      # 이 이상 유사하면 같은 레시피로 보고 success_count 병합
_RECALL_MIN_SIM = 0.78  # 회상 시 이 미만 유사도는 무관한 것으로 제외
_RECALL_K = 3
_MAX_RECIPES = 40       # 스코프당 보관 상한(문서 크기·프롬프트 예산)
_WHY_MAX = 120


def _enabled() -> bool:
    """런타임 토글 — env 직접 확인(restart 없이 끄기)."""
    return os.environ.get("ALLMEET_RECIPE_MEMORY", "1").strip().lower() not in ("0", "false", "no", "off")


def _scope_key(user_email: str) -> str:
    """팀 공유 스코프. 소속팀이 있으면 team_{id}, 없으면 user_{email} 폴백. 실패 시 빈 문자열."""
    email = (user_email or "").strip()
    if not email:
        return ""
    try:
        from firestore.team_config import find_team_by_email

        team_id = find_team_by_email(email)
    except Exception:  # noqa: BLE001
        team_id = None
    if team_id:
        return f"team_{team_id}"
    from domains.agent.memory import memory_doc_id

    return f"user_{memory_doc_id(email)}"


def _collect_refs(args: Any) -> list[str]:
    """args 트리에서 {"$ref": "..."} 의 ref 문자열만 수집(값 아님 — 구조 신호)."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if set(v.keys()) == {"$ref"}:
                out.append(str(v["$ref"]))
            else:
                for x in v.values():
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(args)
    return out


def _sanitize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """계획 steps → 값 없는 절차 구조(tool/why/arg_keys/$ref). 프라이버시 + 일반화."""
    out: list[dict[str, Any]] = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        tool = str(s.get("tool") or "").strip()
        if not tool:
            continue
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        out.append(
            {
                "n": s.get("n"),
                "tool": tool,
                "why": str(s.get("why") or "")[:_WHY_MAX],
                "arg_keys": sorted(str(k) for k in args.keys()),
                "refs": _collect_refs(args),
            }
        )
    return out


def _merge_recipe(
    recipes: list[dict[str, Any]],
    *,
    goal: str,
    tool_seq: list[dict[str, Any]],
    vec: list[float],
    planner_model: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """기존 레시피와 임베딩이 충분히 유사하면 병합(success_count++), 아니면 새로 추가."""
    from domains.agent._embeddings import cosine_sim

    for r in recipes:
        if cosine_sim(vec, r.get("embedding") or []) >= _DEDUP_SIM:
            r["success_count"] = int(r.get("success_count") or 1) + 1
            r["updated_at"] = now
            r["tool_sequence"] = tool_seq  # 도구 진화 반영(최신 성공으로 갱신)
            r["goal"] = goal
            r["embedding"] = vec
            return recipes
    recipes.append(
        {
            "goal": goal,
            "tool_sequence": tool_seq,
            "embedding": vec,
            "success_count": 1,
            "planner_model": planner_model,
            "created_at": now,
            "updated_at": now,
        }
    )
    return recipes


def _prune(recipes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """상한 초과 시 success_count 상위만 보관(인기 레시피 부상, 비대 방지)."""
    if len(recipes) <= _MAX_RECIPES:
        return recipes
    return sorted(recipes, key=lambda r: int(r.get("success_count") or 1), reverse=True)[:_MAX_RECIPES]


def record_recipe(
    user_email: str,
    goal: str,
    steps: list[dict[str, Any]],
    planner_model: str = "",
) -> None:
    """검증된 성공 계획을 팀 레시피로 적재. 실패해도 본 흐름을 막지 않는다.

    호출 측(orchestrator)이 verify achieved=True 일 때만 호출하는 것을 전제로 한다.
    """
    if not _enabled():
        return
    goal = (goal or "").strip()
    tool_seq = _sanitize_steps(steps)
    if not goal or not tool_seq:
        return
    scope = _scope_key(user_email)
    if not scope:
        return
    from domains.agent._embeddings import embed

    vec = embed(goal, is_query=False)
    if vec is None:  # 임베딩 실패 → 학습 skip(안전)
        return
    try:
        ref = document_ref(COLLECTION, scope)
        snap = ref.get()
        now = datetime.utcnow()
        if snap.exists:
            recipes = list((snap.to_dict() or {}).get("recipes") or [])
            merged = _prune(
                _merge_recipe(
                    recipes, goal=goal, tool_seq=tool_seq, vec=vec, planner_model=planner_model, now=now
                )
            )
            ref.update({"recipes": merged, "updated_at": now})
        else:
            ref.set(
                {
                    "scope": scope,
                    "created_at": now,
                    "updated_at": now,
                    "recipes": [
                        {
                            "goal": goal,
                            "tool_sequence": tool_seq,
                            "embedding": vec,
                            "success_count": 1,
                            "planner_model": planner_model,
                            "created_at": now,
                            "updated_at": now,
                        }
                    ],
                }
            )
    except Exception:  # noqa: BLE001
        logger.warning("record_recipe 실패 scope=%s", scope, exc_info=True)


def recall_recipes(
    user_email: str,
    request: str,
    *,
    k: int = _RECALL_K,
    min_sim: float = _RECALL_MIN_SIM,
) -> list[dict[str, Any]]:
    """요청과 의미상 유사한 팀 레시피 top-k(유사도 임계값 이상). 실패 시 빈 리스트."""
    if not _enabled():
        return []
    request = (request or "").strip()
    if not request:
        return []
    scope = _scope_key(user_email)
    if not scope:
        return []
    from domains.agent._embeddings import cosine_sim, embed

    qvec = embed(request, is_query=True)
    if qvec is None:
        return []
    try:
        snap = document_ref(COLLECTION, scope).get()
        if not snap.exists:
            return []
        recipes = (snap.to_dict() or {}).get("recipes") or []
    except Exception:  # noqa: BLE001
        logger.warning("recall_recipes 실패 scope=%s", scope, exc_info=True)
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in recipes:
        sim = cosine_sim(qvec, r.get("embedding") or [])
        if sim >= min_sim:
            scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:k]]


def format_recipes_block(recipes: list[dict[str, Any]]) -> str:
    """recall_recipes 결과 → 플래너 few-shot 텍스트. 비어 있으면 빈 문자열."""
    if not recipes:
        return ""
    lines: list[str] = []
    for r in recipes:
        goal = str(r.get("goal") or "").strip()
        seq = r.get("tool_sequence") or []
        chain = " → ".join(
            str(s.get("tool")) for s in seq if isinstance(s, dict) and s.get("tool")
        )
        if goal and chain:
            lines.append(f"- 요청 '{goal}' → {chain}")
    return "\n".join(lines)
