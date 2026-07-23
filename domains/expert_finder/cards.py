"""전문가 찾기 결과 카드 빌더 — Google Chat cardsV2 스키마.

PLAN.md §6 종료 케이스 4종 + 본격 결과 카드 (Top 3, 1위 강조, 근거 자료).
"""

from __future__ import annotations

import html
from typing import Any

from api.chat.loading import loading_text

_FOOTER_TEXT = (
    "💡 검색 범위: 사내 공용 Drive · Confluence · 공용 Calendar + "
    "'내 데이터 연결' 동의자의 Gmail 메타 · 개인 Calendar. "
    "메일 본문은 보지 않으며, 요청자 본인은 추천 대상에서 제외됩니다."
)


def build_in_progress_card(query: str) -> dict[str, Any]:
    """즉시 응답 카드. ``query`` 는 추출된 키워드(있으면).

    chat ``text`` 필드는 마크다운만 지원 (``*굵게*``, ``_기울임_``, `` `코드` ``).
    """
    q = (query or "").strip()
    if q:
        out = loading_text(f"{q} 분야 전문가를 찾는 중이에요. 잠시 후 결과를 보내드릴게요.", prefix="🔍")
        out["text"] = f"🔍 *{q}* 분야 전문가를 찾는 중이에요. 잠시 후 결과를 보내드릴게요."
    else:
        out = loading_text("전문가를 찾는 중이에요. 잠시 후 결과를 보내드릴게요.", prefix="🔍")
    return out


# 키워드 추출 실패 — 사용자가 다시 입력하도록 예시 안내.
def build_keyword_missing_card() -> dict[str, Any]:
    return {
        "text": (
            "🤔 어떤 분야 전문가를 찾으시나요?\n"
            "주제 키워드를 같이 알려주세요. 예를 들면:\n"
            "  • `벡터DB 전문가 추천`\n"
            "  • `Kafka 잘 아는 사람`\n"
            "  • `리눅스 서버 구축 베테랑`"
        )
    }


# 매칭 0 + 동의자 0 (공용 데이터도 없고 OAuth 동의자도 없음).
def build_no_data_card() -> dict[str, Any]:
    return {
        "text": (
            "📭 아직 검색할 수 있는 사내 데이터가 부족해요.\n"
            "더 정확한 추천을 위해 동료들이 챗에서 `내 데이터 연결` 카드를 눌러 OAuth 동의를 해주시면 좋아요.\n"
            "팀 Shared Drive · Confluence 스페이스 · 공용 캘린더가 등록되어 있는지도 한번 확인해 주세요."
        )
    }


# 매칭 0 + 동의자 있음 (키워드 관련 활동을 찾지 못함).
def build_no_match_card(query: str) -> dict[str, Any]:
    q = (query or "").strip()
    return {
        "text": (
            f"🔍 사내에 *{q}* 관련 문서·메일·일정이 거의 없어요.\n"
            "다른 키워드로 다시 시도해 보시겠어요? 예를 들면:\n"
            "  • 영문 ↔ 한글 표기 (예: `Kafka` ↔ `카프카`)\n"
            "  • 약어 ↔ 풀네임 (예: `MES` ↔ `생산관리시스템`)\n"
            "  • 좁은 키워드 ↔ 넓은 카테고리"
        )
    }


# 모든 API 호출 실패.
def build_all_failed_card() -> dict[str, Any]:
    return {
        "text": (
            "⚠️ 지금 데이터 조회가 불안정해요.\n"
            "잠시 후 다시 시도해 주시고, 계속 같은 문제면 운영 담당자에게 알려주세요."
        )
    }


