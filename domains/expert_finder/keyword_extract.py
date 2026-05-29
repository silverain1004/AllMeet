"""사용자 발화에서 검색 키워드 추출.

MVP(M1) 는 정규식만 사용. 사용자가 자연스럽게 쓰는 패턴 ("X 전문가", "X 잘 아는 사람"
등) 에서 X 부분을 캡처. 매칭이 안 되면 ``None`` 반환 → 호출 측에서 키워드 안내 카드.

LLM 폴백(Vertex Gemini 1줄 분류) 은 plan §5.1 에 적혀있지만 M1 에선 정규식만으로
충분히 검증 가능하므로 도입 보류. M2~M3 단계에서 필요해지면 ``_llm_fallback`` 함수
하나 추가하면 됨.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 키워드 캡처 패턴 — 우선순위 상관없이 가장 긴 캡처를 채택.
# 한국어/영어 혼용 키워드를 받기 위해 캡처군은 lookahead 로 잘라냄.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(.+?)\s*(?:분야|쪽)?\s*전문가"),
    re.compile(r"(.+?)\s*(?:를|을)?\s*잘\s*(?:아는|하는)\s*사람"),
    re.compile(r"(.+?)\s*(?:를|을)?\s*잘\s*(?:아는|하는)"),
    re.compile(r"(.+?)\s*(?:잘\s*하는|숙련된|능한)\s*사람"),
    re.compile(r"(.+?)\s*(?:고수|베테랑|마스터)"),
    re.compile(r"(.+?)\s*담당자"),
    re.compile(r"(.+?)\s*(?:관련|관련된)\s*(?:사람|분|전문가)"),
    re.compile(r"누가\s*(.+?)\s*(?:잘\s*알아|잘\s*하|알아)"),
    re.compile(r"(.+?)\s*추천(?:해|해줘|좀)?"),
)

# 키워드 앞에 자주 붙는 노이즈 (제거). "사내 벡터DB 전문가" → "벡터DB".
_LEADING_NOISE_RE = re.compile(
    r"^\s*(?:사내|회사|우리\s*회사|우리|좀|이거|이|그|저)\s+"
)
# 키워드 뒤에 자주 붙는 종결/조사 (제거). "벡터DB의" → "벡터DB".
_TRAILING_NOISE_RE = re.compile(
    r"(?:의|에\s*대한|관한|관련)\s*$"
)


def extract_keyword(user_message: str) -> str | None:
    """발화에서 검색 키워드 X 를 추출. 못 찾으면 None.

    여러 패턴이 동시에 매칭되면 캡처군이 가장 짧은(=가장 구체적인) 것을 채택.
    "벡터DB 전문가 추천해줘" 에서 ``전문가`` 패턴은 "벡터DB" 를 잡지만
    ``추천`` 패턴은 "벡터DB 전문가" 를 잡으므로, 짧은 쪽이 옳다.
    """
    text = (user_message or "").strip()
    if not text:
        return None

    candidates: list[str] = []
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = (m.group(1) or "").strip()
        cleaned = _clean(raw)
        if cleaned:
            candidates.append(cleaned)

    if not candidates:
        logger.info("expert_finder keyword 추출 실패: %s", text[:120])
        return None

    # 가장 짧은(가장 구체적인) 캡처 채택.
    keyword = min(candidates, key=len)
    logger.info("expert_finder keyword 추출: '%s' from '%s'", keyword, text[:120])
    return keyword


# 캡처군 양 끝의 노이즈 제거.
def _clean(raw: str) -> str:
    s = raw.strip()
    s = _LEADING_NOISE_RE.sub("", s).strip()
    s = _TRAILING_NOISE_RE.sub("", s).strip()
    # 너무 짧거나(1자) 너무 길면(40자 초과) 매칭 신뢰도 낮음.
    if len(s) < 2 or len(s) > 40:
        return ""
    return s
