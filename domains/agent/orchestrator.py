"""Phase C — 승인된 계획 실행 + 자가복구 루프.

핵심 규칙:
- ``execute_plan`` 은 status == approved 일 때만 실행을 시작한다(승인 없이는 어떤 도구도 실행 X).
- 이미 결과가 있는 단계는 건너뛴다(재승인 후 resume 지원).
- 복구 가능한 오류는 repair 서브루프로 스스로 고치고, 사람만 해결 가능한 오류/한도 초과는
  중단하고 사용자에게 넘긴다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from domains.agent import config, repair, store
from domains.agent.tools import get_tool

logger = logging.getLogger(__name__)

# run_loop 결과 outcome
OUTCOME_DONE = "done"
OUTCOME_FAILED = "failed"
OUTCOME_NEEDS_USER = "needs_user"
OUTCOME_NEEDS_REAPPROVAL = "needs_reapproval"


def _ref_paths(args: Any, prefix: str = "") -> list[tuple[str, str]]:
    """args 트리에서 ($ref 경로, dotted ref) 쌍을 수집."""
    out: list[tuple[str, str]] = []
    if isinstance(args, dict):
        if set(args.keys()) == {"$ref"}:
            out.append((prefix or "$ref", str(args["$ref"])))
            return out
        for k, v in args.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_ref_paths(v, p))
    elif isinstance(args, list):
        for i, v in enumerate(args):
            out.extend(_ref_paths(v, f"{prefix}[{i}]"))
    return out


def _step_result(results: dict[Any, Any], step_num: str) -> dict[str, Any] | None:
    cur = results.get(step_num)
    if cur is None:
        try:
            cur = results.get(int(step_num))
        except (ValueError, TypeError):
            cur = None
    return cur if isinstance(cur, dict) else None


def _check_ref_args(step: dict[str, Any], results: dict[Any, Any]) -> str | None:
    """$ref 가 미해결·생략 단계를 가리키면 사용자 안내용 메시지를 반환."""
    raw_args = step.get("args") or {}
    for path, ref in _ref_paths(raw_args):
        parts = ref.split(".")
        if not parts:
            return f"단계 참조({path})를 해석하지 못했어요."
        head = parts[0]
        upstream = _step_result(results, head)
        if upstream and upstream.get("skipped"):
            return f"이전 단계({head})가 생략되어 `{path}` 참조를 이어갈 수 없어요."
        resolved = _resolve_ref(ref, results)
        if resolved is None or (isinstance(resolved, str) and not resolved.strip()):
            return f"이전 단계 결과 참조(`{ref}`)를 해석하지 못했어요. 자료가 없을 수 있습니다."
    return None


def resolve_args(args: Any, results: dict[Any, Any]) -> Any:
    """late-binding — args 안의 {"$ref": "<n>.<dotted.path>"} 를 이전 결과로 치환."""
    if isinstance(args, dict):
        if set(args.keys()) == {"$ref"}:
            return _resolve_ref(str(args["$ref"]), results)
        return {k: resolve_args(v, results) for k, v in args.items()}
    if isinstance(args, list):
        return [resolve_args(v, results) for v in args]
    return args


def _resolve_ref(ref: str, results: dict[Any, Any]) -> Any:
    parts = ref.split(".")
    if not parts:
        return None
    head = parts[0]
    cur: Any = results.get(head)
    if cur is None:
        try:
            cur = results.get(int(head))
        except (ValueError, TypeError):
            cur = None
    for p in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
    return cur


def run_loop(
    *,
    goal: str,
    steps: list[dict[str, Any]],
    results: dict[Any, Any] | None = None,
    push: Callable[[str], None] | None = None,
    plan_id: str = "",
) -> dict[str, Any]:
    """순수 실행 루프(Firestore 비의존, 테스트 가능).

    Returns:
        {"outcome", "results", "message", "ask"(needs_user 시), "steps"(reapproval 시),
         "trace"(step별 실행 기록)}

    trace 는 어느 경로로 끝나든(성공/실패/중단) 부착되도록, 내부 루프가 in-place 로
    누적한 list 를 종료 후 outcome 에 합쳐 반환한다(관측용 — Phase 0).
    """
    trace: list[dict[str, Any]] = []
    outcome = _run_loop_inner(
        goal=goal, steps=steps, results=results, push=push, plan_id=plan_id, trace=trace
    )
    outcome["trace"] = trace
    return outcome


def _run_loop_inner(
    *,
    goal: str,
    steps: list[dict[str, Any]],
    results: dict[Any, Any] | None,
    push: Callable[[str], None] | None,
    plan_id: str,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    results = dict(results or {})
    push = push or (lambda _msg: None)
    total = len(steps)
    repairs = 0
    react_inserts = 0

    for i, original_step in enumerate(steps):
        n = original_step.get("n", i + 1)
        if n in results or str(n) in results:
            continue  # resume: 이미 완료된 단계 건너뜀
        if i >= config.MAX_STEPS:
            return _fail(results, f"단계가 너무 많아({config.MAX_STEPS}개 한도) 중단했어요. 요청을 나눠 주세요.")

        step = original_step
        attempt = 0
        while True:
            tool = get_tool(str(step.get("tool") or ""))
            if tool is None:
                return _fail(results, f"알 수 없는 도구입니다: {step.get('tool')}")

            latency_ms = 0
            ref_issue = _check_ref_args(step, results)
            if ref_issue:
                obs = {"ok": False, "error_kind": "ref_unresolved", "detail": ref_issue}
            else:
                args = resolve_args(step.get("args") or {}, results)
                t0 = time.monotonic()
                try:
                    obs = tool.run(**args)
                except Exception as e:  # noqa: BLE001
                    obs = {"ok": False, "error_kind": "tool_exception", "detail": str(e)}
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "agent step plan_id=%s n=%s tool=%s ok=%s error_kind=%s latency_ms=%d",
                    plan_id, n, step.get("tool"), obs.get("ok"),
                    obs.get("error_kind", ""), latency_ms,
                )

            trace.append(
                {
                    "n": n,
                    "tool": step.get("tool"),
                    "attempt": attempt,
                    "ok": bool(obs.get("ok")),
                    "error_kind": str(obs.get("error_kind") or ""),
                    "latency_ms": latency_ms,
                    "side_effect": bool(step.get("side_effect")),
                }
            )

            if obs.get("ok"):
                if obs.get("found") is False:
                    query = str((step.get("args") or {}).get("query") or "")
                    return _needs_user(
                        results,
                        (
                            "검색 결과가 없어요"
                            + (f" ('{query}')" if query else "")
                            + ". 검색어를 바꿔 주시거나 다른 키워드를 알려주세요."
                        ),
                    )
                results[n] = obs
                push(f"✅ {i + 1}/{total} {step.get('why') or step.get('tool')} 완료")
                break

            # --- 실패 처리 ---
            kind = str(obs.get("error_kind") or "")
            detail = str(obs.get("detail") or "")
            category = repair.classify_error(kind)

            if category == repair.CAT_USER_REQUIRED:
                return _needs_user(
                    results,
                    f"이 작업은 권한 연결이 필요해요(`{kind}`). '내 데이터 연결' 후 다시 시도해 주세요.",
                )

            if attempt >= config.MAX_RETRIES_PER_STEP or repairs >= config.MAX_TOTAL_REPAIRS:
                return _needs_user(
                    results,
                    f"`{step.get('tool')}` 단계를 여러 번 시도했지만 실패했어요(`{kind}`). 어떻게 진행할까요?",
                )

            attempt += 1
            repairs += 1

            if category == repair.CAT_RETRY:
                backoff = config.RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                push(f"⚠️ {i + 1}/{total} 일시 오류 → 자동 재시도 중 ({attempt}/{config.MAX_RETRIES_PER_STEP})")
                time.sleep(min(backoff, 10.0))
                continue  # 동일 step 재시도

            # CAT_LLM_FIX — LLM 복구안
            fix = repair.llm_repair(
                goal=goal,
                approved_steps=steps,
                results=results,
                failed_step=step,
                error_kind=kind,
                detail=detail,
            )
            push(f"🛠 {i + 1}/{total} 문제를 스스로 보정하는 중… ({fix.get('action')})")

            if fix.get("action") == "abort":
                return _needs_user(
                    results,
                    f"`{step.get('tool')}` 단계를 자동으로 복구하지 못했어요. 도움이 필요해요.",
                )
            if fix.get("action") == "skip":
                if not repair.skip_allowed(kind, results=results, failed_step=step):
                    return _needs_user(
                        results,
                        f"`{step.get('tool')}` 단계를 건너뛸 수 없어요. ({detail or kind})",
                    )
                results[n] = {"ok": True, "skipped": True, "reason": str(fix.get("reason") or "")}
                push(f"⚠️ {i + 1}/{total} {step.get('why') or step.get('tool')} 생략")
                break

            # 안전 경계 — 승인 범위 밖 새 side_effect 면 재승인
            if repair.introduces_new_side_effect(fix, approved_steps=steps):
                patched = repair.apply_fix(step, fix)
                new_steps = _replace_step(steps, n, patched)
                return {
                    "outcome": OUTCOME_NEEDS_REAPPROVAL,
                    "results": results,
                    "steps": new_steps,
                    "message": (
                        f"진행 중 계획을 바꿔야 해요: `{patched.get('tool')}` 실행이 필요합니다. "
                        "승인하시면 이어서 진행할게요."
                    ),
                }

            step = repair.apply_fix(step, fix)
            continue  # 보정된 step 재시도

        # --- ReAct: 결과 관찰 후 다음 단계 동적 보강 (조회/생성만 자동, 새 쓰기는 재승인) ---
        if config.react_enabled() and react_inserts < config.MAX_REACT_INSERTS and results.get(n, {}).get("ok"):
            from domains.agent import planner

            decision = planner.decide_next_step(goal=goal, approved_steps=steps, results=results)
            if decision.get("action") == "insert_step" and decision.get("step"):
                new_step = dict(decision["step"])
                inserted = {**new_step, "n": _next_n(steps)}
                if repair.is_unapproved_side_effect(new_step.get("tool", ""), approved_steps=steps):
                    new_steps = steps[: i + 1] + [inserted] + steps[i + 1 :]
                    return {
                        "outcome": OUTCOME_NEEDS_REAPPROVAL,
                        "results": results,
                        "steps": new_steps,
                        "message": (
                            f"진행 중 결과를 보니 `{inserted.get('tool')}` 실행이 필요해요. "
                            "승인하시면 이어서 진행할게요."
                        ),
                    }
                steps.insert(i + 1, inserted)
                react_inserts += 1
                push(f"🔍 결과를 보고 단계를 추가합니다 — {inserted.get('why') or inserted.get('tool')}")

    return {"outcome": OUTCOME_DONE, "results": results, "message": _summarize(goal, results)}


def verify_outcome(goal: str, results: dict[Any, Any]) -> dict[str, Any]:
    """Phase 1B — 실행 결과가 목표를 충족했는지 LLM 으로 판정. 실패 시 achieved=False(fail-closed)."""
    from domains.agent._llm import PLANNER_MODEL, generate_json
    from domains.agent.prompts import build_verify_prompt

    digest = _results_digest(results)
    data = generate_json(build_verify_prompt(goal=goal, results_digest=digest), model=PLANNER_MODEL)
    if not data:
        return {"achieved": False, "reason": "검증 불가"}
    return {
        "achieved": bool(data.get("achieved", False)),
        "reason": str(data.get("reason") or ""),
    }


def _results_digest(results: dict[Any, Any], *, limit: int = 3000) -> str:
    """검증용 요약 — skipped/found/count 등 핵심 필드를 우선 포함."""
    import json

    summary: list[dict[str, Any]] = []
    for key in sorted(results.keys(), key=lambda x: str(x)):
        val = results[key]
        if not isinstance(val, dict):
            continue
        row: dict[str, Any] = {"step": key, "ok": val.get("ok"), "tool_hint": val.get("tool")}
        if val.get("skipped"):
            row["skipped"] = True
        if "found" in val:
            row["found"] = val.get("found")
        if "count" in val:
            row["count"] = val.get("count")
        if val.get("page_id"):
            row["page_id"] = val.get("page_id")
        if val.get("content"):
            row["content_preview"] = str(val.get("content") or "")[:200]
        if val.get("error_kind"):
            row["error_kind"] = val.get("error_kind")
        summary.append(row)
    try:
        return json.dumps(summary, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(summary)[:limit]


def _next_n(steps: list[dict[str, Any]]) -> int:
    """ReAct 삽입 단계용 고유 정수 n (기존 최대 n + 1)."""
    max_n = 0
    for s in steps:
        try:
            max_n = max(max_n, int(s.get("n")))
        except (TypeError, ValueError):
            continue
    return max_n + 1


def _replace_step(steps: list[dict[str, Any]], n: Any, patched: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        out.append(patched if s.get("n") == n else s)
    return out


def _fail(results: dict[Any, Any], message: str) -> dict[str, Any]:
    return {"outcome": OUTCOME_FAILED, "results": results, "message": message}


def _needs_user(results: dict[Any, Any], ask: str) -> dict[str, Any]:
    return {"outcome": OUTCOME_NEEDS_USER, "results": results, "message": ask, "ask": ask}


def _extract_deliverable(results: dict[Any, Any]) -> str:
    """generate_content 등 최종 산출물 텍스트."""
    candidates: list[tuple[int, str]] = []
    for key, val in results.items():
        if not isinstance(val, dict) or not val.get("ok") or val.get("skipped"):
            continue
        text = str(val.get("content") or "").strip()
        if not text:
            continue
        try:
            sort_key = int(key)
        except (TypeError, ValueError):
            sort_key = 0
        candidates.append((sort_key, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def _summarize(goal: str, results: dict[Any, Any]) -> str:
    done = len([1 for v in results.values() if isinstance(v, dict) and v.get("ok")])
    lines = [f"🎉 '{goal}' 작업을 마쳤어요. (완료 단계 {done}개)"]
    deliverable = _extract_deliverable(results)
    if deliverable:
        lines.extend(["", deliverable])
    return "\n".join(lines)


def execute_plan(plan_id: str, *, push: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Firestore 영속 계획을 로드해 실행. status==approved 검증 강제."""
    plan = store.load_plan(plan_id)
    if plan is None:
        return _fail({}, "계획을 찾을 수 없습니다.")
    if plan.get("status") != store.STATUS_APPROVED:
        logger.warning("execute_plan 거부 — status=%s plan_id=%s", plan.get("status"), plan_id)
        return _fail({}, "승인되지 않은 계획은 실행할 수 없습니다.")

    store.set_status(plan_id, store.STATUS_RUNNING)
    outcome = run_loop(
        goal=plan.get("goal") or "",
        steps=plan.get("steps") or [],
        results=plan.get("results") or {},
        push=push,
        plan_id=plan_id,
    )

    # 결과 영속화 (키를 문자열로 정규화해 JSON 직렬화)
    store.save_results(plan_id, {str(k): v for k, v in (outcome.get("results") or {}).items()})
    store.save_trace(plan_id, outcome.get("trace") or [])

    kind = outcome.get("outcome")
    if kind == OUTCOME_DONE:
        verdict = verify_outcome(plan.get("goal") or "", outcome.get("results") or {})
        if not verdict.get("achieved"):
            reason = str(verdict.get("reason") or "목표를 충분히 달성하지 못했습니다.")
            outcome["outcome"] = OUTCOME_NEEDS_USER
            outcome["message"] = f"⚠️ 목표를 달성하지 못했어요: {reason}"
            store.set_status(plan_id, store.STATUS_FAILED, error=reason)
            return outcome
        from domains.agent import memory, recipes

        memory.record_artifacts(
            plan.get("user_email") or "",
            plan.get("goal") or "",
            outcome.get("results") or {},
        )
        # 검증된 성공 계획을 팀 레시피로 학습 — 유사 요청에 few-shot 으로 회상(쓸수록 똑똑해지는 루프).
        try:
            recipes.record_recipe(
                plan.get("user_email") or "",
                plan.get("goal") or "",
                plan.get("steps") or [],
                plan.get("planner_model") or "",
            )
        except Exception:  # noqa: BLE001 - 학습 실패가 본 작업을 막지 않도록
            logger.warning("record_recipe 훅 실패", exc_info=True)
        store.set_status(plan_id, store.STATUS_DONE)
    elif kind == OUTCOME_NEEDS_REAPPROVAL:
        store.update_steps(plan_id, outcome.get("steps") or [])
        store.set_status(plan_id, store.STATUS_PENDING_REAPPROVAL)
    else:  # failed / needs_user
        store.set_status(plan_id, store.STATUS_FAILED, error=outcome.get("message", ""))

    return outcome
