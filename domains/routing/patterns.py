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

# --- 도메인 결정적 fast-path (고정밀·보수적) — 명백한 트리거만 LLM 없이 통과 ---
# expert_finder: 사내 전문가/담당자 검색. "전문가처럼 설명" 류는 부정가드로 제외.
_EXPERT_SUBJECT_RE = re.compile(r"(전문가|담당자|잘\s*아는\s*사람)")
_EXPERT_INTENT_RE = re.compile(r"(추천|찾아|찾는|누구|누가|소개)")
_EXPERT_NEGATIVE_RE = re.compile(r"(처럼|같이|쉽게|설명|뜻|개념|방법|정의)")
# schedule_management: 회의실 예약/빈 시간 찾기. "예약 기록 문서"·"일정표 요약"은 미매치.
_ROOM_RE = re.compile(r"(회의실|미팅룸|세미나실|컨퍼런스룸)")
_BOOK_VERB_RE = re.compile(r"(예약|잡아|잡아줘|잡아\s*주|예약해)")
_FREE_SLOT_RE = re.compile(r"빈\s*시간")
# weekly_meeting: 팀/인원 등록·주간업무 설정. 주간보고(초안/agent)는 부정가드.
_WM_SETUP_RE = re.compile(r"(팀\s*등록|인원\s*등록|멤버\s*등록|주간\s*업무\s*설정)")
_WEEKLY_MEETING_RE = re.compile(r"주간\s*회의")
_WM_ACTION_RE = re.compile(r"(등록|설정|세팅)")
# weekly_page_create: 주간회의/보고 페이지 수동 생성. 요약·검색·초안은 부정가드.
_WEEKLY_PAGE_SUBJECT_RE = re.compile(
    r"주간\s*(?:회의|보고)?\s*페이지|주간\s*(?:회의|보고)\s*페이지"
)
_WEEKLY_PAGE_CREATE_RE = re.compile(r"(만들|생성|작성)")
_WEEKLY_PAGE_CREATE_NEG_RE = re.compile(r"(요약|찾아|검색|초안)")

# daily_chat 회수 CTA 판별 — action-like 사내 업무 신호(decoration 전용, 약간 liberal).
_RECOVERY_NOUN_RE = re.compile(
    r"(회의실|회의|일정|미팅|예약|전문가|담당자|팀\s*등록|인원\s*등록|주간\s*회의|주간\s*보고|캘린더|스케줄)"
)
_RECOVERY_VERB_RE = re.compile(
    r"(예약|잡아|등록|추천|찾아|찾는|요약|정리|알려|만들|초안|보여)"
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


def looks_like_expert_finder(msg: str) -> bool:
    """'전문가/담당자 추천·찾아줘' — 단, '전문가처럼 설명' 류는 제외(daily_chat)."""
    text = (msg or "").strip()
    if not text:
        return False
    if _EXPERT_NEGATIVE_RE.search(text):
        return False
    return bool(_EXPERT_SUBJECT_RE.search(text) and _EXPERT_INTENT_RE.search(text))


def looks_like_schedule_booking(msg: str) -> bool:
    """'회의실 예약/목록' 또는 '빈 시간 찾아 잡기'. 문서·일정표 요약 류는 미매치.

    회의실 목록 질의('회의실 뭐 있어')도 schedule_management 로 보낸다 — 핸들러가 목록/예약을
    구분(_is_room_list_query)하므로, LLM 흔들림 없이 결정적으로 회의실 도메인에 도달시킨다.
    """
    text = (msg or "").strip()
    if not text:
        return False
    if _ROOM_RE.search(text):  # '회의실'이 들어가면(예약/목록 모두) 회의실 도메인
        return True
    if _FREE_SLOT_RE.search(text) and re.search(r"(찾아|잡|예약)", text):
        return True
    return False


def looks_like_weekly_meeting_setup(msg: str) -> bool:
    """'팀/인원 등록·주간업무 설정' 또는 '주간회의 + 등록/설정'. 주간보고는 제외."""
    text = (msg or "").strip()
    if not text:
        return False
    if "주간보고" in text.replace(" ", ""):
        return False
    if _WM_SETUP_RE.search(text):
        return True
    if _WEEKLY_MEETING_RE.search(text) and _WM_ACTION_RE.search(text):
        return True
    return False


def looks_like_weekly_page_create(msg: str) -> bool:
    """'OO팀 주간(회의|보고)페이지 만들어/생성해줘' — 스케줄러 없이 수동 생성.

    요약·찾아·검색·초안이 포함되면 False (agent / weekly_report_draft 유지).
    """
    text = (msg or "").strip()
    if not text:
        return False
    if _WEEKLY_PAGE_CREATE_NEG_RE.search(text):
        return False
    if not _WEEKLY_PAGE_SUBJECT_RE.search(text):
        return False
    return bool(_WEEKLY_PAGE_CREATE_RE.search(text))


def looks_actionable_for_recovery(msg: str) -> bool:
    """daily_chat 으로 떨어졌으나 사내 업무성 발화면 True — 회수 CTA 부착용."""
    text = (msg or "").strip()
    if not text:
        return False
    return bool(_RECOVERY_NOUN_RE.search(text) and _RECOVERY_VERB_RE.search(text))


# 개인 일정 조회/요약 — 주어가 없으면 보통 '내 캘린더'. agent(list_calendar_events)로 보낸다.
# 회의실 예약(booking)과는 구분(예약/잡기/회의실은 schedule_management).
_SCHEDULE_NOUN_RE = re.compile(r"(일정|스케줄|캘린더)")
_SCHEDULE_ASK_RE = re.compile(r"(요약|정리|알려|보여|확인|뭐|뭐야|있어|있나|어때|있는지)")
_SCHEDULE_BOOK_EXCL_RE = re.compile(r"(회의실|예약|잡아|잡아줘|잡기)")
# 휴가/부재 조회 — agent(lookup_team_vacation)로.
_VACATION_RE = re.compile(r"(휴가|연차|반차|쉬는|쉬어|쉬나|쉬어요|부재|자리\s*비)")
_VACATION_ASK_RE = re.compile(r"(누구|누가|언제|있어|있나|뭐|알려|정리|확인)")


def looks_like_my_schedule(msg: str) -> bool:
    """'(이번주/오늘) 일정 요약/알려줘' 류 — 개인 캘린더 조회. 회의실 예약류는 제외."""
    text = (msg or "").strip()
    if not text or _SCHEDULE_BOOK_EXCL_RE.search(text):
        return False
    return bool(_SCHEDULE_NOUN_RE.search(text) and _SCHEDULE_ASK_RE.search(text))


def looks_like_vacation_query(msg: str) -> bool:
    """'누가 쉬어/휴가자 누구/연차 누구' 류 — 팀 휴가 조회."""
    text = (msg or "").strip()
    if not text:
        return False
    return bool(_VACATION_RE.search(text) and _VACATION_ASK_RE.search(text))
