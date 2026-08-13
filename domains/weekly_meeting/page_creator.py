"""주간회의 페이지 자동 생성 — Cloud Scheduler HTTP 트리거 진입점.

두 가지 모드 (Firestore weekly_page_mode 필드로 결정):
  - copy_latest   (MES2): 최신 페이지 복사 후 주차 헤더 교체
  - from_template (PC2) : 템플릿 + 이전 주 내용 인계로 신규 생성
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from config.settings import VACATION_CALENDAR_ID
from api.confluence.client import ConfluenceClient
from api.confluence.folder import resolve_report_folders
from api.confluence.previous_report import (
    fetch_weekly_report_html,
    get_latest_weekly_report_page_id_for_team_root,
    get_previous_weekly_report_content,
)
from domains.weekly_meeting.page_html import (
    apply_bold_adjacent_status_chips,
    apply_status_chip_colors,
    expand_name_placeholder_rows,
    fill_schedule_table_vacations,
    get_week_range_placeholders,
    html_to_plain_text_block,
    is_empty_or_placeholder_content,
    normalize_member_name,
    remove_schedule_table_vertical_space,
    update_week_range_in_assignee_table,
    update_week_range_in_schedule_table,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# URL → 숫자 ID 추출
# ---------------------------------------------------------------------------

def _extract_confluence_id(url_or_id: str) -> str:
    """Confluence URL 또는 숫자 ID에서 폴더/페이지 숫자 ID만 추출.

    예) https://vntg.atlassian.net/wiki/spaces/PC2/folder/2275213400?... → "2275213400"
        https://vntg.atlassian.net/wiki/spaces/PC2/pages/2274952548/... → "2274952548"
        "2274952548" → "2274952548"
    """
    text = (url_or_id or "").strip()
    if not text:
        return ""
    m = re.search(r"/(?:folder|pages|content)/(\d+)", text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return ""


# ---------------------------------------------------------------------------
# 설정 헬퍼
# ---------------------------------------------------------------------------

def _get_root_id(cfg: dict) -> str:
    """cfg에서 report root folder 숫자 ID 추출.

    우선순위: root_pages[0] → report_root_page_id.
    저장 형태가 URL이든 숫자 ID든 모두 처리.
    """
    root_pages = cfg.get("root_pages") or []
    for entry in root_pages:
        # dict {"page_id": "..."} 또는 string URL 모두 처리
        raw = str(entry.get("page_id") if isinstance(entry, dict) else entry).strip()
        folder_id = _extract_confluence_id(raw)
        if folder_id:
            return folder_id
    # 폴백: report_root_page_id
    return _extract_confluence_id(str(cfg.get("report_root_page_id") or ""))


def _get_template_id(cfg: dict) -> str:
    """template_page_id 에서 숫자 ID 추출.

    예) https://.../pages/2274952548/YYYY-MM-DD → "2274952548"
    """
    return _extract_confluence_id(str(cfg.get("template_page_id") or ""))


def _get_space_key(cfg: dict) -> str:
    return str(cfg.get("confluence_space_key") or cfg.get("space_key") or "").strip()


def _get_member_names(cfg: dict) -> list[str]:
    members = cfg.get("team_members") or []
    result = []
    for m in members:
        name = m.get("name", "").strip() if isinstance(m, dict) else str(m).strip()
        if name:
            result.append(name)
    return result


# ---------------------------------------------------------------------------
# 캘린더 기반 일정 공유 표 데이터 빌드 (출장/외근/재택/휴가)
# 원본 vacation_fetcher.py 방식: q 없이 전체 이벤트 조회 → 로컬 파싱
# ---------------------------------------------------------------------------

_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")

# 원본 vacation_fetcher.py CATEGORY_MAP 과 동일
# 리프레시 계열은 vacation 으로 분류 → 일정 공유 표 '휴가' 행에 표시
_VACATION_CATEGORY_MAP: dict[str, str] = {
    "출장": "business_trip",
    "외근": "field_work",
    "재택": "remote",
    "휴가": "vacation",
    "연차": "vacation",
    "반차": "vacation",
    "반반차": "vacation",
    "오전반차": "vacation",
    "오후반차": "vacation",
    "오전반반차": "vacation",
    "오후반반차": "vacation",
    "경조": "vacation",
    "공가": "vacation",
    "병가": "vacation",
    "조퇴": "vacation",
    "출산(육아)": "vacation",
    "출산휴가": "vacation",
    "육아휴직": "vacation",
    "대체": "vacation",
    "아이돌봄": "vacation",
    "아이돌봄휴가": "vacation",
    "특별휴가(연차)": "vacation",
    "특별휴가(반차)": "vacation",
    "리프레쉬": "vacation",
    "리프레시": "vacation",
    "리프레쉬휴가": "vacation",
    "개인특별휴가": "vacation",
    "생일": "vacation",
    "샌드위치": "vacation",
    "장기근속": "vacation",
}


# 부분일치 폴백 키워드 → 카테고리 (우선순위 순).
# 출산휴가·특별휴가 등은 위 _VACATION_CATEGORY_MAP 정확일치에서 이미 처리되므로
# 여기 '휴가' 폴백까지 내려오지 않는다.
_KEYWORD_CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("출장", "business_trip"),
    ("외근", "field_work"),
    ("재택", "remote"),
    ("휴가", "vacation"),
    ("연차", "vacation"),
    ("반차", "vacation"),
    ("반반차", "vacation"),
    ("공가", "vacation"),
    ("병가", "vacation"),
    ("경조", "vacation"),
)


def _categorize_kind_by_keyword(kind: str) -> Optional[str]:
    """종류 문자열에 카테고리 키워드가 포함되면 해당 카테고리 반환."""
    for label, key in _KEYWORD_CATEGORY_ORDER:
        if label in kind:
            return key
    return None


def _resolve_vacation_category(kind: str) -> Optional[str]:
    """종류 문자열 → 카테고리 키. 정확일치 → 공백제거 → 오전/오후 접두 → 키워드 폴백."""
    if not kind:
        return None
    cat = _VACATION_CATEGORY_MAP.get(kind)
    if cat is None and " " in kind:
        cat = _VACATION_CATEGORY_MAP.get(kind.replace(" ", ""))
    if cat is None and (kind.startswith("오전 ") or kind.startswith("오후 ")):
        cat = _VACATION_CATEGORY_MAP.get(kind[3:].strip())
        if cat is None:
            cat = _VACATION_CATEGORY_MAP.get(kind[3:].strip().replace(" ", ""))
    if cat is None:
        # 폴백: 지역 접두/접미가 붙은 종류(예: '합정출장', '군산 출장', '출장(군산)')
        cat = _categorize_kind_by_keyword(kind)
    return cat


def _strip_name_annotation(raw_name: str) -> tuple[str, str]:
    """'김도현(대체)' → ('김도현', '대체'). 주석 없으면 annotation=''."""
    text = (raw_name or "").strip()
    m = re.match(r"^(.+?)\(([^)]+)\)\s*$", text)
    if not m:
        return text, ""
    return m.group(1).strip(), m.group(2).strip()


_DASH_CHARS = frozenset("-–—")


def _parse_paren_vacation_event(summary: str) -> Optional[tuple[str, list[str], str]]:
    """PC2/MES2 형식: '종류(이름)' 또는 '종류(이름1, 이름2)'."""
    m = re.match(r"^(.+?)\(([^)]+)\)\s*$", summary.strip())
    if not m:
        return None
    kind = m.group(1).strip()
    # '연차 - 김도현(대체)' 처럼 대시 형식에 이름 주석이 붙은 경우 제외
    if any(ch in kind for ch in _DASH_CHARS):
        return None
    names_str = m.group(2).strip()
    cat = _resolve_vacation_category(kind)
    if cat is None:
        return None
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    if not names:
        return None
    return (cat, names, kind)


def _parse_dash_vacation_event(summary: str) -> list[tuple[str, list[str], str]]:
    """ERP2 형식: '종류 - 이름' / '종류-이름' / 한 제목에 여러 쌍·여러 이름.

    예)
      재택 - 정주현
      합정출장-김도현
      연차 - 김도현(대체), 박소영(대체)
      오전반차 - 박소영, 연차 - 임연주
      연차 - 김도현, 정주현
    """
    text = (summary or "").strip()
    if not text or not any(ch in text for ch in _DASH_CHARS):
        return []

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return []

    results: list[tuple[str, list[str], str]] = []
    current_kind: Optional[str] = None
    current_entries: list[tuple[str, str]] = []  # (pure_name, annotation)

    def _flush() -> None:
        nonlocal current_kind, current_entries
        if not current_kind or not current_entries:
            current_kind = None
            current_entries = []
            return
        cat = _resolve_vacation_category(current_kind)
        if cat is None:
            current_kind = None
            current_entries = []
            return
        # 주석이 있으면 표시용 kind 에 병기 (같은 주석끼리 묶음)
        by_ann: dict[str, list[str]] = {}
        for pure, ann in current_entries:
            by_ann.setdefault(ann, []).append(pure)
        for ann, names in by_ann.items():
            display_kind = f"{current_kind}({ann})" if ann else current_kind
            results.append((cat, names, display_kind))
        current_kind = None
        current_entries = []

    for part in parts:
        # 대시로 종류/이름 분리 — 첫 번째 대시만 사용
        m = re.match(r"^(.+?)\s*[-–—]\s*(.+)$", part)
        if m:
            _flush()
            current_kind = m.group(1).strip()
            pure, ann = _strip_name_annotation(m.group(2).strip())
            current_entries = [(pure, ann)] if pure else []
        else:
            # 대시 없음 → 직전 종류의 추가 이름
            if current_kind is None:
                continue
            pure, ann = _strip_name_annotation(part)
            if pure:
                current_entries.append((pure, ann))

    _flush()
    return results


def _parse_vacation_event(summary: str) -> list[tuple[str, list[str], str]]:
    """휴가/일정 이벤트 제목 파싱. 0개 이상 (카테고리, 이름목록, 표시종류) 반환.

    지원 형식:
      - PC2/MES2: '종류(이름)' / '종류(이름1, 이름2)'
      - ERP2:     '종류 - 이름' / '종류-이름' / 한 제목에 여러 쌍
    """
    if not summary or not str(summary).strip():
        return []
    text = str(summary).strip()
    paren = _parse_paren_vacation_event(text)
    if paren is not None:
        return [paren]
    return _parse_dash_vacation_event(text)


def _build_team_lookup(
    team_members_cfg: list,
) -> tuple[set[str], dict[str, str]]:
    """Firestore team_members → (valid_ids, nickname_to_name).

    원본 vacation_fetcher._build_team_lookup 과 동일.
    valid_ids: 정식 이름 + 모든 닉네임 → 이벤트 이름 매칭에 사용.
    nickname_to_name: 닉네임 → 정식 이름 변환.
    """
    valid_ids: set[str] = set()
    nickname_to_name: dict[str, str] = {}
    for m in (team_members_cfg or []):
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or "").strip()
        if not name:
            continue
        valid_ids.add(name)
        for n in (m.get("nickname") or []):
            n = (n or "").strip()
            if n:
                valid_ids.add(n)
                nickname_to_name[n] = name
    return valid_ids, nickname_to_name


def _get_event_dates(event: dict) -> list[str]:
    """종일 이벤트를 일별 날짜 리스트(YYYY-MM-DD)로 펼침.

    원본 vacation_fetcher._get_event_dates 와 동일.
    Google Calendar all-day 이벤트는 end 가 마지막 날 +1일(exclusive).
    """
    start_str = (event.get("start") or "")[:10]
    end_str = (event.get("end") or "")[:10]
    if not start_str:
        return []
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d").date()
        if end_str and end_str > start_str:
            e = datetime.strptime(end_str, "%Y-%m-%d").date()
            result: list[str] = []
            d = s
            while d < e:
                result.append(d.strftime("%Y-%m-%d"))
                d += timedelta(days=1)
            return result
        return [start_str]
    except ValueError:
        return [start_str] if start_str else []


def _consecutive_day_ranges(sorted_dates: list[str]) -> list[tuple[str, str]]:
    """연속된 날짜를 (시작, 끝) 쌍 리스트로 병합.

    원본 vacation_fetcher._consecutive_day_ranges 와 동일.
    """
    if not sorted_dates:
        return []
    ranges: list[tuple[str, str]] = []
    start = prev = sorted_dates[0]
    for d in sorted_dates[1:]:
        prev_d = datetime.strptime(prev, "%Y-%m-%d")
        cur_d = datetime.strptime(d, "%Y-%m-%d")
        if (cur_d - prev_d).days == 1:
            prev = d
        else:
            ranges.append((start, prev))
            start = prev = d
    ranges.append((start, prev))
    return ranges


def _format_date_range(start: str, end: str) -> str:
    """'MM/DD(요일)' 또는 'MM/DD-DD(요일-요일)' 포맷.

    원본 vacation_fetcher._format_merged_date_span 출력 형식과 동일.
    """
    s_d = datetime.strptime(start, "%Y-%m-%d")
    e_d = datetime.strptime(end, "%Y-%m-%d")
    if start == end:
        return f"{s_d.strftime('%m/%d')}({_WEEKDAY_KO[s_d.weekday()]})"
    if s_d.month == e_d.month:
        return (
            f"{s_d.strftime('%m/%d')}-{e_d.strftime('%d')}"
            f"({_WEEKDAY_KO[s_d.weekday()]}-{_WEEKDAY_KO[e_d.weekday()]})"
        )
    return (
        f"{s_d.strftime('%m/%d')}-{e_d.strftime('%m/%d')}"
        f"({_WEEKDAY_KO[s_d.weekday()]}-{_WEEKDAY_KO[e_d.weekday()]})"
    )


def _build_vacation_map(
    member_names: list[str],
    calendar_id: str,
    reference_date: datetime,
    team_members_cfg: list | None = None,
) -> dict[str, dict[str, str]]:
    """reference_date 기준 이번주/다음주 일정 카테고리 맵 빌드.

    원본 vacation_fetcher.build_vacation_data 방식으로 완전 재구현:
    - 닉네임 포함 팀원 매칭 (_build_team_lookup)
    - 종일 이벤트 일별 펼침 (_get_event_dates)
    - 이번주/다음주 각각 월~금 날짜 범위 체크 (KST 기준)
    - 연속 날짜 병합 및 범위 포맷 (_format_date_range)
    - 단일 캘린더 조회로 양쪽 주차 처리
    """
    if not calendar_id:
        return {}
    try:
        from domains.weekly_meeting.schedule_lookup import fetch_all_calendar_events
    except ImportError:
        return {}

    # 닉네임 포함 팀원 lookup 구성
    if team_members_cfg:
        valid_ids, nickname_to_name = _build_team_lookup(team_members_cfg)
    else:
        valid_ids = {normalize_member_name(n) for n in member_names if normalize_member_name(n)}
        nickname_to_name = {}

    if not valid_ids:
        return {}

    # reference_date 기준 이번 주 월요일 계산 (로컬 시간 = KST)
    ref = reference_date
    mon_this = (ref - timedelta(days=ref.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    fri_this = mon_this + timedelta(days=4)
    mon_next = mon_this + timedelta(days=7)
    fri_next = mon_next + timedelta(days=4)

    s_mon_this = mon_this.strftime("%Y-%m-%d")
    s_fri_this = fri_this.strftime("%Y-%m-%d")
    s_mon_next = mon_next.strftime("%Y-%m-%d")
    s_fri_next = fri_next.strftime("%Y-%m-%d")

    # 이번 주 월요일 ~ 다음 주 토요일 (exclusive) 범위 한 번에 조회 — KST 오프셋
    time_min = mon_this.strftime("%Y-%m-%dT00:00:00") + "+09:00"
    time_max = (fri_next + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00") + "+09:00"

    logger.info(
        "[vacation] 기준일=%s 이번주=%s~%s 다음주=%s~%s valid_ids=%s",
        ref.strftime("%Y-%m-%d"), s_mon_this, s_fri_this,
        s_mon_next, s_fri_next, sorted(valid_ids),
    )

    acc: dict[str, dict[str, list[str]]] = {
        "business_trip": {"this_week": [], "next_week": []},
        "field_work":    {"this_week": [], "next_week": []},
        "remote":        {"this_week": [], "next_week": []},
        "vacation":      {"this_week": [], "next_week": []},
    }

    # (cat, display_name, kind, week) → [date_str, ...]
    date_buckets: dict[tuple[str, str, str, str], list[str]] = {}

    try:
        res = fetch_all_calendar_events(
            time_min=time_min, time_max=time_max, calendar_id=calendar_id
        )
        if not res.ok:
            logger.warning("[vacation] 캘린더 조회 실패: %s", res.error_kind)
            return {}

        ev_list = [(e.get("summary"), e.get("start"), e.get("end")) for e in res.events]
        logger.info("[vacation] 이벤트 %d건: %s", len(res.events), ev_list)

        skipped: list[str] = []
        unmatched: list[str] = []

        for event in res.events:
            summary = event.get("summary", "")
            parsed_list = _parse_vacation_event(summary)
            if not parsed_list:
                skipped.append(summary)
                continue

            dates = _get_event_dates(event)
            if not dates:
                skipped.append(summary)
                continue

            for cat_key, event_names, kind in parsed_list:
                if cat_key not in acc:
                    continue

                for event_name in event_names:
                    # valid_ids 로 직접 매칭 (정식 이름 또는 닉네임)
                    matched_id: str | None = None
                    if event_name in valid_ids:
                        matched_id = event_name
                    else:
                        for vid in valid_ids:
                            if event_name in vid or vid in event_name:
                                matched_id = vid
                                break

                    if matched_id is None:
                        unmatched.append(f"{summary}({event_name})")
                        continue

                    # 닉네임이면 정식 이름으로 변환
                    display_name = nickname_to_name.get(matched_id, matched_id)

                    for date_str in dates:
                        if s_mon_this <= date_str <= s_fri_this:
                            week = "this_week"
                        elif s_mon_next <= date_str <= s_fri_next:
                            week = "next_week"
                        else:
                            continue  # 주말 또는 범위 외

                        bucket_key = (cat_key, display_name, kind, week)
                        if bucket_key not in date_buckets:
                            date_buckets[bucket_key] = []
                        if date_str not in date_buckets[bucket_key]:
                            date_buckets[bucket_key].append(date_str)

        if skipped:
            logger.info("[vacation] 파싱/카테고리 스킵 %d건: %s", len(skipped), skipped)
        if unmatched:
            logger.info("[vacation] 이름 미매칭 %d건: %s", len(unmatched), unmatched)

        # 날짜 정렬 → 연속 범위 병합 → 라인 생성
        result_lines: list[str] = []
        for (cat_key, display_name, kind, week), dates in date_buckets.items():
            dates.sort()
            for start, end in _consecutive_day_ranges(dates):
                line = f"{_format_date_range(start, end)} {display_name}({kind})"
                if line not in acc[cat_key][week]:
                    acc[cat_key][week].append(line)
                result_lines.append(f"{cat_key}[{week[:4]}]:{line}")
        if result_lines:
            logger.info("[vacation] 결과 %d건: %s", len(result_lines), result_lines)

    except Exception as exc:
        # 조회/파싱 중 실패하면 acc가 일부만 채워진 상태일 수 있어, 이 상태로 반환하면
        # "이번 주 실제로 없음"과 "조회 실패로 모름"을 구분할 수 없게 됨 — 표를 잘못
        # 비우지 않도록 빈 dict 반환(호출 측에서 표를 건드리지 않음).
        logger.warning("[vacation] 일정 조회 실패: %s", exc)
        return {}

    def _to_ul(lines: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>" if lines else ""

    # 조회에 성공했으면 4개 카테고리를 항상 포함(이벤트 없는 주는 빈 문자열) —
    # 그래야 fill_schedule_table_vacations 가 "이번 주엔 없음"을 명시적으로 받아서
    # 지난 주 데이터가 안 지워지고 남는 문제를 방지할 수 있음.
    result: dict[str, dict[str, str]] = {}
    for cat_key, weeks in acc.items():
        result[cat_key] = {
            "this_week": _to_ul(weeks["this_week"]),
            "next_week": _to_ul(weeks["next_week"]),
        }
    return result


# ---------------------------------------------------------------------------
# 캘린더 기반 다음 회의일 추정
# ---------------------------------------------------------------------------

def _find_reference_date(calendar_id: str, team_name: str) -> datetime:
    """오늘 이후의 다음 주간회의 일자. 캘린더 조회 실패 시 오늘 +7일.

    오늘이 회의 날짜면 이미 회의가 끝난 것으로 보고 다음 회의(다음 주)를 기준일로 잡는다.
    회의 시각(시:분)에 영향받지 않도록 날짜 경계로 조회하고 날짜 단위로 비교한다.
    """
    try:
        from api.calendar.events import list_events
        now = datetime.utcnow()
        # 회의 종료 시각 기준 필터(timeMin)에 흔들리지 않도록 오늘 0시부터 조회하고,
        # '오늘 이후' 판정은 아래 날짜 비교로 처리한다.
        time_min = now.strftime("%Y-%m-%dT00:00:00Z")
        time_max = (now + timedelta(days=14)).strftime("%Y-%m-%dT23:59:59Z")
        result = list_events(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            q=f"{team_name} 주간",
            max_results=10,
        )
        if result.ok:
            local_now = datetime.now()
            for event in result.events:
                # list_events는 "start"를 이미 str로 반환
                start_val = event.get("start") or ""
                date_str = start_val[:10] if isinstance(start_val, str) else (
                    start_val.get("date") or (start_val.get("dateTime") or "")[:10]
                )
                if date_str:
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    # 오늘 회의는 끝난 것으로 보고 건너뛴다 (> : 오늘 이후만)
                    if d.date() > local_now.date():
                        return d
    except Exception as e:
        logger.warning("캘린더 회의일 조회 실패: %s", e)
    return datetime.now() + timedelta(days=7)


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

def _find_page_in_folder(client: ConfluenceClient, folder_id: str, title: str) -> Optional[str]:
    """폴더에서 해당 제목 페이지 ID 반환. 없으면 None."""
    try:
        children = client.get_folder_direct_children(folder_id, limit=200)
        for c in children:
            if c.get("type") == "page" and c.get("title") == title:
                return str(c["id"])
    except Exception as e:
        logger.warning("기존 페이지 조회 실패 (folder_id=%s): %s", folder_id, e)
    return None


def _get_quarter_folder(
    client: ConfluenceClient,
    root_id: str,
    team_name: str,
    reference_date: datetime,
) -> str:
    """분기 폴더 ID. 없으면 RuntimeError."""
    _, quarter_id, _ = resolve_report_folders(
        client=client,
        root_id=root_id,
        team_name=team_name,
        create_if_missing=True,
        reference_date=reference_date,
    )
    if not quarter_id:
        raise RuntimeError(f"분기 폴더를 찾거나 생성할 수 없습니다. root_id={root_id}")
    return quarter_id


def _resolve_target_folder(
    client: ConfluenceClient,
    cfg: dict,
    root_id: str,
    team_name: str,
    reference_date: datetime,
) -> str:
    """페이지를 생성할 대상 폴더 ID.

    weekly_folder_layout="single" (ERP2): 연/분기 폴더 없이 root 폴더에 바로 생성.
    그 외 (PC2/MES2): 기존 YYYY년 > N분기 구조 탐색/생성.
    """
    layout = str(cfg.get("weekly_folder_layout") or "").strip().lower()
    if layout == "single":
        return root_id
    return _get_quarter_folder(client, root_id, team_name, reference_date)


# ---------------------------------------------------------------------------
# MES2: copy_latest
# ---------------------------------------------------------------------------

def _create_by_copy(team_id: str, cfg: dict) -> str:
    """최신 주간회의 페이지 복사 후 주차 헤더 교체 (MES2)."""
    team_name = str(cfg.get("team_name") or team_id)
    root_id = _get_root_id(cfg)
    if not root_id:
        raise RuntimeError(f"config/{team_id}: root_pages 또는 report_root_page_id 에서 폴더 ID를 추출할 수 없습니다.")

    calendar_id = str(cfg.get("calendar_id") or "")
    reference_date = _find_reference_date(calendar_id, team_name)
    logger.info("[%s] copy_latest 기준일: %s", team_id, reference_date.strftime("%Y-%m-%d"))

    client = ConfluenceClient()

    latest_page_id = get_latest_weekly_report_page_id_for_team_root(
        client, root_id, team_name, before_meeting_date=reference_date
    )
    if not latest_page_id:
        raise RuntimeError(
            f"복사 소스(이전 주간회의 페이지)를 찾을 수 없습니다. root_id={root_id}, team={team_name}"
        )

    full_html = fetch_weekly_report_html(client, latest_page_id)
    if not full_html:
        raise RuntimeError(f"복사 소스(page_id={latest_page_id}) HTML이 비어 있습니다.")

    week_phs = get_week_range_placeholders(reference_date)
    full_html = update_week_range_in_schedule_table(full_html, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
    full_html = update_week_range_in_assignee_table(full_html, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])

    # 일정 공유 표 — 캘린더에서 최신 일정 데이터로 교체 (출장/외근/재택/휴가)
    # calendar_id(회의 캘린더)로 폴백하면 안 됨: 휴가 이벤트가 없어 표가 비게 된다.
    vacation_cal_id = str(cfg.get("vacation_calendar_id") or VACATION_CALENDAR_ID or "")
    member_names = _get_member_names(cfg)
    vacation_map: dict[str, dict[str, str]] = {}
    if vacation_cal_id and member_names:
        logger.info("[%s] 휴가 캘린더: %s", team_id, vacation_cal_id)
        try:
            vacation_map = _build_vacation_map(
                    member_names, vacation_cal_id, reference_date,
                    team_members_cfg=cfg.get("team_members"),
                )
            if vacation_map:
                full_html = fill_schedule_table_vacations(full_html, vacation_map)
                logger.info("[%s] 일정 공유 업데이트 완료: %s", team_id, list(vacation_map.keys()))
            else:
                logger.info("[%s] 일정 공유: 해당 주차 이벤트 없음", team_id)
        except Exception as exc:
            logger.warning("[%s] 일정 공유 채우기 실패: %s", team_id, exc)

    status_chip_mode = str(cfg.get("status_chip_mode") or "").strip().lower()
    if status_chip_mode == "label_date":
        # 일정 공유 갱신과 같은 타이밍에 상태(날짜) 칩 색상도 함께 갱신 (ERP2 전용).
        full_html = apply_status_chip_colors(full_html)
        full_html = apply_bold_adjacent_status_chips(full_html)

    target_folder_id = _resolve_target_folder(client, cfg, root_id, team_name, reference_date)

    title = f"{reference_date.strftime('%Y-%m-%d')} {team_name} 주간회의"
    existing_id = _find_page_in_folder(client, target_folder_id, title)
    if existing_id:
        # 기존 페이지가 있으면 본문(사용자 작성 내용)은 보존하고
        # 일정 공유(휴가)·주차 날짜만 패치. vacation_map 이 비어도 스킵하지 않음
        # (자연어 재요청 / 스케줄러 재실행 시 최신 휴가 반영).
        try:
            existing_page = client.get_page(existing_id, expand="body.storage")
            existing_html = ((existing_page.get("body") or {}).get("storage") or {}).get("value") or ""
        except Exception as e:
            logger.warning("[%s] 기존 페이지 본문 조회 실패: %s", team_id, e)
            existing_html = ""

        if not existing_html:
            # 본문 조회 실패 시에만 복사본으로 전체 재생성
            client.update_page(existing_id, title=title, html_content=full_html)
            return f"기존 페이지 업데이트 완료(본문 조회 실패→재생성): {title} (page_id={existing_id})"

        patched = existing_html
        if vacation_map:
            patched = fill_schedule_table_vacations(patched, vacation_map)
        patched = update_week_range_in_schedule_table(patched, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
        patched = update_week_range_in_assignee_table(patched, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
        if status_chip_mode == "label_date":
            patched = apply_status_chip_colors(patched)
            patched = apply_bold_adjacent_status_chips(patched)
        client.update_page(existing_id, title=title, html_content=patched)
        if vacation_map:
            return f"기존 페이지 패치 완료(본문 보존, 일정 공유 갱신): {title} (page_id={existing_id})"
        return f"기존 페이지 패치 완료(본문 보존, 일정 이벤트 없음): {title} (page_id={existing_id})"

    space_key = _get_space_key(cfg)
    if not space_key:
        raise RuntimeError(f"config/{team_id}: confluence_space_key 또는 space_key 가 없습니다.")
    page = client.create_page(title=title, html_content=full_html, parent_id=target_folder_id, space_key=space_key)
    new_page_id = str(page.get("id") or "")

    layout = str(cfg.get("weekly_folder_layout") or "").strip().lower()
    if layout == "single" and new_page_id and latest_page_id:
        # flat 레이아웃: 신규 페이지가 항상 목록 맨 위(최신)에 오도록 직전 최신 페이지 앞으로 재배치.
        try:
            client.move_page_position(new_page_id, "before", latest_page_id)
        except Exception as exc:
            logger.warning("[%s] 신규 페이지 정렬 실패: %s", team_id, exc)

    return f"페이지 생성 완료: {title} (page_id={new_page_id})"


# ---------------------------------------------------------------------------
# PC2: from_template
# ---------------------------------------------------------------------------

def _create_from_template(team_id: str, cfg: dict) -> str:
    """템플릿 기반 주간회의 페이지 생성 (PC2)."""
    team_name = str(cfg.get("team_name") or team_id)
    root_id = _get_root_id(cfg)
    template_page_id = _get_template_id(cfg)

    if not root_id:
        raise RuntimeError(f"config/{team_id}: root_pages 또는 report_root_page_id 에서 폴더 ID를 추출할 수 없습니다.")
    if not template_page_id:
        raise RuntimeError(f"config/{team_id}: template_page_id 에서 페이지 ID를 추출할 수 없습니다.")

    calendar_id = str(cfg.get("calendar_id") or "")
    reference_date = _find_reference_date(calendar_id, team_name)
    logger.info("[%s] from_template 기준일: %s", team_id, reference_date.strftime("%Y-%m-%d"))

    client = ConfluenceClient()

    # 1) 템플릿 HTML
    tpl_page = client.get_page(template_page_id, expand="body.storage")
    html = ((tpl_page.get("body") or {}).get("storage") or {}).get("value") or ""
    if not html:
        raise RuntimeError(f"템플릿 페이지(ID={template_page_id}) body.storage 가 없습니다.")

    # 2) 이전 주간회의 내용
    prev_data: dict[str, Any] = {
        "page_id": None, "main_notices_html": "",
        "notice_vntg": "", "notice_center": "", "notice_team": "",
        "project_mgmt": "", "project_main": "", "project_common": "",
        "members_plans": [],
    }
    try:
        prev_data = get_previous_weekly_report_content(
            report_root_page_id=root_id,
            team_name=team_name,
            client=client,
            before_meeting_date=reference_date,
        )
        if prev_data.get("page_id"):
            logger.info("[%s] 이전 주간회의 내용 가져옴 (page_id=%s)", team_id, prev_data["page_id"])
        else:
            logger.info("[%s] 이전 주간회의 없음 — 공지·프로젝트 공백 처리", team_id)
    except Exception as e:
        logger.warning("[%s] 이전 주간회의 조회 실패: %s", team_id, e)

    # 3) {{NAME}} 행 팀원 수만큼 복사
    member_names = _get_member_names(cfg)
    plans_by_name: dict[str, dict[str, str]] = {}
    for m in (prev_data.get("members_plans") or []):
        n = normalize_member_name(m.get("name") or "")
        if n:
            plans_by_name[n] = {
                "this_week": m.get("this_week_html") or "",
                "next_week": m.get("next_week_html") or "",
            }
    if member_names:
        html = expand_name_placeholder_rows(html, member_names, plans_by_name)
    html = remove_schedule_table_vertical_space(html)

    # 4) 정적 섹션 라벨·팀명 교체
    for ph, label in (
        ("NOTICE", "주요 공지(회의 등)"),
        ("SCHEDULE", "일정 공유"),
        ("PROJECT", "주요 프로젝트 현황"),
    ):
        html = html.replace("{{" + ph + "}}", label)
    html = html.replace("{{TEAM}}", team_name)
    for ph, label in (
        ("PROJECT_MGMT", "프로젝트 관리"),
        ("PROJECT_MAIN", "주요 업무 추진 내용"),
        ("PROJECT_COMMON", "공통-주 업무 수행"),
    ):
        html = html.replace("{{" + ph + "}}", label)

    # 프로젝트 내용 (이전 주 데이터)
    for ph, pkey in (
        ("PROJECT_MGMT_CONTENT", "project_mgmt"),
        ("PROJECT_MAIN_CONTENT", "project_main"),
        ("PROJECT_COMMON_CONTENT", "project_common"),
    ):
        html = html.replace("{{" + ph + "}}", prev_data.get(pkey) or "")

    # 공지 플레이스홀더
    prev_notices = {
        "NOTICE_VNTG": prev_data.get("notice_vntg") or "",
        "NOTICE_CENTER": prev_data.get("notice_center") or "",
        "NOTICE_TEAM": prev_data.get("notice_team") or "",
    }
    if not any(prev_notices.values()):
        main_block = (prev_data.get("main_notices_html") or "").strip()
        if main_block and not is_empty_or_placeholder_content(html_to_plain_text_block(main_block)):
            prev_notices["NOTICE_VNTG"] = main_block
    for ph, val in prev_notices.items():
        html = html.replace("{{" + ph + "}}", val)

    # 5) 주차 날짜 교체
    week_phs = get_week_range_placeholders(reference_date)
    for ph, val in week_phs.items():
        html = html.replace("{{" + ph + "}}", val)
    html = update_week_range_in_schedule_table(html, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
    html = update_week_range_in_assignee_table(html, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])

    # 일정 공유 표 데이터 (출장/외근/재택/휴가) — 신규 생성·기존 패치 양쪽에서 재사용
    # {{SCHEDULE}} → "일정 공유" 교체 후라야 테이블을 찾을 수 있음
    # calendar_id(회의 캘린더)로 폴백하면 안 됨: 휴가 이벤트가 없어 표가 비게 된다.
    vacation_cal_id = str(cfg.get("vacation_calendar_id") or VACATION_CALENDAR_ID or "")
    vacation_map: dict[str, dict[str, str]] = {}
    if vacation_cal_id and member_names:
        logger.info("[%s] 휴가 캘린더: %s", team_id, vacation_cal_id)
        try:
            vacation_map = _build_vacation_map(
                member_names, vacation_cal_id, reference_date,
                team_members_cfg=cfg.get("team_members"),
            )
        except Exception as exc:
            logger.warning("[%s] 일정 공유 조회 실패: %s", team_id, exc)

    if vacation_map:
        html = fill_schedule_table_vacations(html, vacation_map)
        logger.info("[%s] 일정 공유 업데이트 완료: %s", team_id, list(vacation_map.keys()))
    else:
        logger.info("[%s] 일정 공유: 해당 주차 이벤트 없음", team_id)

    # 6) 잔여 플레이스홀더 제거
    html = re.sub(r"\{\{[A-Z0-9_]+\}\}", "", html)

    # 7) 대상 폴더 탐색/생성 (single 레이아웃이면 root 폴더 그대로)
    target_folder_id = _resolve_target_folder(client, cfg, root_id, team_name, reference_date)
    title = f"{reference_date.strftime('%Y-%m-%d')} {team_name} 주간회의"
    space_key = _get_space_key(cfg)
    if not space_key:
        raise RuntimeError(f"config/{team_id}: confluence_space_key 또는 space_key 가 없습니다.")

    # 8) 기존 페이지가 있으면 본문(사용자가 주중에 작성한 내용)은 보존하고
    #    일정 공유 표·주차 날짜·진도율 칩 색상만 패치. 없으면 템플릿으로 신규 생성.
    #    (매일 10시 스케줄러가 같은 reference_date 로 재실행돼도 작성 내용이 날아가지 않게 함)
    existing_id = _find_page_in_folder(client, target_folder_id, title)
    if existing_id:
        try:
            existing_page = client.get_page(existing_id, expand="body.storage")
            existing_html = ((existing_page.get("body") or {}).get("storage") or {}).get("value") or ""
        except Exception as e:
            logger.warning("[%s] 기존 페이지 본문 조회 실패: %s", team_id, e)
            existing_html = ""

        # 본문 조회 실패 시에만 전체 재생성으로 폴백 (정상적이면 기존 본문 보존)
        if not existing_html:
            client.update_page(existing_id, title=title, html_content=html)
            return f"기존 페이지 업데이트 완료(본문 조회 실패→재생성): {title} (page_id={existing_id})"

        patched = existing_html
        if vacation_map:
            patched = fill_schedule_table_vacations(patched, vacation_map)
        patched = update_week_range_in_schedule_table(patched, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
        patched = update_week_range_in_assignee_table(patched, week_phs["THIS_WEEK"], week_phs["NEXT_WEEK"])
        patched = apply_status_chip_colors(patched)
        client.update_page(existing_id, title=title, html_content=patched)
        return f"기존 페이지 패치 완료(본문 보존): {title} (page_id={existing_id})"

    page = client.create_page(title=title, html_content=html, parent_id=target_folder_id, space_key=space_key)
    return f"페이지 생성 완료: {title} (page_id={page.get('id', '')})"


# ---------------------------------------------------------------------------
# 공개 진입점
# ---------------------------------------------------------------------------

def run_weekly_page_job(team_id: str) -> str:
    """특정 팀 1개 처리."""
    from firestore.team_config import get_team_config, normalize_team_id

    team_id = normalize_team_id(team_id)
    cfg = get_team_config(team_id)
    if not cfg:
        raise RuntimeError(f"팀 설정을 찾을 수 없습니다: {team_id}")

    mode = str(cfg.get("weekly_page_mode") or "").strip()
    if not mode:
        # 신규 팀 추가 시 누락됐거나 구 문서 — MES2 와 동일하게 copy_latest 로 동작
        from firestore.team_config import DEFAULT_WEEKLY_PAGE_MODE

        mode = DEFAULT_WEEKLY_PAGE_MODE
        logger.warning(
            "config/%s 에 weekly_page_mode 없음 → 기본값 %s 사용",
            team_id,
            mode,
        )

    logger.info("weekly_page_job team=%s mode=%s", team_id, mode)
    if mode == "copy_latest":
        return _create_by_copy(team_id, cfg)
    return _create_from_template(team_id, cfg)


def run_weekly_page_jobs_all() -> str:
    """Firestore의 모든 팀을 순회 — weekly_page_mode 있는 팀만 처리."""
    from firestore.team_config import get_team_list, get_team_config, normalize_team_id

    teams = get_team_list()
    if not teams:
        return "등록된 팀이 없습니다."

    results = []
    for team in teams:
        tid = normalize_team_id(str(team.get("id") or ""))
        if not tid:
            continue
        try:
            cfg = get_team_config(tid)
            if not cfg:
                logger.warning("팀 설정 없음, 스킵: %s", tid)
                continue
            mode = str(cfg.get("weekly_page_mode") or "").strip()
            if not mode:
                from firestore.team_config import DEFAULT_WEEKLY_PAGE_MODE

                logger.info(
                    "weekly_page_mode 미설정 → 기본값 %s 로 처리: %s",
                    DEFAULT_WEEKLY_PAGE_MODE,
                    tid,
                )
            result = run_weekly_page_job(tid)
            results.append(f"[{tid}] {result}")
        except Exception as e:
            logger.exception("weekly_page_job 실패 [%s]", tid)
            results.append(f"[{tid}] 실패: {e}")

    return " | ".join(results) if results else "처리할 팀 없음 (weekly_page_mode 설정된 팀이 없음)"
