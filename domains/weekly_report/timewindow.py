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


def find_meeting_date(events: Iterable[dict], reference: datetime | None = None) -> datetime | None:
    """주간회의 이벤트 후보들 중 기준 시각에 가장 가까운 회의 일자.

    우선순위: 미래 회의 중 가장 빠른 것 → 없으면 과거 중 가장 늦은 것.
    """
    ref = (reference or kst_now()).astimezone(KST)
    candidates: list[datetime] = []
    for e in events:
        start = (e.get("start") or "").strip()
        if not start:
            continue
        try:
            s = start.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s).astimezone(KST)
            candidates.append(dt)
        except Exception:
            continue
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
