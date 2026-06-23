"""라우팅용 deterministic 패턴 — LLM 보조 fast-path."""

from __future__ import annotations

import re

# 조회·검색 동사 + 생성·요약 동사가 함께 있으면 멀티스텝(agent) 업무.
_FIND_RE = re.compile(r"(찾아|찾고|검색|검색해|검색해서|조회|조회해|찾아서|불러|가져와)")
_PRODUCE_RE = re.compile(r"(만들|작성|생성|요약|정리|문서화|초안|로드맵|체크리스트|페이지로|보고서)")
_WEEKLY_REPORT_RE = re.compile(r"주간\s*보고")
# 요약·정리 요청 동사 (방법/가이드 질문과 구분)
_SUMMARIZE_ASK_RE = re.compile(
    r"(요약해|요약해줘|요약해\s*주|요약만|요약\s*$|정리해|정리해줘|핵심|개요|브리핑)"
)

_HOME_EXACT = frozenset(
    {
        "안녕",
        "안녕하세요",
        "하이",
        "hi",
        "hello",
        "홈",
        "홈 메뉴",
        "홈메뉴",
        "메뉴",
        "처음으로",
        "도움말",
        "help",
    }
)

_HOME_KEYWORDS = (
    "뭐할수있어",
    "뭐 할 수 있어",
    "뭘 할 수 있어",
    "무엇을 할 수 있",
    "너 뭐할수있어",
    "너 뭐 할 수 있어",
    "할 수 있는 것",
    "기능 알려",
)


def looks_like_agent_task(msg: str) -> bool:
    """'찾아서 ~ 요약/만들/정리' 또는 Confluence/주간보고 요약 류."""
    text = (msg or "").strip()
    if not text:
        return False
    if _FIND_RE.search(text) and _PRODUCE_RE.search(text):
        return True
    lower = text.lower()
    if "confluence" in lower and _PRODUCE_RE.search(text):
        return True
    if (
        "페이지" in text
        and _PRODUCE_RE.search(text)
        and ("최신" in text or "최근" in text or "confluence" in lower)
    ):
        return True
    # 주간보고 Confluence 검색+요약 — '찾아' 없이도 (예: PC2팀 최근 주간보고 요약해줘)
    if _WEEKLY_REPORT_RE.search(text) and _SUMMARIZE_ASK_RE.search(text):
        compact = text.replace(" ", "")
        if "초안" in compact:
            return False
        if re.search(r"(어떻게|방법|팁|가이드)", text):
            return False
        return True
    # 회의록 본문 요약 (회의 일정표 요약과 구분)
    if "회의록" in text and _SUMMARIZE_ASK_RE.search(text):
        return True
    return False


def is_exact_home_greeting(msg: str) -> bool:
    """짧은 인사·홈 진입 exact match — 맥락과 무관하게 home_menu."""
    stripped = (msg or "").strip()
    if not stripped:
        return False
    if stripped in _HOME_EXACT:
        return True
    return stripped.lower() in _HOME_EXACT


def looks_like_home_greeting(msg: str) -> bool:
    """인사·도움말·기능 문의 (맥락 없을 때 fast-path 용)."""
    if is_exact_home_greeting(msg):
        return True
    stripped = (msg or "").strip()
    return any(k in stripped for k in _HOME_KEYWORDS)
