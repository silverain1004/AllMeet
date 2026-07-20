"""플래너 / repair LLM 프롬프트.

도구 카탈로그(name/description/parameters)를 JSON 으로 직렬화해 주입한다 =
"AI 가 참조하는 사전 정의된 API 리스트".
"""

from __future__ import annotations

import json
from typing import Any


def build_planner_prompt(
    *,
    user_message: str,
    catalog: list[dict[str, Any]],
    today: str,
    user_name: str,
    user_email: str,
    ctx_block: str = "",
    memory_block: str = "",
    recipes_block: str = "",
) -> str:
    """Phase A — 실행 없는 구조화 계획(JSON)을 만들게 하는 프롬프트."""
    tools_json = json.dumps(catalog, ensure_ascii=False, indent=2)
    ctx = f"\n[최근 대화]\n{ctx_block}\n" if (ctx_block or "").strip() else ""
    mem = (
        f"\n[내가 최근 만든 산출물(연속 작업 시 참고)]\n{memory_block}\n"
        if (memory_block or "").strip()
        else ""
    )
    rec = (
        "\n[비슷한 과거 성공 사례 — 팀이 같은 류 요청을 어떤 도구 순서로 해결했는지. "
        f"패턴만 참고하고 그대로 베끼지 말 것; 현재 요청에 맞게 도구/인자를 설계하라]\n{recipes_block}\n"
        if (recipes_block or "").strip()
        else ""
    )
    return f"""당신은 기업용 업무 에이전트 'All-Meet'의 플래너입니다.
사용자의 목표를 달성하기 위해, 아래 [사용 가능한 도구]만 사용하는 단계별 실행 계획을 JSON 으로 설계하세요.
지금은 '계획만' 세우고 실행은 하지 않습니다.

[설계 원칙]
- 한 단계 = 도구 하나. 단계는 실행 순서대로 나열한다.
- 쓰기/생성/발송(side_effect=true) 도구 전에는 반드시 읽기 도구로 전제 조건을 먼저 확인한다.
  (예: create_meeting 전에 find_free_slots)
- 아직 알 수 없는 값(예: 빈 시간 조회 결과)은 이전 단계 결과를 참조한다.
  참조는 args 안에서 {{"$ref": "<단계번호>.<점경로>"}} 형식으로 쓴다.
  예: {{"$ref": "1.busy"}} 는 1번 단계 결과의 busy 필드.
- 검색 도구(search_confluence/search_emails)는 제목·링크·메타만 준다. 본문 내용이 필요하면
  get_confluence_page_body / get_email_body 로 본문을 먼저 읽어라(예: page_id={{"$ref": "1.pages.0.id"}}).
- 주간보고·주간회의 페이지를 '최신/최근/지난주/이번주' 또는 특정 팀(예: PC2팀) 기준으로 조회·요약할 때는
  키워드 검색(search_confluence) 대신 find_team_weekly_report 를 먼저 써라(날짜·팀 기준으로 정확한 페이지를 찾는다).
  반환된 content 를 generate_content 의 source 에 $ref 로 연결하면 본문 읽기 단계 없이 바로 요약할 수 있다.
- 새 문서·체크리스트·로드맵·요약을 만들어야 하면 generate_content 로 생성한다.
  참조자료는 source 에 이전 단계 결과를 $ref 로 연결한다(예: source={{"$ref": "2.body"}}).
  검색→본문읽기→생성 체인이면 generate_content 에 require_source: true 를 반드시 넣어라.
  생성 결과를 문서로 남기려면 create_confluence_page 의 html_content 에
  {{"$ref": "<생성단계>.content_html"}} 로 연결한다.
- 캘린더 도구(list_calendar_events/find_free_slots/create_meeting 등)로 '내/본인' 일정·캘린더
  (내 일정, 이번주 일정 등)를 다룰 때는 calendar_id 에 'primary' 를, user_email 에 요청자 이메일을 넣어라
  (개인 캘린더는 요청자 OAuth 로 접근). 팀·회의실 등 공유 캘린더만 그 캘린더 ID 를 쓴다.
- 정보가 부족해 계획을 못 세우면 steps 를 빈 배열로 두고 ask_user 에 사용자에게 물을 한 문장을 적는다.
- 사용자가 명시하지 않은 사람에게 메일/초대를 보내지 않는다.

[사용 가능한 도구] — 각 도구의 examples 는 "언제·어떤 args 로" 쓰는지 대표 사용예다(있으면 참고).
{tools_json}

[맥락]
오늘 날짜: {today}
사용자: {user_name} <{user_email}>{ctx}{mem}{rec}

[출력 형식] — JSON 오브젝트 하나만. 설명/코드펜스 없이.
{{
  "goal": "사용자 목표 요약",
  "plan_rationale": "이 계획을 이렇게 설계한 이유와 어떤 자료를 참조하는지 1-2문장",
  "steps": [
    {{"n": 1, "tool": "도구이름", "args": {{...}}, "why": "이 단계가 필요한 이유", "side_effect": false}}
  ],
  "needs_confirmation": true,
  "ask_user": ""
}}

사용자 요청: "{user_message}"
"""


