"""Confluence 주간회의 폴더 탐색 — 연도/분기 폴더 찾기·생성.

구 프로젝트 confluence/folder.py 포팅.
resolve_report_folders는 client를 파라미터로 받도록 수정.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Tuple, Union

from api.confluence.client import ConfluenceClient

# 연도 폴더 제목에서 4자리 연도(19xx/20xx) 추출용
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _derive_year_folder_title(children: list, year: int) -> str:
    """기존 형제 연도 폴더의 네이밍 규칙을 따라 새 연도 폴더 제목을 만든다.

    예) 형제로 '2026년_주간회의' 가 있으면 year=2027 → '2027년_주간회의'.
        형제로 '2026' 만 있으면 → '2027'.
    형제 중 연도가 가장 큰(최근) 것을 기준으로 삼고, 그 연도 부분만 target year 로 치환.
    연도 패턴을 가진 형제 폴더가 없으면 기본값 'YYYY년' 으로 폴백.
    """
    best_title: Optional[str] = None
    best_year = -1
    for c in children:
        if c.get("type") != "folder":
            continue
        title = (c.get("title") or "").strip()
        m = _YEAR_RE.search(title)
        if not m:
            continue
        y = int(m.group(0))
        if y > best_year:
            best_year = y
            best_title = title
    if best_title is None:
        return f"{year}년"
    m = _YEAR_RE.search(best_title)
    return best_title[: m.start()] + str(year) + best_title[m.end():]


def _quarter_from_month(month: int) -> int:
    if month <= 3:
        return 1
    if month <= 6:
        return 2
    if month <= 9:
        return 3
    return 4


def _find_folder_by_title(children: list, title: str) -> Optional[str]:
    for c in children:
        if c.get("type") == "folder" and (c.get("title") or "").strip() == title:
            return str(c["id"])
    return None


def get_or_create_year_folder(
    client: ConfluenceClient,
    root_id: str,
    space_id: str,
    year: Optional[int] = None,
    create_if_missing: bool = True,
) -> Optional[str]:
    """루트 폴더 아래 연도 폴더(예: '2026년') 반환. create_if_missing=False면 없을 때 None."""
    year = year or datetime.now().year
    title = f"{year}년"
    children = client.get_folder_direct_children(root_id)
    year_text = str(year)
    candidates = [c for c in children if c.get("type") == "folder" and year_text in (c.get("title") or "")]

    selected = None
    for c in candidates:
        if "주간회의" in (c.get("title") or ""):
            selected = c
            break
    if selected is None:
        for c in candidates:
            if (c.get("title") or "").strip() == title:
                selected = c
                break
    if selected is None and candidates:
        selected = candidates[0]
    if selected is not None:
        return str(selected["id"])

    if not create_if_missing:
        return None
    # 신규 연도 폴더는 기존 형제 폴더 네이밍 규칙을 따른다 (예: '2027년_주간회의')
    new_title = _derive_year_folder_title(children, year)
    created = client.create_folder(space_id=space_id, title=new_title, parent_id=root_id)
    return str(created["id"])


def get_or_create_quarter_folder(
    client: ConfluenceClient,
    year_folder_id: str,
    space_id: str,
    quarter: Optional[int] = None,
    create_if_missing: bool = True,
) -> Optional[str]:
    """연도 폴더 아래 분기 폴더(예: '2분기') 반환."""
    quarter = quarter or _quarter_from_month(datetime.now().month)
    title = f"{quarter}분기"
    children = client.get_folder_direct_children(year_folder_id)
    found = _find_folder_by_title(children, title)
    if found:
        return found
    if not create_if_missing:
        return None
    try:
        created = client.create_folder(space_id=space_id, title=title, parent_id=year_folder_id)
        return str(created["id"])
    except Exception:
        # 동시 실행으로 중복 생성 실패 시 재조회
        children = client.get_folder_direct_children(year_folder_id)
        found = _find_folder_by_title(children, title)
        if found:
            return found
        raise


def _parse_title_date(title: str) -> Optional[str]:
    """제목 앞 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM:SS' → 비교 가능한 문자열."""
    if not title or len(title) < 10:
        return None
    date_part = title[:10]
    if len(title) >= 19 and title[10:11] == " " and title[11:19].count(":") == 2:
        return f"{date_part}T{title[11:19]}"
    return f"{date_part}T00:00:00"


