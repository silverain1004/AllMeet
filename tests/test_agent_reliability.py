"""에이전트 신뢰성 개선 — revision 라우팅·generate_content gate 통합 테스트.

배포 후 수동 재테스트 시나리오:
1. E-BIZ ... 찾고 ... 체크리스트 → 에이전트 승인 카드
2. 승인 전 2주로 바꿔줘 → 같은 plan_id 로 갱신된 승인 카드
3. Confluence에 없는 키워드 → 검색 0건에서 중단(🎉 없음)
4. 있는 키워드 → 본문→생성→검증 통과 시에만 완료
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


def test_main_routes_revision_when_pending_plan():
    from main import hello_http

    payload = {
        "type": "MESSAGE",
        "user": {"email": "u@example.com", "displayName": "U"},
        "space": {"name": "spaces/AAA"},
        "message": {"text": "2주로 바꿔줘"},
    }
    active = {
        "plan_id": "PID1",
        "goal": "3주 로드맵",
        "steps": [{"n": 1, "tool": "search_confluence", "args": {}, "why": "검색", "side_effect": False}],
        "status": "pending_approval",
    }
    revised_card = {"text": "수정됨", "cardsV2": [{"cardId": "ag_plan"}]}

    class _Req:
        method = "POST"
        path = "/"

        def get_json(self, silent=True):
            return payload

    with patch("main.agent_store.find_active_plan", return_value=active), \
         patch("main.is_plan_revision", return_value=True), \
         patch("main.handle_agent_revision", return_value=revised_card) as rev, \
         patch("main.match_user_intent") as mi:
        body, status, _ = hello_http(_Req())
    rev.assert_called_once()
    mi.assert_not_called()
    assert status == 200
    assert "수정됨" in body


def test_handle_agent_revision_keeps_plan_id():
    from domains.agent.actions import handle_agent_revision

    existing = {
        "plan_id": "PID-KEEP",
        "goal": "3주 로드맵",
        "plan_rationale": "r",
        "critique_note": "",
        "steps": [
            {"n": 1, "tool": "search_confluence", "args": {"query": "x"}, "why": "검색", "side_effect": False}
        ],
    }
    revised = {
        "goal": "2주 로드맵",
        "plan_rationale": "기간 변경",
        "critique_note": "ok",
        "steps": existing["steps"],
    }
    chat_event = {
        "type": "MESSAGE",
        "user": {"email": "u@x.com"},
        "space": {"name": "spaces/AAA"},
    }

    pushed_card: dict = {}

    def _sync_bg(fn, *args, **kwargs):
        fn(*args, **kwargs)

    def _capture_push(*, space_name, payload):
        pushed_card.update(payload if isinstance(payload, dict) else {"text": str(payload)})

    with patch("api.chat.loading.start_background", side_effect=_sync_bg), \
         patch("domains.agent.actions.revise_plan", return_value=revised), \
         patch("domains.agent.actions.store.update_plan") as upd, \
         patch("domains.agent.memory.load_user_memory", return_value=[]), \
         patch("domains.agent.memory.format_memory_block", return_value=""), \
         patch("api.chat.messages.post_message_to_space", side_effect=_capture_push):
        loading = handle_agent_revision("2주로 바꿔줘", chat_event=chat_event, existing_plan=existing)
    assert "⏳" in loading.get("text", "")
    upd.assert_called_once()
    assert upd.call_args[0][0] == "PID-KEEP"
    out = pushed_card
    buttons = [
        w["buttonList"]["buttons"]
        for w in out["cardsV2"][0]["card"]["sections"][0]["widgets"]
        if "buttonList" in w
    ][0]
    params = {p["key"]: p["value"] for p in buttons[0]["onClick"]["action"]["parameters"]}
    assert params["plan_id"] == "PID-KEEP"


def test_generate_content_requires_source_when_flagged():
    from domains.agent.registry import _run_generate_content

    out = _run_generate_content(
        instruction="로드맵",
        source=None,
        require_source=True,
    )
    assert out["ok"] is False
    assert out["error_kind"] == "missing_source"


def test_skip_not_allowed_for_ref_unresolved():
    from domains.agent import repair

    assert repair.skip_allowed(
        "ref_unresolved",
        results={1: {"ok": True, "found": False}},
        failed_step={"tool": "get_confluence_page_body"},
    ) is False