# 본격 결과 카드 — Top 3, 1위 강조, 근거 자료.
def build_result_card(
    *,
    query: str,
    experts: list[dict[str, Any]],
    window_label: str = "최근 6개월",
    member_pool: list[dict[str, Any]] | None = None,
    draft: dict[str, Any] | None = None,
    requester_excluded: bool = False,
) -> dict[str, Any]:
    """추천 결과 카드.

    Args:
        query: 검색 키워드.
        experts: ``scoring.score_candidates`` 결과 (점수 내림차순). 상위 3명만 표시.
        window_label: 사용된 시간 윈도우 (subtitle 노출).
        member_pool: 소속팀 라벨링용.
        draft: ``recommend.recommend`` 결과 — ``{"experts": [{"email", "reason"}]}``.
            None 이면 추천 멘트 없이 raw evidence 만 표시.
        requester_excluded: 요청자 본인이 후보에 잡혔다가 제외됐으면 True — 카드에 명시.
    """
    safe_query = html.escape(query or "")
    safe_window = html.escape(window_label)
    top3 = (experts or [])[:3]
    member_pool = member_pool or []

    # email → AI 추천 reason / has_evidence 판정 map.
    reasons: dict[str, str] = {}
    verdicts: dict[str, Any] = {}
    for e in (draft or {}).get("experts") or []:
        em = (e.get("email") or "").strip().lower()
        rs = (e.get("reason") or "").strip()
        if em and rs:
            reasons[em] = rs
        if em:
            verdicts[em] = e.get("has_evidence")

    # AI 가 "근거 없음"으로 판정한 후보는 강등 — 점수 대신 참고 표시.
    # 판정이 없으면(draft 실패·구버전 응답) 근거 있음으로 간주 (안전 폴백).
    def _is_weak(entry: dict[str, Any]) -> bool:
        return verdicts.get((entry.get("email") or "").strip().lower()) is False

    # 이중 조건 제외 — AI "근거 없음" AND 점수가 1위의 30% 미만이면 아예 미노출.
    top_score = float(top3[0].get("score") or 0.0) if top3 else 0.0
    visible = [
        e for e in top3
        if not (_is_weak(e) and float(e.get("score") or 0.0) < top_score * 0.3)
    ]
    strong = [e for e in visible if not _is_weak(e)]
    weak = [e for e in visible if _is_weak(e)]

    sections: list[dict[str, Any]] = []

    # 1위 — 강조 (근거 있는 후보 중 최고점).
    if strong:
        sections.append({
            "header": "🏆 가장 유력한 전문가",
            "widgets": [{"textParagraph": {"text": _render_expert_block(strong[0], member_pool, rank=1, reasons=reasons)}}],
        })

    # 2·3위 — 작게.
    others = strong[1:]
    if others:
        widgets: list[dict[str, Any]] = []
        for i, e in enumerate(others):
            if i > 0:
                widgets.append({"divider": {}})
            widgets.append({"textParagraph": {"text": _render_expert_block(e, member_pool, rank=i + 2, reasons=reasons)}})
        sections.append({
            "header": "그 외 후보",
            "widgets": widgets,
        })

    # 근거 약한 후보 — 점수 없이 참고용으로만.
    if weak:
        widgets = []
        for i, e in enumerate(weak):
            if i > 0:
                widgets.append({"divider": {}})
            widgets.append({"textParagraph": {"text": _render_expert_block(e, member_pool, rank=0, reasons=reasons, demoted=True)}})
        sections.append({
            "header": "참고 — 언급은 있으나 직접 근거 약함",
            "widgets": widgets,
        })

    # 본인 제외 안내 — 본인이 실제로 후보에 잡혔던 경우에만 한 줄 명시.
    if requester_excluded and sections:
        sections[-1]["widgets"].append(
            {
                "textParagraph": {
                    "text": (
                        "ℹ️ 이 주제에 회원님 본인의 활동도 있었지만, "
                        "본인은 추천 대상에서 제외하고 보여드려요."
                    )
                }
            }
        )

    # 푸터 — 같은 section 마지막 widget 으로 (sections 분리하면 chat 이 silently drop 한 사례 회피).
    if sections:
        sections[-1]["widgets"].append(
            {"textParagraph": {"text": f"<font color=\"#888888\">{_FOOTER_TEXT}</font>"}}
        )
    else:
        sections.append({
            "widgets": [{"textParagraph": {"text": _FOOTER_TEXT}}],
        })

    return {
        "cardsV2": [
            {
                "cardId": "expert_finder_result",
                "card": {
                    "header": {
                        "title": f"🔍 {safe_query} 전문가",
                        "subtitle": f"검색 범위: {safe_window} · Top {len(top3)}",
                    },
                    "sections": sections,
                },
            }
        ]
    }