def _title_start_date(title: str) -> Optional[date]:
    ts = _parse_title_date(title)
    if ts is None:
        return None
    try:
        return datetime.strptime(ts[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def get_latest_weekly_report_page_id_before_meeting_date(
    client: ConfluenceClient,
    root_id: str,
    space_id: str,
    team_name: str,
    before_meeting_date: Union[datetime, date],
) -> Optional[str]:
    """연·분기 폴더 전체에서 team_name 포함, before_meeting_date 이전 중 최신 페이지 ID."""
    cutoff = before_meeting_date.date() if isinstance(before_meeting_date, datetime) else before_meeting_date
    best_id: Optional[str] = None
    best_d: Optional[date] = None
    for year in (cutoff.year, cutoff.year - 1):
        year_id = get_or_create_year_folder(client, root_id, space_id, year=year, create_if_missing=False)
        if not year_id:
            continue
        for quarter in (1, 2, 3, 4):
            quarter_id = get_or_create_quarter_folder(client, year_id, space_id, quarter=quarter, create_if_missing=False)
            if not quarter_id:
                continue
            try:
                children = client.get_folder_direct_children(quarter_id, limit=200)
            except Exception:
                continue
            for c in children:
                if c.get("type") != "page":
                    continue
                title = c.get("title") or ""
                if team_name not in title:
                    continue
                d = _title_start_date(title)
                if d is None or d >= cutoff:
                    continue
                if best_d is None or d > best_d:
                    best_d = d
                    best_id = str(c["id"])
    return best_id


def get_latest_weekly_report_page_id(
    client: ConfluenceClient,
    quarter_folder_id: str,
    title_contains: str = "주간회의",
) -> Optional[str]:
    """분기 폴더에서 title_contains 포함 페이지 중 제목 날짜 최신 ID."""
    children = client.get_folder_direct_children(quarter_folder_id)
    best_id: Optional[str] = None
    best_ts: Optional[str] = None
    for c in children:
        if c.get("type") != "page":
            continue
        if title_contains not in (c.get("title") or ""):
            continue
        ts = _parse_title_date(c.get("title") or "")
        if ts is None:
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_id = str(c["id"])
    return best_id


def resolve_report_folders(
    client: ConfluenceClient,
    root_id: str,
    team_name: str = "",
    create_if_missing: bool = True,
    reference_date: Optional[Union[datetime, date]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    루트 폴더 아래 YYYY년 > N분기 경로 탐색/생성.

    Returns:
        (연도_폴더_ID, 분기_폴더_ID, 최신_주간회의_페이지_ID | None)
    """
    root_folder = client.get_folder(root_id)
    space_id = str(root_folder.get("spaceId", ""))
    if not space_id:
        raise RuntimeError("루트 폴더에서 spaceId를 가져올 수 없습니다.")

    if reference_date is not None:
        if isinstance(reference_date, datetime):
            y, q = reference_date.year, _quarter_from_month(reference_date.month)
        else:
            y, q = reference_date.year, _quarter_from_month(reference_date.month)
    else:
        now = datetime.now()
        y, q = now.year, _quarter_from_month(now.month)

    # 연도 폴더 없이 분기 폴더가 root 바로 아래 있는 flat 구조 우선 확인
    # (이 경우 새 연도 폴더를 만들려다 Confluence 중복 제목 400 에러가 발생하거나,
    #  새 분기 진입 시 연도 폴더가 한 단계 더 중첩 생성되는 문제가 발생함)
    try:
        root_children = client.get_folder_direct_children(root_id, limit=200)
    except Exception:
        root_children = None

    if root_children is not None:
        flat_quarter_id = _find_folder_by_title(root_children, f"{q}분기")
        # 현재 분기 폴더가 아직 없어도, 다른 분기 폴더(N분기)가 root 바로 아래 있으면
        # root 자체가 연도 폴더인 flat 구조로 판단한다. (예: '2026년_주간회의' 아래
        # 1·2분기만 있는 상태에서 7월에 3분기로 진입하는 경우)
        is_flat = flat_quarter_id is not None or any(
            _find_folder_by_title(root_children, f"{n}분기") for n in (1, 2, 3, 4)
        )
        if is_flat:
            quarter_id = flat_quarter_id or get_or_create_quarter_folder(
                client, root_id, space_id, quarter=q, create_if_missing=create_if_missing
            )
            if quarter_id is None:
                return (None, None, None)
            latest_id = get_latest_weekly_report_page_id(
                client, quarter_id, title_contains=team_name or "주간회의"
            )
            return (None, quarter_id, latest_id)

    year_id = get_or_create_year_folder(client, root_id, space_id, year=y, create_if_missing=create_if_missing)
    if year_id is None:
        return (None, None, None)

    quarter_id = get_or_create_quarter_folder(client, year_id, space_id, quarter=q, create_if_missing=create_if_missing)
    if quarter_id is None:
        return (year_id, None, None)

    latest_id = get_latest_weekly_report_page_id(client, quarter_id)
    return (year_id, quarter_id, latest_id)
