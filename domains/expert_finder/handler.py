"""전문가 찾기 진입점 + 백그라운드 검색.

흐름 (M3 범위):
1. ``handle_expert_finder`` 가 챗 이벤트를 받아 키워드 추출.
2. 키워드 없으면 ``build_keyword_missing_card`` 즉시 반환.
3. 키워드 있으면 ``build_in_progress_card`` 반환 + 백그라운드 thread 시작.
4. 백그라운드:
   - ``build_public_sources()`` — 전사 공용 자원 풀
   - ``search_public()`` — Drive · Confluence · Calendar 키워드 검색
   - ``list_consenting_users()`` — OAuth 동의자 명단
   - ``search_private()`` — Gmail · 개인 Calendar 점진 확장 (6m → 1y → 3y → 전체)
   - ``score_candidates()`` + ``annotate_with_consent()``
   - ``recommend()`` — Vertex Gemini 추천 멘트 (실패해도 raw 만으로 카드)
   - 종료 케이스 4종 분기 (PLAN §6) → 결과 카드 push
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from api.chat.messages import post_message_to_space
from api.chat.progress import LiveProgress
from domains.expert_finder.candidates import annotate_with_consent, list_consenting_users
from domains.expert_finder.cards import (
    build_all_failed_card,
    build_in_progress_card,
    build_keyword_missing_card,
    build_no_data_card,
    build_no_match_card,
    build_result_card,
)
from domains.expert_finder.keyword_extract import extract_keyword
from domains.expert_finder.public_sources import build_public_sources
from domains.expert_finder.recommend import recommend
from domains.expert_finder.scoring import score_candidates
from domains.expert_finder.search import search_private, search_public

logger = logging.getLogger(__name__)

# 점진 확장 윈도우 — PLAN.md §8.
# (label, days) — days=None 이면 시간 제약 없음 (전체).
_WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("최근 6개월", 30 * 6),
    ("최근 1년", 365),
    ("최근 3년", 365 * 3),
    ("전체", None),
)
# 점진 확장 중단 임계값 — 매칭 동의자 수 ≥ 이 값이면 멈춤.
_MATCHING_THRESHOLD = 3

# 진행상황 라이브 메시지 단계.
_PROGRESS_STAGES: list[tuple[str, str]] = [
    ("public", "공용 자원 (Drive·Confluence·Calendar)"),
    ("consent", "동의자 명단"),
    ("private", "개인 데이터"),
    ("score", "후보 스코어링"),
    ("recommend", "AI 추천"),
]


# 메인 진입 — 챗 이벤트 → 키워드 분기 → 즉시 응답 + 백그라운드 thread.
def handle_expert_finder(
    user_message: str,
    *,
    chat_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``UserIntent.EXPERT_FINDER`` 진입점. main.py 의 dispatch 에서 호출."""
    keyword = extract_keyword(user_message)
    if not keyword:
        return build_keyword_missing_card()

    # 검색 요청자 본인 — 결과에서 제외하기 위해 추출 (본인이 본인을 추천받지 않도록).
    requester_email = str(((chat_event or {}).get("user") or {}).get("email") or "").strip()

    space_name = ((chat_event or {}).get("space") or {}).get("name") or ""
    if not space_name:
        # 챗 외 호출 (단순 POST 등) — 동기 처리.
        return _run_search_sync(keyword, requester_email)

    threading.Thread(
        target=_run_search_background,
        args=(space_name, keyword, requester_email),
        daemon=False,
    ).start()
    return build_in_progress_card(keyword)


# 동기 실행 (챗 외 호출용).
def _run_search_sync(keyword: str, requester_email: str = "") -> dict[str, Any]:
    try:
        return _build_result_card(keyword, requester_email)
    except Exception:
        logger.exception("expert_finder sync 실패: keyword=%s", keyword)
        return build_all_failed_card()


# 백그라운드 thread.
def _run_search_background(space_name: str, keyword: str, requester_email: str = "") -> None:
    logger.info("expert_finder 검색 시작 space=%s keyword=%s", space_name, keyword)
    reporter = LiveProgress(
        space_name=space_name,
        title=f"'{keyword}' 전문가 찾는 중...",
        stages=_PROGRESS_STAGES,
    )
    reporter.begin()  # 실패해도 live=False 로 폴백 — 결과만 새 메시지로 push.
    try:
        card = _build_result_card(keyword, requester_email, progress=reporter)
        if reporter.live:
            reporter.finish(card)
            ok = True
        else:
            ok = post_message_to_space(space_name=space_name, payload=card)
        logger.info(
            "expert_finder 검색 완료·push %s keyword=%s",
            "성공" if ok else "실패",
            keyword,
        )
    except Exception:
        logger.exception("expert_finder background 실패: keyword=%s", keyword)
        err = build_all_failed_card()
        if reporter.live:
            reporter.finish(err)
        else:
            post_message_to_space(space_name=space_name, payload=err)