# 후보자 1명 블록 — 이름·이메일·소속팀·점수·(AI 멘트)·근거 자료.
def _render_expert_block(
    entry: dict[str, Any],
    member_pool: list[dict[str, Any]],
    rank: int,
    reasons: dict[str, str] | None = None,
    demoted: bool = False,
) -> str:
    email = (entry.get("email") or "").strip()
    name = (entry.get("name") or "").strip()
    score = float(entry.get("score") or 0.0)
    hits_by_source = entry.get("hits_by_source") or {}
    is_consenting = bool(entry.get("is_consenting"))
    evidence = entry.get("evidence") or []

    # 소속팀 — member_pool 에 있으면 표시.
    team_label = ""
    member = _find_member(email, member_pool)
    if member:
        if not name:
            name = member.get("name") or ""
        team_name = (member.get("team_name") or "").strip()
        if team_name:
            team_label = f" · {html.escape(team_name)}"

    # 이름 표시 우선순위: name > email
    display = html.escape(name) if name else html.escape(email)
    safe_email = html.escape(email)

    consent_badge = "" if is_consenting else " <font color=\"#888888\">(공용 데이터 기반)</font>"

    # 소스별 hit 분포 요약 — "Drive 3 · Confluence 1" 식.
    source_summary = " · ".join(
        f"{_SOURCE_LABEL.get(s, s)} {n}"
        for s, n in sorted(hits_by_source.items(), key=lambda x: -x[1])
    )

    # 근거 자료 — 최대 3개, 제목+링크.
    evidence_lines: list[str] = []
    seen_titles: set[str] = set()
    for h in evidence:
        title = (h.get("title") or "").strip()
        url = (h.get("url") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        safe_title = html.escape(title[:60])
        if url:
            evidence_lines.append(f"&nbsp;&nbsp;◦ <a href=\"{html.escape(url)}\">{safe_title}</a>")
        else:
            evidence_lines.append(f"&nbsp;&nbsp;◦ {safe_title}")
        if len(evidence_lines) >= 3:
            break

    if demoted:
        # 근거 약함 강등 — 점수·순위 없이 회색 참고 표시. 점수를 보여주면
        # "근거 없음" 멘트와 숫자가 모순되어 신뢰를 깎는다.
        lines = [
            f"<font color=\"#888888\"><b>{display}</b> &lt;{safe_email}&gt;{team_label}</font>",
        ]
        if source_summary:
            lines.append(f"<font color=\"#888888\">&nbsp;&nbsp;{source_summary}</font>")
        if reasons:
            rs = reasons.get(email.lower())
            if rs:
                lines.append(f"<font color=\"#888888\">&nbsp;&nbsp;💬 <i>{html.escape(rs)}</i></font>")
        lines.extend(evidence_lines)
        return "<br>".join(lines)

    rank_label = "" if rank == 1 else f"{rank}위 — "
    lines = [
        f"<b>{rank_label}{display}</b> &lt;{safe_email}&gt;{team_label}{consent_badge}",
        f"&nbsp;&nbsp;점수 {score:.1f} · {source_summary}" if source_summary else f"&nbsp;&nbsp;점수 {score:.1f}",
    ]
    # AI 추천 멘트 (있으면, 점수 줄 다음·근거 자료 앞).
    if reasons:
        rs = reasons.get(email.lower())
        if rs:
            lines.append(f"&nbsp;&nbsp;💬 <i>{html.escape(rs)}</i>")
    lines.extend(evidence_lines)
    return "<br>".join(lines)


_SOURCE_LABEL = {
    "drive": "Drive",
    "confluence": "Confluence",
    "calendar": "Calendar",
    "gmail": "Gmail",
}


def _find_member(email: str, member_pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = (email or "").strip().lower()
    if not needle:
        return None
    for m in member_pool:
        if (m.get("email") or "").strip().lower() == needle:
            return m
    return None
