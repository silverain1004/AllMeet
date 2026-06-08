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

from api.confluence.client import ConfluenceClient
from api.confluence.folder import resolve_report_folders
from api.confluence.previous_report import (
    fetch_weekly_report_html,
    get_latest_weekly_report_page_id_for_team_root,
    get_previous_weekly_report_content,
)
from domains.weekly_meeting.page_html import (
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

# 원본 CATEGORY_MAP 의 AllMeet 버전 (vacation_fetcher.py와 동일 키 사용)
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


def _parse_vacation_event(summary: str) -> Optional[tuple[str, list[str], str]]:
    """'종류(이름)' 또는 '종류(이름1, 이름2)' 형식 파싱.

    원본 vacation_fetcher._parse_event_title 과 동일한 로직.
    예: '연차(영은)' → ('vacation', ['영은'], '연차')
    예: '출장(희민, 성훈)' → ('business_trip', ['희민', '성훈'], '출장')
    """
    if not summary:
        return None
    m = re.match(r"^(.+?)\(([^)]+)\)\s*$", summary.strip())
    if not m:
        return None
    kind = m.group(1).strip()
    names_str = m.group(2).strip()
    cat = _VACATION_CATEGORY_MAP.get(kind)
    if cat is None and " " in kind:
        cat = _VACATION_CATEGORY_MAP.get(kind.replace(" ", ""))
    if cat is None and (kind.startswith("오전 ") or kind.startswith("오후 ")):
        cat = _VACATION_CATEGORY_MAP.get(kind[3:].strip())
    if cat is None:
        return None
    names = [n.strip() for n in names_str.split(",") if n.strip()]
    return (cat, names, kind)


def _event_name_matches_member(event_name: str, member_name: str) -> bool:
    """이벤트 추출 이름이 팀원 이름과 일치하는지.

    '영은' vs '이영은' 처럼 given name이 full name의 suffix인 경우도 허용.
    """
    if not event_name or not member_name:
        return False
    return event_name in member_name or member_name in event_name


def _format_vacation_line(kind: str, start: str, member_name: str) -> str:
    """'MM/DD(요일) 이름(종류)' 포맷 — 원본 vacation_fetcher 출력 형식."""
    date_str = start[:10] if start else ""
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            date_part = f"{d.strftime('%m/%d')}({_WEEKDAY_KO[d.weekday()]})"
        except ValueError:
            date_part = date_str
    else:
        date_part = ""
    text = f"{member_name}({kind})"
    return f"{date_part} {text}" if date_part else text


def _build_vacation_map(
    member_names: list[str],
    calendar_id: str,
    reference_date: datetime,
) -> dict[str, dict[str, str]]:
    """reference_date 기준 이번주/다음주 일정 카테고리 맵 빌드.

    원본 vacation_fetcher.get_vacation_template_data 방식:
    q 없이 전체 이벤트 조회 → '종류(이름)' 파싱 → 팀원 매칭.
    캘린더 조회 실패 시 빈 dict 반환 (페이지 생성은 계속 진행).

    Returns: {
        "vacation":      {"this_week": "<ul><li>...</li></ul>", "next_week": "..."},
        "business_trip": {...},
        ...
    }
    """
    if not member_names or not calendar_id:
        return {}
    try:
        from domains.weekly_meeting.schedule_lookup import fetch_all_calendar_events
    except ImportError:
        return {}

    norm_members = [normalize_member_name(n) for n in member_names if normalize_member_name(n)]

    def _week_range(ref: datetime) -> tuple[str, str]:
        mon = ref - timedelta(days=ref.weekday())
        mon = mon.replace(hour=0, minute=0, second=0, microsecond=0)
        end = mon + timedelta(days=7)
        return mon.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT00:00:00Z")

    this_min, this_max = _week_range(reference_date)
    next_min, next_max = _week_range(reference_date + timedelta(weeks=1))

    acc: dict[str, dict[str, list[str]]] = {
        "business_trip": {"this_week": [], "next_week": []},
        "field_work":    {"this_week": [], "next_week": []},
        "remote":        {"this_week": [], "next_week": []},
        "vacation":      {"this_week": [], "next_week": []},
    }

    try:
        for week_key, t_min, t_max in (
            ("this_week", this_min, this_max),
            ("next_week", next_min, next_max),
        ):
            res = fetch_all_calendar_events(time_min=t_min, time_max=t_max, calendar_id=calendar_id)
            if not res.ok:
                logger.warning("[vacation] 캘린더 조회 실패 (%s): %s", week_key, res.error_kind)
                continue
            for event in res.events:
                summary = event.get("summary", "")
                parsed = _parse_vacation_event(summary)
                if parsed is None:
                    continue
                cat_key, event_names, kind = parsed
                if cat_key not in acc:
                    continue
                for event_name in event_names:
                    matched = next(
                        (m for m in norm_members if _event_name_matches_member(event_name, m)),
                        None,
                    )
                    if matched is None:
                        continue
                    line = _format_vacation_line(kind, event.get("start", ""), matched)
                    if line not in acc[cat_key][week_key]:
                        acc[cat_key][week_key].append(line)
    except Exception as exc:
        logger.warning("[vacation] 일정 조회 실패: %s", exc)

    def _to_ul(lines: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>" if lines else ""

    result: dict[str, dict[str, str]] = {}
    for cat_key, weeks in acc.items():
        this_ul = _to_ul(weeks["this_week"])
        next_ul = _to_ul(weeks["next_week"])
        if this_ul or next_ul:
            result[cat_key] = {"this_week": this_ul, "next_week": next_ul}
    return result


# ---------------------------------------------------------------------------
# 캘린더 기반 다음 회의일 추정
# ---------------------------------------------------------------------------

def _find_reference_date(calendar_id: str, team_name: str) -> datetime:
    """다음 주간회의 일자. 캘린더 조회 실패 시 오늘 +7일."""
    try:
        from api.calendar.events import list_events
        now = datetime.utcnow()
        time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
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
                    if d.date() >= local_now.date():
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
    # vacation_calendar_id 우선, 없으면 calendar_id 사용
    vacation_cal_id = str(cfg.get("vacation_calendar_id") or calendar_id or "")
    member_names = _get_member_names(cfg)
    if vacation_cal_id and member_names:
        logger.info("[%s] 휴가 캘린더: %s", team_id, vacation_cal_id)
        try:
            vacation_map = _build_vacation_map(member_names, vacation_cal_id, reference_date)
            if vacation_map:
                full_html = fill_schedule_table_vacations(full_html, vacation_map)
                logger.info("[%s] 일정 공유 업데이트 완료: %s", team_id, list(vacation_map.keys()))
            else:
                logger.info("[%s] 일정 공유: 해당 주차 이벤트 없음", team_id)
        except Exception as exc:
            logger.warning("[%s] 일정 공유 채우기 실패: %s", team_id, exc)

    quarter_id = _get_quarter_folder(client, root_id, team_name, reference_date)

    title = f"{reference_date.strftime('%Y-%m-%d')} {team_name} 주간회의"
    existing_id = _find_page_in_folder(client, quarter_id, title)
    if existing_id:
        return f"이미 존재 — 스킵 (page_id={existing_id}, title={title})"

    space_key = _get_space_key(cfg)
    if not space_key:
        raise RuntimeError(f"config/{team_id}: confluence_space_key 또는 space_key 가 없습니다.")
    page = client.create_page(title=title, html_content=full_html, parent_id=quarter_id, space_key=space_key)
    return f"페이지 생성 완료: {title} (page_id={page.get('id', '')})"


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

    # 일정 공유 표 — 캘린더에서 일정 데이터 채우기 (출장/외근/재택/휴가)
    # vacation_calendar_id 우선, 없으면 calendar_id 사용
    vacation_cal_id = str(cfg.get("vacation_calendar_id") or calendar_id or "")
    if vacation_cal_id and member_names:
        logger.info("[%s] 휴가 캘린더: %s", team_id, vacation_cal_id)
        try:
            vacation_map = _build_vacation_map(member_names, vacation_cal_id, reference_date)
            if vacation_map:
                html = fill_schedule_table_vacations(html, vacation_map)
                logger.info("[%s] 일정 공유 업데이트 완료: %s", team_id, list(vacation_map.keys()))
            else:
                logger.info("[%s] 일정 공유: 해당 주차 이벤트 없음", team_id)
        except Exception as exc:
            logger.warning("[%s] 일정 공유 채우기 실패: %s", team_id, exc)

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

    # 6) 잔여 플레이스홀더 제거
    html = re.sub(r"\{\{[A-Z0-9_]+\}\}", "", html)

    # 7) 분기 폴더 탐색/생성
    quarter_id = _get_quarter_folder(client, root_id, team_name, reference_date)
    title = f"{reference_date.strftime('%Y-%m-%d')} {team_name} 주간회의"
    space_key = _get_space_key(cfg)
    if not space_key:
        raise RuntimeError(f"config/{team_id}: confluence_space_key 또는 space_key 가 없습니다.")

    # 8) 기존 페이지 업데이트 / 없으면 신규 생성
    existing_id = _find_page_in_folder(client, quarter_id, title)
    if existing_id:
        client.update_page(existing_id, title=title, html_content=html)
        return f"기존 페이지 업데이트 완료: {title} (page_id={existing_id})"

    page = client.create_page(title=title, html_content=html, parent_id=quarter_id, space_key=space_key)
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
        raise RuntimeError(
            f"config/{team_id} 문서에 weekly_page_mode 필드가 없습니다. "
            f"'copy_latest' 또는 'from_template' 을 추가하세요."
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
                logger.info("weekly_page_mode 미설정, 스킵: %s", tid)
                continue
            result = run_weekly_page_job(tid)
            results.append(f"[{tid}] {result}")
        except Exception as e:
            logger.exception("weekly_page_job 실패 [%s]", tid)
            results.append(f"[{tid}] 실패: {e}")

    return " | ".join(results) if results else "처리할 팀 없음 (weekly_page_mode 설정된 팀이 없음)"