# 공통 — 공용 + 개인(점진 확장) + 스코어링 + Vertex 추천 멘트 + 카드 선택.
def _build_result_card(
    keyword: str,
    requester_email: str = "",
    *,
    progress: LiveProgress | None = None,
) -> dict[str, Any]:
    sources = build_public_sources()
    public_hits, public_errors = search_public(keyword=keyword, sources=sources)
    if progress:
        progress.done("public", len(public_hits))
    consenting = list_consenting_users()
    if progress:
        progress.done("consent", len(consenting))

    # 개인 데이터 — 점진 확장. 매칭 동의자 수 < 3 이면 윈도우 넓힘.
    private_hits, private_errors, used_window = _search_private_progressive(
        keyword=keyword, consenting_emails=consenting, progress=progress
    )
    if progress:
        progress.done("private", len(private_hits))

    # 공용 + 개인 모두 모든 소스 실패 → all_failed.
    # (공용 3개 타입 모두 실패 AND 개인 2개 타입 모두 실패)
    if (
        len(public_errors) >= 3
        and (not consenting or len(private_errors) >= 2)
    ):
        logger.info(
            "expert_finder 모든 API 실패: public=%s private=%s",
            public_errors,
            private_errors,
        )
        return build_all_failed_card()

    all_hits = list(public_hits) + list(private_hits)
    scored = score_candidates(all_hits, keyword=keyword)
    scored = _exclude_requester(scored, requester_email)
    annotate_with_consent(scored, consenting)
    if progress:
        progress.done("score", len(scored))

    if not scored:
        if not consenting and not all_hits:
            return build_no_data_card()
        return build_no_match_card(keyword)

    # Vertex 추천 멘트 (실패해도 raw 만으로 카드).
    if progress:
        progress.active("recommend")
    draft = recommend(keyword, scored)

    logger.info(
        "expert_finder 결과 keyword=%s window=%s public_hits=%d private_hits=%d scored=%d draft=%s",
        keyword,
        used_window,
        len(public_hits),
        len(private_hits),
        len(scored),
        "ok" if draft else "none",
    )

    return build_result_card(
        query=keyword,
        experts=scored,
        window_label=used_window,
        member_pool=sources.get("member_pool") or [],
        draft=draft,
    )


# 검색 요청자 본인 제외 — 본인이 본인을 전문가로 추천받는 건 의미 없음.
def _exclude_requester(
    scored: list[dict[str, Any]], requester_email: str
) -> list[dict[str, Any]]:
    me = (requester_email or "").strip().lower()
    if not me:
        return scored
    return [s for s in scored if (s.get("email") or "").strip().lower() != me]


# 점진 확장 — _WINDOWS 순회. 매칭 동의자 수 ≥ 임계값이면 그 윈도우에서 멈춤.
def _search_private_progressive(
    *,
    keyword: str,
    consenting_emails: list[str],
    progress: LiveProgress | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    if not consenting_emails:
        return [], {}, _WINDOWS[0][0]

    last_hits: list[dict[str, Any]] = []
    last_errors: dict[str, str] = {}
    used_window = _WINDOWS[-1][0]

    for label, days in _WINDOWS:
        if progress:
            progress.active("private", detail=label)
        time_min, time_max = _compute_window(days)
        hits, errors = search_private(
            keyword=keyword,
            consenting_emails=consenting_emails,
            time_min=time_min,
            time_max=time_max,
        )
        last_hits = hits
        last_errors = errors
        used_window = label

        matching_users = {(h.get("email") or "").lower() for h in hits if h.get("email")}
        logger.info(
            "expert_finder 점진확장 window=%s matching_users=%d",
            label,
            len(matching_users),
        )
        if len(matching_users) >= _MATCHING_THRESHOLD:
            break

    return last_hits, last_errors, used_window


def _compute_window(days: int | None) -> tuple[str | None, str | None]:
    """(time_min, time_max) ISO 문자열. days=None 이면 (None, None)."""
    if days is None:
        return None, None
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return time_min, time_max