def build_critique_prompt(
    *,
    goal: str,
    steps: list[dict[str, Any]],
    user_message: str,
    catalog: list[dict[str, Any]],
) -> str:
    """Phase 1B — 실행 전 계획 자가검수. 누락/위험/순서 오류를 점검한다."""
    return f"""당신은 실행 전 계획을 검수하는 시니어 리뷰어입니다.
아래 계획이 사용자의 목표를 안전하고 빠짐없이 달성하는지 점검하세요.

[사용자 요청] {user_message}
[목표] {goal}
[계획 steps] {json.dumps(steps, ensure_ascii=False)}
[사용 가능한 도구] {json.dumps(catalog, ensure_ascii=False)}

[점검 항목]
- 목표 대비 빠진 단계가 있는가(예: 쓰기 전 전제 조회 누락).
- 위험한 단계(되돌릴 수 없는 side_effect)가 사용자 의도와 어긋나지 않는가.
- 단계 순서가 올바른가(참조 $ref 가 이전 단계를 가리키는가).
- 알 수 없는/불필요한 도구를 쓰지 않는가.
- 검색어가 모호하거나(예: '작년 보고서') 검색 실패 가능성이 크면 note 에 경고하거나 ask_user 로 되묻는 것을 검토한다.

[규칙]
- 문제가 없으면 ok=true, issues=[] 로 답한다.
- 고칠 수 있으면 revised_steps 에 '전체 교체할' steps 배열을 담는다(없으면 생략).
- 도구는 위 목록에 있는 것만 쓴다. 새 side_effect 를 임의로 추가하지 마라.

[출력 형식] — JSON 오브젝트 하나만.
{{"ok": true, "issues": ["..."], "note": "한 줄 요약", "revised_steps": []}}
"""


def build_verify_prompt(*, goal: str, results_digest: str) -> str:
    """Phase 1B — 실행 후 결과가 목표를 충족했는지 판정한다."""
    return f"""당신은 실행 결과가 목표를 달성했는지 판정하는 검증자입니다.

[목표] {goal}
[실행 결과 요약] {results_digest}

[규칙]
- 목표가 충분히 달성됐으면 achieved=true.
- 부족하거나 의심되면 achieved=false 와 사용자에게 알릴 한 줄 사유(reason)를 적는다.

[출력 형식] — JSON 오브젝트 하나만.
{{"achieved": true, "reason": ""}}
"""


