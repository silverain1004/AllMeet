"""LLM + 대화 맥락 기반 intent 분류."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# main.UserIntent 값과 동일한 문자열 라벨
VALID_INTENTS = frozenset(
    {
        "agent",
        "daily_chat",
        "expert_finder",
        "schedule_management",
        "weekly_report_draft",
        "weekly_meeting",
        "settings",
        "home_menu",
    }
)

_INTENT_CRITERIA = """사용자 발화의 의도를 아래 라벨 중 정확히 하나로 분류하세요.

[맥락 우선]
- [최근 대화]가 있으면 이전 턴 주제·진행 중인 기능을 우선합니다.
  예: 전문가 검색 직후 "더 찾아줘" → expert_finder, 일정 예약 중 "3시로" → schedule_management.
- 대화 맥락 없이 첫 인사·도움말만 home_menu. 대화 중 짧은 인사만이면 daily_chat.

[라벨별 판별 — YES(해당 기능 호출) / NO(다른 라벨)]

agent — 여러 단계·도구를 엮는 복합 업무 (Confluence/페이지 찾기+요약·정리 포함)
  YES: "최신 주간보고 페이지 찾아서 요약해줘", "PC2팀 주간보고 Confluence 최신 페이지 요약해줘"
  YES: "PC2팀 최근 주간보고 요약해줘", "지난주 회의록 요약해줘"
  YES: "작년 보고서 찾아서 로드맵 만들어줘", "회의 잡고 회의록 페이지도 만들어줘"
  NO: "오늘 날씨 알려줘"(daily_chat), "Kafka 전문가 추천"(expert_finder)

daily_chat — 일상 대화·단순 질문·단일 정보·비유적 표현·봇이 직접 답 가능한 단일 요약
  YES: "점심 메뉴 추천해줘", "Kafka를 전문가처럼 쉽게 설명해줘", "회의 일정표 요약해줘"(본문 없이)
  NO: "Kafka 전문가 찾아줘"(expert_finder), "회의실 예약해줘"(schedule_management)
  NO: "페이지 찾아서 요약"(agent — 사내 시스템 조회 필요)

expert_finder — 사내 전문가·담당자 추천/검색 기능 호출
  YES: "Kafka 전문가 추천해줘", "이 분야 담당자 찾아줘"
  NO: "전문가처럼 설명해줘"(daily_chat), "전문 용어 뜻 알려줘"(daily_chat)

schedule_management — 회의실·캘린더·일정 예약·빈 시간 찾기 기능 호출
  YES: "내일 3시 회의실 예약해줘", "빈 시간 찾아서 잡아줘"
  NO: "회의 일정표 요약해줘"(daily_chat), "예약 기록 문서 찾아줘"(agent)

weekly_report_draft — '주간보고 초안' 기능 직접 호출(작성/받기)만
  YES: "주간보고 초안 받기", "주간보고초안"
  NO: "주간보고 내용 알려줘"(daily_chat), "최신 주간보고 페이지 찾아서 요약"(agent)

weekly_meeting — 주간회의 메뉴·팀/인원 등록·주간업무 설정
  YES: "주간회의 팀 등록", "인원 등록해줘", "주간업무 설정"
  NO: "주간회의 페이지 찾아서 요약"(agent), "주간보고 페이지 요약"(agent)

settings — 앱 설정·내 데이터 연결·OAuth (기능 메뉴 진입)
  YES: "설정", "내 데이터 연결"
  NO: "알람 설정 방법"(daily_chat), "환경 변수 설정 가이드"(daily_chat)

home_menu — 인사·도움말·홈·뭐 할 수 있어 (첫 진입·기능 안내)
  YES: "안녕", "뭐 할 수 있어", "홈 메뉴"
  NO: 대화 중 "안녕"(daily_chat), 업무 요청 문장

[공통 규칙]
- Confluence·페이지·문서를 찾아 본문을 읽고 요약/정리하면 항상 agent (daily_chat 아님).
- 키워드만 보고 기능 메뉴를 열지 마세요.
- 기능 메뉴 강제 오픈은 피하되, 명확한 복합 업무는 agent 로 분류하세요.
- 정말 애매한 일상 질문만 daily_chat.

