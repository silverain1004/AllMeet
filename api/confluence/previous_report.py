"""이전 주간회의 내용 조회 — 페이지 본문 파싱 (공지/프로젝트/담당자 계획).

구 프로젝트 confluence/previous_report.py 포팅.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.confluence.client import ConfluenceClient
from api.confluence.folder import (
    get_latest_weekly_report_page_id_before_meeting_date,
    get_latest_weekly_report_page_id,
    resolve_report_folders,
)


def get_latest_weekly_report_page_id_for_team_root(
    client: ConfluenceClient,
    report_root_page_id: str,
    team_name: str,
    before_meeting_date: Optional[datetime] = None,
) -> Optional[str]:
    """
    팀의 report_root 아래 team_name 포함 주간회의 페이지 ID.

    before_meeting_date 있으면 그 날짜 이전 중 제목 날짜 최신 페이지.
    없으면 현재 분기 폴더에서 최신 페이지.
    """
    root_id = report_root_page_id
    if not root_id:
        return None

    def _title_date(title: str) -> Optional[datetime.date]:
        m = re.search(r"^\s*(\d{4}-\d{2}-\d{2})", title)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None

    if before_meeting_date is not None:
        try:
            root_folder = client.get_folder(root_id)
            space_id = str(root_folder.get("spaceId", ""))
            if space_id:
                page_id = get_latest_weekly_report_page_id_before_meeting_date(
                    client, root_id, space_id, team_name, before_meeting_date
                )
                if page_id:
                    return page_id
        except Exception:
            pass

        # Fallback: report_root 바로 아래 or 분기 폴더 아래 직접 탐색
        try:
            cutoff = before_meeting_date.date()
            children = client.get_folder_direct_children(root_id, limit=200)
            best_id: Optional[str] = None
            best_d = None
            for c in children:
                if c.get("type") != "page":
                    continue
                title = c.get("title") or ""
                if team_name not in title:
                    continue
                d = _title_date(title)
                if d is None or d >= cutoff:
                    continue
                if best_d is None or d > best_d:
                    best_d = d
                    best_id = str(c.get("id"))
            if best_id:
                return best_id

            # root 아래 분기 폴더(예: 2분기) 안에서 탐색
            best_id = None
            best_d = None
            for c in children:
                if c.get("type") != "folder":
                    continue
                if not re.search(r"^([1-4])\s*분기$", (c.get("title") or "").strip()):
                    continue
                for qc in client.get_folder_direct_children(str(c.get("id")), limit=200):
                    if qc.get("type") != "page":
                        continue
                    t = qc.get("title") or ""
                    if team_name not in t:
                        continue
                    d = _title_date(t)
                    if d is None or d >= cutoff:
                        continue
                    if best_d is None or d > best_d:
                        best_d = d
                        best_id = str(qc.get("id"))
            return best_id
        except Exception:
            return None

    try:
        _, quarter_id, _ = resolve_report_folders(
            client=client, root_id=root_id, team_name=team_name, create_if_missing=False
        )
    except Exception:
        return None
    if not quarter_id:
        try:
            return get_latest_weekly_report_page_id(client, root_id, title_contains=team_name)
        except Exception:
            return None
    return get_latest_weekly_report_page_id(client, quarter_id, title_contains=team_name)


def fetch_weekly_report_html(client: ConfluenceClient, page_id: str) -> str:
    """주간회의 페이지 body.storage HTML 반환."""
    page = client.get_page(str(page_id), expand="body.storage")
    return ((page.get("body") or {}).get("storage") or {}).get("value") or ""


def _strip_tag(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


NOTICE_ROW_LABELS: List[tuple] = [
    (("주관 VNTG", "주관", "VNTG"), "notice_vntg"),
    (("센터",), "notice_center"),
    (("제조PC2팀", "제조PC2", "제조"), "notice_team"),
]
PROJECT_ROW_LABELS: List[tuple] = [
    ("프로젝트관리", "project_mgmt"),
    ("주요업무추진내용", "project_main"),
    ("공통-주업무수행", "project_common"),
]
PROJECT_SECTION_LABELS: List[tuple] = [
    (("프로젝트관리", "프로젝트 관리"), "project_mgmt"),
    (("주요 업무 추진 내용", "주요업무추진내용", "주요 업무 추진"), "project_main"),
    (("공통-주 업무 수행", "공통-주업무수행", "공통-주 업무"), "project_common"),
]


def _match_label(first_text: str, label_spec: Any) -> bool:
    labels = label_spec if isinstance(label_spec, (list, tuple)) else (label_spec,)
    first_text = (first_text or "").strip()
    return any((str(L).strip() in first_text) or (first_text in str(L).strip()) for L in labels)


def _extract_table_rows_by_labels(block: str, label_key_pairs: List[tuple]) -> Dict[str, str]:
    result = {item[1]: "" for item in label_key_pairs}
    cell_pat = re.compile(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", re.IGNORECASE)
    row_pat = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.IGNORECASE)
    for table_m in re.finditer(r"<table[^>]*>([\s\S]*?)</table>", block, re.IGNORECASE | re.DOTALL):
        for row_html in row_pat.findall(table_m.group(1)):
            cells = cell_pat.findall(row_html)
            if len(cells) < 2:
                continue
            first_text = _strip_tag(cells[0]).strip()
            content = cells[-1].strip() if len(cells) >= 3 else cells[1].strip()
            for label_spec, key in label_key_pairs:
                if _match_label(first_text, label_spec):
                    if not result[key] or len(content) > len(result[key]):
                        result[key] = content
                    break
    return result


def _trim_project_section(chunk: str) -> str:
    if not chunk or not chunk.strip():
        return ""
    s = chunk.strip()
    first_end = s.find("</table>")
    if first_end != -1:
        before = s[:first_end]
        if len(before) < 200 and ("</th>" in before or "</tr>" in before):
            s = s[first_end + len("</table>"):].strip()
    last_table = s.rfind("<table")
    if last_table != -1:
        s = s[:last_table].strip()
    return s


def _extract_sections_by_labels(block: str, label_key_pairs: List[tuple]) -> Dict[str, str]:
    result = {item[1]: "" for item in label_key_pairs}
    entries: List[tuple] = []
    for label_spec, key in label_key_pairs:
        labels = label_spec if isinstance(label_spec, (list, tuple)) else (label_spec,)
        first_pos, first_len = -1, 0
        for L in labels:
            L = str(L).strip()
            pos = block.find(L)
            if pos != -1 and (first_pos == -1 or pos < first_pos):
                first_pos, first_len = pos, len(L)
        if first_pos != -1:
            entries.append((first_pos, first_pos + first_len, key))
    entries.sort(key=lambda x: x[0])
    for i, (_, content_start, key) in enumerate(entries):
        content_end = entries[i + 1][0] if i + 1 < len(entries) else len(block)
        chunk = block[content_start:content_end].strip()
        if chunk:
            result[key] = _trim_project_section(chunk)
    return result


def parse_weekly_report_sections(html: str) -> Dict[str, Any]:
    """주간회의 Storage HTML 파싱 → 공지/프로젝트/담당자 계획."""
    out: Dict[str, Any] = {
        "main_notices_html": "",
        "project_status_html": "",
        "notice_vntg": "",
        "notice_center": "",
        "notice_team": "",
        "project_mgmt": "",
        "project_main": "",
        "project_common": "",
        "members_plans": [],
    }
    if not html:
        return out

    # 1) 주요 공지
    main_start = html.find("주요 공지(회의 등)")
    if main_start == -1:
        main_start = html.find("주요 공지")
    if main_start != -1:
        main_end = len(html)
        for marker in ("일정 공유", "일정공유"):
            pos = html.find(marker, main_start)
            if pos != -1 and pos < main_end:
                main_end = pos
        cell_with_heading = html.rfind("<ac:layout-cell", 0, main_start)
        if cell_with_heading != -1:
            prev_end = html.rfind("</ac:layout-cell>", 0, cell_with_heading)
            prev_start = html.rfind("<ac:layout-cell", 0, prev_end) if prev_end != -1 else -1
            block_start = prev_start if prev_start != -1 else cell_with_heading
        else:
            block_start = html.rfind("<", 0, main_start)
        if block_start == -1:
            block_start = main_start
        notice_block = html[block_start:main_end].strip()
        out["main_notices_html"] = notice_block
        for key, val in _extract_table_rows_by_labels(notice_block, NOTICE_ROW_LABELS).items():
            out[key] = val

    # 2) 주요 프로젝트 현황
    schedule_pos = html.find("일정 공유")
    if schedule_pos == -1:
        schedule_pos = html.find("일정공유")
    search_after = schedule_pos if schedule_pos != -1 else 0
    proj_start = html.find("주요 프로젝트 현황", search_after)
    if proj_start != -1:
        dan_pos = html.find("담당자", proj_start)
        proj_end = dan_pos if dan_pos != -1 else len(html)
        cell_with_heading = html.rfind("<ac:layout-cell", 0, proj_start)
        block_start = cell_with_heading if cell_with_heading != -1 else html.rfind("<", 0, proj_start)
        if block_start == -1:
            block_start = proj_start
        project_block = html[block_start:proj_end].strip()
        out["project_status_html"] = project_block
        parsed = _extract_table_rows_by_labels(project_block, PROJECT_ROW_LABELS)
        if any(parsed.values()):
            for key, val in parsed.items():
                out[key] = val
        else:
            for key, val in _extract_sections_by_labels(project_block, PROJECT_SECTION_LABELS).items():
                out[key] = val

    # 3) 담당자 표
    search_start = proj_start if proj_start != -1 else 0
    table_start = html.find("<table", search_start)
    while table_start != -1:
        depth = 1
        p = html.find(">", table_start) + 1
        table_end = -1
        while p < len(html):
            next_table = html.find("<table", p)
            next_close = html.find("</table>", p)
            if next_close == -1:
                break
            if next_table != -1 and next_table < next_close:
                depth += 1
                p = next_table + 6
            else:
                depth -= 1
                if depth == 0:
                    table_end = next_close + len("</table>")
                    break
                p = next_close + 8
        if table_end == -1:
            break
        table_block = html[table_start:table_end]
        if "담당자" not in table_block or "금주" not in table_block or "차주" not in table_block:
            table_start = html.find("<table", table_end)
            continue
        row_pat = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.IGNORECASE)
        cell_pat = re.compile(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", re.IGNORECASE)
        rows = row_pat.findall(table_block)
        if len(rows) < 2:
            table_start = html.find("<table", table_end)
            continue
        if "담당자" not in _strip_tag((cell_pat.findall(rows[0]) or [""])[0]):
            table_start = html.find("<table", table_end)
            continue
        for row_html in rows[1:]:
            cells = cell_pat.findall(row_html)
            if len(cells) < 3:
                continue
            name = _strip_tag(cells[0]).strip()
            if not name or name == "{{NAME}}":
                continue
            out["members_plans"].append({
                "name": name,
                "this_week_html": cells[1].strip(),
                "next_week_html": cells[2].strip(),
            })
        break

    return out


def get_previous_weekly_report_content(
    report_root_page_id: str,
    team_name: str,
    client: Optional[ConfluenceClient] = None,
    before_meeting_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """이전 주간회의에서 공지/프로젝트/담당자 계획 추출."""
    if client is None:
        client = ConfluenceClient()
    page_id = get_latest_weekly_report_page_id_for_team_root(
        client, report_root_page_id, team_name, before_meeting_date=before_meeting_date
    )
    result: Dict[str, Any] = {
        "page_id": page_id,
        "main_notices_html": "",
        "project_status_html": "",
        "notice_vntg": "",
        "notice_center": "",
        "notice_team": "",
        "project_mgmt": "",
        "project_main": "",
        "project_common": "",
        "members_plans": [],
    }
    if not page_id:
        return result
    html = fetch_weekly_report_html(client, page_id)
    parsed = parse_weekly_report_sections(html)
    result["main_notices_html"] = parsed["main_notices_html"]
    result["project_status_html"] = parsed["project_status_html"]
    for key in ("notice_vntg", "notice_center", "notice_team", "project_mgmt", "project_main", "project_common"):
        result[key] = parsed.get(key, "")
    result["members_plans"] = parsed["members_plans"]
    return result
