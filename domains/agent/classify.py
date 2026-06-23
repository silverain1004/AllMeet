"""2-tier 라우팅 — 멀티스텝/복잡 요청인지 LLM 으로 판별.

daily_chat.chat._yes_no_classify 패턴 재사용. 단순 단일 요청은 기존 핸들러로 보내고,
여러 도구를 엮거나 다단계가 필요한 요청만 에이전트로 보낸다. 실패 시 False(기존 라우팅).
"""

from __future__ import annotations

import logging
import os

from domains.routing.patterns import looks_like_agent_task

logger = logging.getLogger(__name__)

_MULTI_STEP_CRITERIA = (
    "사용자 요청이 여러 단계를 거치거나 둘 이상의 작업을 엮어야 달성되는 복합 업무이면 yes.\n"
    "다음 유형은 모두 yes 다:\n"
    "- 조회→실행 연쇄: '회의 잡고 회의록 페이지도 만들어줘', '빈 시간 찾아서 예약하고 참석자에게 알려줘'.\n"
    "- 자료 검색→콘텐츠 생성/요약/추출 연쇄: '작년 보고서 찾아서 로드맵 초안 만들어줘', "
    "'E-BIZ 인수인계 데이터를 찾고 배포 체크리스트 만들어줘', "
    "'표준 가이드 기반으로 온보딩 체크리스트 만들어줘'.\n"
    "- 문서를 찾아 본문을 읽고 요약/정리하는 작업: '최신 주간회의 페이지 찾아서 핵심을 요약 페이지로 정리해줘' "
    "(검색→본문읽기→요약은 단계가 여럿이므로 yes).\n"
    "단일 동작(일상 대화, 단순 질문, 기능 하나만 호출, 단순 인사)이면 no."
)

# 승인 대기 중 짧은 수정 지시 — LLM is_plan_revision 으로 판별 (키워드 regex 제거).


def _keyword_fastpath_enabled() -> bool:
    return os.environ.get("AGENT_KEYWORD_FASTPATH", "").strip().lower() in ("1", "true", "yes")


def _keyword_fastpath(msg: str) -> bool:
    """'찾아서 ~ 만들/요약/정리' 류 조회→생성 연쇄를 키워드로 빠르게 인정."""
    return looks_like_agent_task(msg)


def looks_like_revision(
    user_message: str,
    active_plan: dict | None = None,
    ctx_block: str = "",
) -> bool:
    """승인 대기 plan 이 있을 때 수정 후속인지 판별 (LLM)."""
    from domains.routing.intent import is_plan_revision

    return is_plan_revision(user_message, active_plan, ctx_block)


def is_multi_step_request(user_message: str, ctx_block: str = "") -> bool:
    """복합/멀티스텝 요청이면 True. 분류 실패 시 False(기존 키워드 라우팅 유지)."""
    msg = (user_message or "").strip()
    if not msg:
        return False
    if _keyword_fastpath_enabled() and _keyword_fastpath(msg):
        return True
    try:
        from domains.daily_chat.chat import _yes_no_classify

        return _yes_no_classify(
            msg,
            ctx_block,
            criteria=_MULTI_STEP_CRITERIA,
            log_fail="is_multi_step_request 실패, 기존 라우팅으로 처리: %s",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("is_multi_step_request 예외: %s", e)
        return False
