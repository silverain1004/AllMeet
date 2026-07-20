"""명료화 대기 상태 — 되물음 후속 답변을 원 요청과 병합·재계획하는 플로우 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_clarification_replan_success_builds_approval_card():
    """병합된 요청으로 계획이 서면 승인 카드 + 명료화 레코드 정리."""
    from domains.agent import actions

    existing = {"plan_id": "c1", "goal": "지난주 회의록 찾아서 요약해줘", "clarify_attempts": 1}
    plan_with_steps = {
        "goal": "E-BIZ 회의록 요약",
        "steps": [{"n": 1, "tool": "search_confluence", "why": "검색"}],
    }

    with patch(
        "domains.agent.actions.create_plan", return_value=plan_with_steps
    ) as create, patch(
        "domains.agent.actions.store.save_plan", return_value="p99"
    ) as save_plan, patch(
        "domains.agent.actions.store.clear_clarification"
    ) as clear, patch(
        "domains.agent.memory.load_user_memory", return_value=[]
    ), patch(
        "domains.agent.memory.format_memory_block", return_value=""
    ), patch(
        "domains.agent.recipes.recall_recipes", return_value=[]
    ):
        out = actions._build_clarify_response(
            "E-BIZ 시스템 관련",
            existing=existing,
            user_email="u@x.com",
            user_name="U",
            space_name="spaces/AAA",
            ctx_block="",
        )

    # 원 요청 + 답변이 병합되어 계획 생성에 쓰였는지
    merged = create.call_args.args[0]
    assert "지난주 회의록" in merged and "E-BIZ 시스템 관련" in merged
    clear.assert_called_once_with("c1")
    save_plan.assert_called_once()
    assert "cardsV2" in out


def test_clarification_replan_still_missing_saves_followup():
    """여전히 정보 부족이면 attempts 를 올린 새 명료화 레코드를 저장."""
    from domains.agent import actions

    existing = {"plan_id": "c1", "goal": "회의록 요약", "clarify_attempts": 1}
    plan_no_steps = {"steps": [], "ask_user": "어떤 회의인가요?"}

    with patch("domains.agent.actions.create_plan", return_value=plan_no_steps), patch(
        "domains.agent.actions.store.clear_clarification"
    ) as clear, patch(
        "domains.agent.actions.store.save_clarification", return_value="c2"
    ) as save_clar, patch(
        "domains.agent.memory.load_user_memory", return_value=[]
    ), patch(
        "domains.agent.memory.format_memory_block", return_value=""
    ), patch(
        "domains.agent.recipes.recall_recipes", return_value=[]
    ):
        out = actions._build_clarify_response(
            "음 그냥",
            existing=existing,
            user_email="u@x.com",
            user_name="U",
            space_name="spaces/AAA",
            ctx_block="",
        )

    clear.assert_called_once_with("c1")
    save_clar.assert_called_once()
    assert save_clar.call_args.kwargs.get("attempts") == 2
    assert "🤔" in out.get("text", "")


def test_clarification_gives_up_after_cap():
    """재명료화 상한 도달 시 새 레코드 저장 없이 정중히 종료."""
    from domains.agent import actions

    existing = {"plan_id": "c1", "goal": "회의록 요약", "clarify_attempts": 3}
    plan_no_steps = {"steps": [], "ask_user": "어떤 회의인가요?"}

    with patch("domains.agent.actions.create_plan", return_value=plan_no_steps), patch(
        "domains.agent.actions.store.clear_clarification"
    ) as clear, patch(
        "domains.agent.actions.store.save_clarification"
    ) as save_clar, patch(
        "domains.agent.memory.load_user_memory", return_value=[]
    ), patch(
        "domains.agent.memory.format_memory_block", return_value=""
    ), patch(
        "domains.agent.recipes.recall_recipes", return_value=[]
    ):
        out = actions._build_clarify_response(
            "음",
            existing=existing,
            user_email="u@x.com",
            user_name="U",
            space_name="spaces/AAA",
            ctx_block="",
        )

    clear.assert_called_once_with("c1")
    save_clar.assert_not_called()
    assert "기능 이름" in out.get("text", "")


def test_should_consume_clarification_true_for_plain_answer():
    import main

    clar = {"plan_id": "c1", "created_at": _now_iso()}
    assert main._should_consume_clarification("E-BIZ 시스템 관련", clar, "") is True


def test_should_consume_clarification_false_for_new_deterministic_task():
    import main

    clar = {"plan_id": "c1", "created_at": _now_iso()}
    # 새 발화가 결정적 라우팅(회의실 예약)을 트리거 → 답변이 아니라 새 작업
    assert main._should_consume_clarification("회의실 예약해줘", clar, "") is False


def test_should_consume_clarification_false_when_expired():
    import main

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    clar = {"plan_id": "c1", "created_at": old}
    assert main._should_consume_clarification("E-BIZ 시스템 관련", clar, "") is False