출력: 위 라벨 중 하나만 소문자로 (예: daily_chat). 다른 글자 없음."""

_REVISION_CRITERIA = """승인 대기 중인 실행 계획이 있습니다.
사용자 마지막 발화가 그 계획을 수정·조정하려는 짧은 후속 지시이면 yes.
완전히 새로운 복합 업무 요청이면 no.

yes 예: "2주로 바꿔줘", "대상을 B팀으로", "요약만 하고 페이지는 빼", "3주가 아니라 2주"
no 예: "작년 보고서 찾아서 로드맵 만들어줘", "E-BIZ 찾고 체크리스트 만들어줘", "회의 잡고 페이지도 만들어줘"
no 예: 검색→생성·조회→실행 등 여러 단계가 새로 필요한 요청 전체

짧은 기간·대상·범위·형식 변경만 yes. 새 목표·새 작업 체인은 no."""


def _explicit_intent_fastpath(text: str) -> str | None:
    """오탐 없는 명시적 트리거만 — LLM 호출 생략."""
    stripped = (text or "").strip()
    lower = stripped.lower()
    if not stripped:
        return "daily_chat"

    draft_keywords = (
        "주간보고초안",
        "주간 보고 초안",
        "주간보고 초안",
        "a함수호출",
        "a 함수호출",
        "a 함수 호출",
    )
    if any(k in lower for k in draft_keywords):
        return "weekly_report_draft"

    if stripped in {"설정", "setting", "settings"}:
        return "settings"

    settings_keywords = (
        "내 데이터 연결",
        "데이터 연결",
        "내 데이터연결",
        "데이터연결",
        "내 정보 연결",
        "oauth 연결",
        "oauth연결",
    )
    if any(k in stripped for k in settings_keywords):
        return "settings"

    return None


def _active_plan_block(active_plan: dict[str, Any] | None) -> str:
    if not active_plan:
        return ""
    goal = str(active_plan.get("goal") or "").strip()
    steps = active_plan.get("steps") or []
    step_lines = [
        f"  {s.get('n')}. {s.get('why') or s.get('tool')}"
        for s in steps[:8]
        if isinstance(s, dict)
    ]
    body = f"[승인 대기 계획]\n목표: {goal}\n"
    if step_lines:
        body += "단계:\n" + "\n".join(step_lines) + "\n"
    return body


def _classify_label(
    user_message: str,
    ctx_block: str,
    *,
    criteria: str,
    valid: frozenset[str],
    log_fail: str,
    extra_block: str = "",
) -> str:
    msg = (user_message or "").strip()
    if not msg:
        return "daily_chat"
    ctx = (ctx_block or "").strip()
    extra = (extra_block or "").strip()
    try:
        from config.settings import ALLMEET_INTENT_MODEL
        from domains.agent._llm import generate_text

        body = (
            "역할: 분류기. 출력은 지정된 라벨 한 단어만.\n\n"
            f"{criteria}\n\n"
        )
        if extra:
            body += f"{extra}\n\n"
        body += f"{ctx}\n\n" if ctx else "(이전 대화 없음)\n\n"
        body += f'사용자 마지막 발화: "{msg}"\n\n분류:'
        # 폴백 분류기는 pro(2.5)로 — fast-path 가 놓친 애매한 발화만 도달한다.
        # 2.5 계열은 thinking 모델이라 토큰이 적으면 thinking 에 소진돼 본문이 빌 수 있으므로,
        # 한 단어 답이라도 넉넉한 budget 을 준다(빈 응답이면 daily_chat 폴백).
        raw = (
            generate_text(
                body, model=ALLMEET_INTENT_MODEL, max_output_tokens=1024, temperature=0
            )
            or ""
        ).strip().lower()
        if not raw:
            return "daily_chat"
        first = raw.split()[0].rstrip(".,!?")
        if first in valid:
            return first
        # 부분 매칭 — 정확 라벨 우선, 동률이면 가장 긴 라벨(frozenset 비결정 순서 보정).
        hits = [label for label in valid if label in raw]
        if hits:
            return max(hits, key=len)
    except Exception as e:
        logger.warning(log_fail, e)
    return "daily_chat"


def _yes_no_revision(
    user_message: str,
    ctx_block: str,
    *,
    active_plan: dict[str, Any],
) -> bool:
    msg = (user_message or "").strip()
    if not msg or len(msg) > 120:
        return False
    plan_block = _active_plan_block(active_plan)
    try:
        from domains.daily_chat.chat import _yes_no_classify

        return _yes_no_classify(
            msg,
            (ctx_block or "") + "\n" + plan_block,
            criteria=_REVISION_CRITERIA,
            log_fail="is_plan_revision 실패: %s",
        )
    except Exception as e:
        logger.warning("is_plan_revision 예외: %s", e)
        return False


def is_plan_revision(
    user_message: str,
    active_plan: dict[str, Any] | None,
    ctx_block: str = "",
) -> bool:
    """승인 대기 plan 이 있을 때 수정 후속인지 LLM 판별."""
    if not active_plan:
        return False
    msg = (user_message or "").strip()
    if not msg:
        return False
    return _yes_no_revision(msg, ctx_block, active_plan=active_plan)


def _deterministic_label(msg: str, ctx_block: str) -> tuple[str, str] | None:
    """LLM 이전 결정적 라우팅 — (label, route_path) 또는 None(애매 → LLM 폴백).

    agent(조회→생성 연쇄)를 도메인 fast-path 보다 먼저 둬, "주간회의 페이지 찾아서 요약"=agent
    가 보장되고 "주간회의 팀 등록"=weekly_meeting 으로 갈린다.
    """
    from domains.routing.patterns import (
        is_exact_home_greeting,
        looks_like_agent_task,
        looks_like_expert_finder,
        looks_like_home_greeting,
        looks_like_schedule_booking,
        looks_like_weekly_meeting_setup,
    )

    fast = _explicit_intent_fastpath(msg)
    if fast:
        return fast, "explicit_fastpath"
    if is_exact_home_greeting(msg):
        return "home_menu", "exact_home_greeting"
    if not (ctx_block or "").strip() and looks_like_home_greeting(msg):
        return "home_menu", "home_greeting"
    if looks_like_agent_task(msg):
        return "agent", "agent_pattern"
    if looks_like_expert_finder(msg):
        return "expert_finder", "expert_fastpath"
    if looks_like_schedule_booking(msg):
        return "schedule_management", "schedule_fastpath"
    if looks_like_weekly_meeting_setup(msg):
        return "weekly_meeting", "weekly_meeting_fastpath"
    return None


def deterministic_intent(user_message: str, ctx_block: str = "") -> str | None:
    """결정적 fast-path 로 확정되는 intent 라벨(없으면 None). 명료화 하이재킹 가드용."""
    msg = (user_message or "").strip()
    if not msg:
        return None
    decided = _deterministic_label(msg, ctx_block)
    return decided[0] if decided else None


def classify_intent(
    user_message: str,
    ctx_block: str = "",
    *,
    active_plan: dict[str, Any] | None = None,
) -> str:
    """사용자 발화 → intent 라벨 문자열. 실패 시 daily_chat."""
    msg = (user_message or "").strip()
    if not msg:
        return "daily_chat"

    def _decided(label: str, route_path: str) -> str:
        # 어느 분기에서 라벨이 정해졌는지 기록 — 오라우팅(특히 agent 누락) 분석용(Phase 0).
        logger.info("route intent=%s path=%s msg=%r", label, route_path, msg[:80])
        return label

    decided = _deterministic_label(msg, ctx_block)
    if decided is not None:
        return _decided(*decided)

    extra = _active_plan_block(active_plan)
    label = _classify_label(
        msg,
        ctx_block,
        criteria=_INTENT_CRITERIA,
        valid=VALID_INTENTS,
        log_fail="classify_intent 실패, daily_chat 폴백: %s",
        extra_block=extra,
    )
    return _decided(label, "llm")
