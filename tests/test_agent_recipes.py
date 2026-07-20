"""팀 공유 절차적 레시피 메모리 — 학습 루프 테스트.

임베딩·Firestore 는 모두 mock. 코사인은 실제 함수를 쓰되 임베딩 벡터를 단순화([1,0]/[0,1])해
유사/비유사를 결정적으로 만든다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

DOC_REF = "domains.agent.recipes.document_ref"
EMBED = "domains.agent._embeddings.embed"
TEAM = "firestore.team_config.find_team_by_email"


class _FakeSnap:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data) if self._data else {}


class _FakeRef:
    def __init__(self, store: dict[str, Any], key: str) -> None:
        self.store = store
        self.key = key

    def get(self) -> _FakeSnap:
        return _FakeSnap(self.store.get(self.key))

    def set(self, data: dict[str, Any]) -> None:
        self.store[self.key] = dict(data)

    def update(self, patch: dict[str, Any]) -> None:
        self.store.setdefault(self.key, {}).update(patch)


def _ref_factory(store: dict[str, Any]):
    return lambda _collection, key: _FakeRef(store, key)


# ---------------------------------------------------------------------------
# record_recipe
# ---------------------------------------------------------------------------


def test_record_recipe_creates_and_sanitizes():
    from domains.agent import recipes

    store: dict[str, Any] = {}
    steps = [
        {"n": 1, "tool": "find_team_weekly_report", "why": "찾기", "args": {"team_name": "PC2팀", "requester_email": "me@x.com"}},
        {"n": 2, "tool": "generate_content", "why": "요약", "args": {"instruction": "요약", "source": {"$ref": "1.content"}, "require_source": True}},
    ]
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="PC2"), patch(
        EMBED, return_value=[1.0, 0.0]
    ):
        recipes.record_recipe("me@x.com", "PC2팀 주간보고 요약", steps, "gemini-2.5-pro")

    assert "team_PC2" in store
    recs = store["team_PC2"]["recipes"]
    assert len(recs) == 1
    r = recs[0]
    assert r["goal"] == "PC2팀 주간보고 요약"
    assert [s["tool"] for s in r["tool_sequence"]] == ["find_team_weekly_report", "generate_content"]
    assert r["tool_sequence"][0]["arg_keys"] == ["requester_email", "team_name"]
    assert r["tool_sequence"][1]["refs"] == ["1.content"]
    # args 값은 저장되지 않는다(키·$ref 구조만) — 프라이버시
    assert "PC2팀" not in str(r["tool_sequence"])
    assert "me@x.com" not in str(r["tool_sequence"])
    assert r["success_count"] == 1


def test_record_recipe_merges_duplicate_increments_count():
    from domains.agent import recipes

    store = {
        "team_PC2": {
            "scope": "team_PC2",
            "recipes": [
                {"goal": "기존", "tool_sequence": [{"tool": "x"}], "embedding": [1.0, 0.0], "success_count": 1}
            ],
        }
    }
    steps = [{"n": 1, "tool": "find_team_weekly_report", "args": {}}]
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="PC2"), patch(
        EMBED, return_value=[1.0, 0.0]  # cosine=1.0 ≥ 0.92 → 병합
    ):
        recipes.record_recipe("me@x.com", "새 요청", steps, "m")

    recs = store["team_PC2"]["recipes"]
    assert len(recs) == 1  # 추가가 아니라 병합
    assert recs[0]["success_count"] == 2
    assert recs[0]["goal"] == "새 요청"  # 최신 성공으로 갱신


def test_record_recipe_user_fallback_when_no_team():
    from domains.agent import recipes

    store: dict[str, Any] = {}
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value=None), patch(
        EMBED, return_value=[1.0, 0.0]
    ):
        recipes.record_recipe("solo@x.com", "g", [{"tool": "x", "args": {}}], "m")
    assert any(k.startswith("user_") for k in store)


def test_record_recipe_embed_failure_is_noop():
    from domains.agent import recipes

    store: dict[str, Any] = {}
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="PC2"), patch(
        EMBED, return_value=None
    ):
        recipes.record_recipe("me@x.com", "g", [{"tool": "x", "args": {}}], "m")
    assert store == {}


# ---------------------------------------------------------------------------
# recall_recipes
# ---------------------------------------------------------------------------


def _two_recipe_store() -> dict[str, Any]:
    return {
        "team_PC2": {
            "recipes": [
                {"goal": "주간보고 요약", "tool_sequence": [{"tool": "find_team_weekly_report"}], "embedding": [1.0, 0.0], "success_count": 3},
                {"goal": "회의실 예약", "tool_sequence": [{"tool": "list_meeting_rooms"}], "embedding": [0.0, 1.0], "success_count": 1},
            ]
        }
    }


def test_recall_recipes_ranks_and_thresholds():
    from domains.agent import recipes

    store = _two_recipe_store()
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="PC2"), patch(
        EMBED, return_value=[1.0, 0.0]  # 질의 ~ 주간보고
    ):
        out = recipes.recall_recipes("me@x.com", "지난주 보고 정리해줘", k=3, min_sim=0.5)
    assert len(out) == 1  # 회의실(cosine 0)은 임계값 미만 제외
    assert out[0]["goal"] == "주간보고 요약"


def test_recall_scoped_by_team():
    from domains.agent import recipes

    store = _two_recipe_store()  # team_PC2 에만 존재
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="MES2"), patch(
        EMBED, return_value=[1.0, 0.0]
    ):
        out = recipes.recall_recipes("other@x.com", "주간보고 요약")
    assert out == []  # 다른 팀 레시피는 회수되지 않음


def test_format_recipes_block():
    from domains.agent import recipes

    block = recipes.format_recipes_block(
        [{"goal": "PC2 주간보고 요약", "tool_sequence": [{"tool": "find_team_weekly_report"}, {"tool": "generate_content"}]}]
    )
    assert "find_team_weekly_report → generate_content" in block
    assert "PC2 주간보고 요약" in block
    assert recipes.format_recipes_block([]) == ""


# ---------------------------------------------------------------------------
# 킬스위치
# ---------------------------------------------------------------------------


def test_kill_switch_disables_record_and_recall(monkeypatch):
    from domains.agent import recipes

    monkeypatch.setenv("ALLMEET_RECIPE_MEMORY", "0")
    store: dict[str, Any] = {}
    with patch(DOC_REF, side_effect=_ref_factory(store)), patch(TEAM, return_value="PC2"), patch(
        EMBED, return_value=[1.0, 0.0]
    ):
        recipes.record_recipe("me@x.com", "g", [{"tool": "x", "args": {}}], "m")
        assert recipes.recall_recipes("me@x.com", "g") == []
    assert store == {}


# ---------------------------------------------------------------------------
# 프롬프트 주입 + create_plan 전달
# ---------------------------------------------------------------------------


def test_planner_prompt_includes_recipes_block():
    from domains.agent.prompts import build_planner_prompt

    p = build_planner_prompt(
        user_message="x",
        catalog=[],
        today="2026-06-24",
        user_name="U",
        user_email="u@x.com",
        recipes_block="- 요청 'PC2 주간보고 요약' → find_team_weekly_report → generate_content",
    )
    assert "비슷한 과거 성공 사례" in p
    assert "find_team_weekly_report → generate_content" in p


def test_create_plan_forwards_recipes_block():
    from domains.agent import planner

    with patch("domains.agent.planner.build_planner_prompt", return_value="P") as bp, patch(
        "domains.agent.planner.generate_json", return_value={}
    ):
        planner.create_plan("x", user_email="u@x.com", recipes_block="REC")
    assert bp.call_args.kwargs.get("recipes_block") == "REC"


# ---------------------------------------------------------------------------
# 쓰기 훅: 검증된 성공에서만 record_recipe
# ---------------------------------------------------------------------------


def _approved_plan():
    from domains.agent import store

    return {
        "plan_id": "p1",
        "status": store.STATUS_APPROVED,
        "goal": "g",
        "steps": [{"n": 1, "tool": "x"}],
        "user_email": "u@x.com",
        "planner_model": "m",
        "results": {},
    }


def test_execute_plan_records_recipe_on_verified_success():
    from domains.agent import orchestrator

    with patch("domains.agent.store.load_plan", return_value=_approved_plan()), patch(
        "domains.agent.store.set_status"
    ), patch("domains.agent.store.save_results"), patch("domains.agent.store.save_trace"), patch(
        "domains.agent.orchestrator.run_loop",
        return_value={"outcome": orchestrator.OUTCOME_DONE, "results": {1: {"ok": True}}, "trace": []},
    ), patch(
        "domains.agent.orchestrator.verify_outcome", return_value={"achieved": True}
    ), patch("domains.agent.memory.record_artifacts"), patch(
        "domains.agent.recipes.record_recipe"
    ) as rec:
        orchestrator.execute_plan("p1")
    rec.assert_called_once()


def test_execute_plan_skips_recipe_when_not_achieved():
    from domains.agent import orchestrator

    with patch("domains.agent.store.load_plan", return_value=_approved_plan()), patch(
        "domains.agent.store.set_status"
    ), patch("domains.agent.store.save_results"), patch("domains.agent.store.save_trace"), patch(
        "domains.agent.orchestrator.run_loop",
        return_value={"outcome": orchestrator.OUTCOME_DONE, "results": {1: {"ok": True}}, "trace": []},
    ), patch(
        "domains.agent.orchestrator.verify_outcome", return_value={"achieved": False, "reason": "부족"}
    ), patch("domains.agent.memory.record_artifacts"), patch(
        "domains.agent.recipes.record_recipe"
    ) as rec:
        orchestrator.execute_plan("p1")
    rec.assert_not_called()
