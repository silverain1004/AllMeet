"""주간보고초안 시간 범위 — KST 기준 회의주 + 전주 (두 주치).

회의가 미뤄져도 두 주 안의 데이터는 안전하게 잡히도록 14일 폭.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

KST = timezone(timedelta(hours=9))


def kst_now() -> datetime:
    """현재 시각 (KST aware)."""
    return datetime.now(KST)


def week_monday_kst(d: datetime) -> datetime:
    """주어진 일자가 속한 주의 월요일 00:00 KST."""
    d_kst = d.astimezone(KST)
    monday = d_kst - timedelta(days=d_kst.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def two_weeks_around_kst(meeting_date: datetime) -> tuple[datetime, datetime]:
    """회의 일자가 속한 주 + 직전 주 (월~일 두 주치) 의 KST 범위.

    Returns:
        (prev_monday_kst, this_sunday_end_kst)
    """
    this_monday = week_monday_kst(meeting_date)
    prev_monday = this_monday - timedelta(days=7)
    this_sunday_end = this_monday + timedelta(days=7) - timedelta(microseconds=1)
    return prev_monday, this_sunday_end


def two_weeks_around_utc_iso(meeting_date: datetime) -> tuple[str, str]:
    """KST 두 주치 → UTC RFC3339 Z (Calendar/Drive/Gmail API 호환)."""
    start_kst, end_kst = two_weeks_around_kst(meeting_date)
    return _to_utc_z(start_kst), _to_utc_z(end_kst)


def _to_utc_z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _title_matches(summary: str, team_name: str) -> bool:
    """이벤트 제목이 '<팀명> ... 주간회의' 인지 — 공백 무시 매칭.

    공유 팀 캘린더에는 여러 팀의 주간회의가 섞여 있어, 본인 팀 회의만 추려야 한다.
    team_name 이 비면 '주간회의' 포함 여부만 본다(폴백).
    """
    compact = summary.replace(" ", "")
    if "주간회의" not in compact:
        return False
    if not team_name:
        return True
    return team_name.replace(" ", "") in compact


def find_meeting_date(
    events: Iterable[dict],
    reference: datetime | None = None,
    *,
    team_name: str = "",
) -> datetime | None:
    """주간회의 이벤트 후보들 중 기준 시각에 가장 가까운 회의 일자.

    우선순위: 미래 회의 중 가장 빠른 것 → 없으면 과거 중 가장 늦은 것.

    ``team_name`` 이 주어지면 제목에 팀명 + '주간회의' 가 함께 든 이벤트만 후보로 삼는다.
    공유 캘린더 ``q="주간회의"`` 검색은 다른 팀 회의·본문 매칭까지 잡아오므로,
    본인 팀 회의로 좁히지 않으면 가장 가까운 *남의 팀* 회의 일자가 찍힌다.
    팀명 일치 이벤트가 하나도 없으면 '주간회의' 제목만으로 폴백한다.
    """
    ref = (reference or kst_now()).astimezone(KST)
    events = list(events)

    def _to_candidates(items: list[dict]) -> list[datetime]:
        out: list[datetime] = []
        for e in items:
            start = (e.get("start") or "").strip()
            if not start:
                continue
            try:
                s = start.replace("Z", "+00:00")
                out.append(datetime.fromisoformat(s).astimezone(KST))
            except Exception:
                continue
        return out

    if team_name:
        matched = [e for e in events if _title_matches(str(e.get("summary") or ""), team_name)]
        candidates = _to_candidates(matched)
        if not candidates:
            # 팀명 일치 없음 → 제목에 '주간회의' 든 것만으로 폴백 (본문-only 매칭 제외).
            title_only = [e for e in events if _title_matches(str(e.get("summary") or ""), "")]
            candidates = _to_candidates(title_only)
    else:
        candidates = _to_candidates(events)

    if not candidates:
        return None
    future = [d for d in candidates if d >= ref]
    if future:
        return min(future)
    past = [d for d in candidates if d < ref]
    return max(past) if past else None


def fallback_meeting_date_kst(now: datetime | None = None) -> datetime:
    """캘린더에서 회의 일자를 못 찾았을 때 폴백 — 이번주 금요일 10:00 KST."""
    base = (now or kst_now()).astimezone(KST)
    days_to_friday = (4 - base.weekday()) % 7
    target = base + timedelta(days=days_to_friday)
    return target.replace(hour=10, minute=0, second=0, microsecond=0)
