"""멀티스텝 에이전트 전역 설정 — 환경변수로 덮어쓸 수 있게 둔다."""

from __future__ import annotations

import os

# 계획 1건당 실행할 수 있는 최대 단계 수 (무한루프 방지).
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS") or "8")

# 한 단계에서 자가복구 재시도 최대 횟수.
MAX_RETRIES_PER_STEP = int(os.environ.get("AGENT_MAX_RETRIES_PER_STEP") or "2")

# 계획 전체에서 허용하는 자가복구 총량 (스텝을 가로질러 합산).
MAX_TOTAL_REPAIRS = int(os.environ.get("AGENT_MAX_TOTAL_REPAIRS") or "5")

# 일시 오류 재시도 시 기본 backoff (초). 지수 증가.
RETRY_BACKOFF_BASE_SEC = float(os.environ.get("AGENT_RETRY_BACKOFF_BASE_SEC") or "1.0")


def react_enabled() -> bool:
    """ReAct 동적 재계획 on/off. 기본 off — 켜면 실행 중 조회/생성 단계를 동적으로 보강한다."""
    return (os.environ.get("AGENT_REACT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


# ReAct 가 한 계획에서 삽입할 수 있는 단계 최대 수 (MAX_STEPS 와 함께 존중).
MAX_REACT_INSERTS = int(os.environ.get("AGENT_MAX_REACT_INSERTS") or "3")


def is_dry_run() -> bool:
    """에이전트 전역 dry-run. side_effect 도구가 미리보기만 반환하도록 강제.

    schedule_management 의 ``SCHEDULE_DRY_RUN`` 도 함께 인정해, 캘린더 dry-run 설정과
    일관되게 동작하도록 한다.
    """
    for key in ("AGENT_DRY_RUN", "SCHEDULE_DRY_RUN"):
        raw = (os.environ.get(key) or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
    return False