def build_next_step_prompt(
    *,
    goal: str,
    approved_steps: list[dict[str, Any]],
    results: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> str:
    """Phase 3(ReAct) — 지금까지의 결과를 보고 '다음 한 단계'가 필요한지 판단한다."""
    return f"""당신은 실행 중 결과를 관찰하고 다음 행동을 결정하는 에이전트입니다.
지금까지의 결과를 보고, 목표 달성을 위해 '추가 단계'가 필요한지 판단하세요.

[목표] {goal}
[승인된 계획] {json.dumps(approved_steps, ensure_ascii=False)}
[지금까지 결과] {json.dumps(results, ensure_ascii=False)[:2000]}
[사용 가능한 도구] {json.dumps(catalog, ensure_ascii=False)}

[규칙]
- 계획대로 충분하면 action="continue" (추가 단계 없음).
- 관찰 결과상 '조회/생성' 단계를 하나 더 끼우는 게 목표 달성에 필요하면 action="insert_step" 과 step 을 제시.
  step 은 {{"tool": "...", "args": {{...}}, "why": "..."}} 형식. 이전 결과 참조는 $ref 사용.
- 목표가 이미 달성됐으면 action="done".
- 추가 단계는 꼭 필요할 때만. 불필요하면 continue.

[출력 형식] — JSON 오브젝트 하나만.
{{"action": "continue", "step": null}}
"""


def build_repair_prompt(
    *,
    goal: str,
    approved_steps: list[dict[str, Any]],
    results: dict[str, Any],
    failed_step: dict[str, Any],
    error_kind: str,
    detail: str,
    catalog: list[dict[str, Any]],
) -> str:
    """Phase C — 실패한 단계를 복구하는 프롬프트."""
    return f"""당신은 실행 중 실패한 단계를 복구하는 에이전트입니다.

[원래 목표] {goal}
[승인된 전체 계획] {json.dumps(approved_steps, ensure_ascii=False)}
[지금까지 성공한 단계와 결과] {json.dumps(results, ensure_ascii=False)[:2000]}
[실패한 단계] {json.dumps(failed_step, ensure_ascii=False)}
[에러] error_kind={error_kind}, detail={detail}
[사용 가능한 도구] {json.dumps(catalog, ensure_ascii=False)}

[규칙]
- 이 실패를 어떻게 복구할지 딱 하나의 방법을 JSON 으로 제시한다.
- 가능한 action: "retry"(동일 재시도), "fix_args"(인자 교정), "switch_tool"(대체 도구),
  "skip"(이 단계 생략), "abort"(복구 불가).
- 인자만 바꿔 해결되면 fix_args 를 우선한다.
- error_kind 가 ref_unresolved, missing_source, missing_args 이거나 upstream 검색 found=false 이면 skip 을 제안하지 마라(abort 또는 fix_args).
- 승인된 계획에 없던 '새로운 쓰기/발송(side_effect)' 행동은 제안하지 마라.
- auth_required / auth_error 는 사용자만 해결 가능하므로 abort.

[출력 형식] — JSON 오브젝트 하나만.
{{"action": "fix_args", "tool": "도구이름(선택)", "new_args": {{...}}, "reason": "왜"}}
"""


def build_revise_prompt(
    *,
    user_message: str,
    goal: str,
    steps: list[dict[str, Any]],
    plan_rationale: str,
    catalog: list[dict[str, Any]],
    today: str,
    user_name: str,
    user_email: str,
    ctx_block: str = "",
    memory_block: str = "",
) -> str:
    """승인 대기 중 사용자 수정 요청 → 기존 계획 갱신."""
    ctx = f"\n[최근 대화]\n{ctx_block}\n" if (ctx_block or "").strip() else ""
    mem = (
        f"\n[내가 최근 만든 산출물]\n{memory_block}\n"
        if (memory_block or "").strip()
        else ""
    )
    return f"""당신은 기존 실행 계획을 사용자 수정 요청에 맞게 고치는 플래너입니다.
새 계획을 처음부터 만들지 말고, 아래 [기존 계획]을 최소 변경으로 갱신하세요.

[기존 목표] {goal}
[기존 설계 노트] {plan_rationale}
[기존 steps] {json.dumps(steps, ensure_ascii=False)}
[사용자 수정 요청] "{user_message}"

[사용 가능한 도구]
{json.dumps(catalog, ensure_ascii=False, indent=2)}

[맥락]
오늘: {today}
사용자: {user_name} <{user_email}>{ctx}{mem}

[규칙]
- 수정 요청(기간·대상·범위·형식 변경 등)만 반영한다. 불필요한 단계 추가/삭제는 하지 않는다.
- $ref 규칙·require_source 규칙은 build_planner_prompt 와 동일하다.
- 모호하면 steps 를 비우고 ask_user 에 되물을 문장을 적는다.

[출력 형식] — JSON 오브젝트 하나만.
{{
  "goal": "갱신된 목표",
  "plan_rationale": "무엇을 바꿨는지 1-2문장",
  "steps": [...],
  "needs_confirmation": true,
  "ask_user": ""
}}
"""
