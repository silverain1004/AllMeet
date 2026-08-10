"""Confluence 주간회의 페이지 HTML 조작 유틸.

구 프로젝트 tests/create_confluence_page_from_template.py 의 순수 함수들 포팅.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

_NAME_PLACEHOLDER = "{{NAME}}"

_HEADER_DATE_RANGE = re.compile(
    r"\((?:"
    r"\d{1,2}/\d{1,2}\s*~\s*\d{1,2}/\d{1,2}|"
    r"\d{1,2}/\d{1,2}\s*-\s*\d{1,2}/\d{1,2}"
    r")\)"
)


# ---------------------------------------------------------------------------
# 주차 날짜
# ---------------------------------------------------------------------------

def get_week_range_placeholders(reference: Optional[datetime] = None) -> dict[str, str]:
    """금주(월~금), 차주(월~금) 기간 문자열 반환.

    Returns:
        {"THIS_WEEK": "MM/DD~MM/DD", "NEXT_WEEK": "MM/DD~MM/DD"}
    """
    base = reference or datetime.now()
    this_monday = base - timedelta(days=base.weekday())
    this_friday = this_monday + timedelta(days=4)
    next_monday = this_monday + timedelta(days=7)
    next_friday = next_monday + timedelta(days=4)
    return {
        "THIS_WEEK": f"{this_monday.strftime('%m/%d')}~{this_friday.strftime('%m/%d')}",
        "NEXT_WEEK": f"{next_monday.strftime('%m/%d')}~{next_friday.strftime('%m/%d')}",
    }


# ---------------------------------------------------------------------------
# 날짜 헤더 교체
# ---------------------------------------------------------------------------

def update_week_range_in_schedule_table(html: str, this_week: str, next_week: str) -> str:
    """'일정 공유' 표의 이번주/다음주 헤더 날짜 교체."""
    if not html or not this_week or not next_week:
        return html
    idx = html.find("일정 공유")
    if idx == -1:
        idx = html.find("일정공유")
    if idx == -1:
        return html
    table_start = html.find("<table", idx)
    if table_start == -1:
        return html
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        return html
    table_end += len("</table>")
    block = html[table_start:table_end]
    matches = list(_HEADER_DATE_RANGE.finditer(block))
    if len(matches) >= 2:
        block = block[:matches[1].start()] + f"({next_week})" + block[matches[1].end():]
        block = block[:matches[0].start()] + f"({this_week})" + block[matches[0].end():]
        return html[:table_start] + block + html[table_end:]
    return html


def update_week_range_in_assignee_table(html: str, this_week: str, next_week: str) -> str:
    """'담당자' 표의 금주/차주 헤더 날짜 교체."""
    if not html or not this_week or not next_week:
        return html
    idx = html.find("금주 계획 및 실적")
    if idx == -1:
        idx = html.find("담당자")
    if idx == -1:
        return html
    table_start = html.rfind("<table", 0, idx)
    if table_start == -1:
        return html
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        return html
    table_end += len("</table>")
    block = html[table_start:table_end]
    matches = list(_HEADER_DATE_RANGE.finditer(block))
    if len(matches) >= 2:
        block = block[:matches[1].start()] + f"({next_week})" + block[matches[1].end():]
        block = block[:matches[0].start()] + f"({this_week})" + block[matches[0].end():]
        return html[:table_start] + block + html[table_end:]
    return html


# ---------------------------------------------------------------------------
# {{NAME}} 행 확장
# ---------------------------------------------------------------------------

def _find_name_placeholder_row(html: str):
    """{{NAME}} 포함 <tr>...</tr> 의 (시작, 끝, 행HTML). 없으면 (None, None, '')."""
    pos = html.find(_NAME_PLACEHOLDER)
    if pos == -1:
        return None, None, ""
    tr_start = html.rfind("<tr", 0, pos)
    if tr_start == -1:
        return None, None, ""
    tr_end = html.find("</tr>", pos) + len("</tr>")
    return tr_start, tr_end, html[tr_start:tr_end]


def _make_data_row(template_row: str, member_name: str, this_week_html: Optional[str] = None, next_week_html: Optional[str] = None) -> str:
    """{{NAME}} 행 1개를 팀원용으로 채움."""
    row = template_row.replace(_NAME_PLACEHOLDER, member_name)
    first_td = re.compile(r"<td([^>]*)>(.*?)</td>", re.DOTALL | re.IGNORECASE)
    row = first_td.sub(
        lambda m: "<td" + m.group(1) + "><p><strong>" + member_name + "</strong></p></td>",
        row,
        count=1,
    )
    if this_week_html is not None or next_week_html is not None:
        td_list = list(first_td.finditer(row))
        if len(td_list) >= 3:
            second, third = td_list[1], td_list[2]
            parts = []
            if this_week_html is not None:
                parts.append((second.start(2), second.end(2), this_week_html))
            if next_week_html is not None:
                parts.append((third.start(2), third.end(2), next_week_html))
            for start, end, replacement in reversed(parts):
                row = row[:start] + replacement + row[end:]
    return row


def expand_name_placeholder_rows(
    html: str,
    member_names: list[str],
    plans_by_name: dict[str, dict[str, str]] | None = None,
) -> str:
    """{{NAME}} 템플릿 행을 팀원 수만큼 복사해 이름·이전 주 계획 채움."""
    tr_start, tr_end, row_template = _find_name_placeholder_row(html)
    if tr_start is None or not row_template:
        return html
    plans = plans_by_name or {}

    def _row(name: str) -> str:
        p = plans.get(normalize_member_name(name), {})
        tw = (p.get("this_week") or "").strip()
        nw = (p.get("next_week") or "").strip()
        return _make_data_row(
            row_template,
            name,
            this_week_html=apply_status_chip_colors(tw) if tw else None,
            next_week_html=apply_status_chip_colors(nw) if nw else None,
        )

    new_rows = "".join(_row(n) for n in member_names)
    return html[:tr_start] + new_rows + html[tr_end:]


# ---------------------------------------------------------------------------
# 진도율 칩 색상
# ---------------------------------------------------------------------------

# 상태(날짜) 형식 (ERP2) — 상태 텍스트 기준 색상. 띄어쓰기 차이는 정규화 후 비교.
_STATUS_LABEL_COLOURS = {
    "대기": "Grey",
    "보류": "Grey",
    "진행중": "Yellow",
    "완료": "Green",
    "완료예정": "Green",
}

# 날짜 한 조각: m/d, d 대신 예정을 뜻하는 'E'도 허용 (예: 8/6, 8/E, 08/04)
_DATE_UNIT = r"\d{1,2}/(?:\d{1,2}|E)"
# 범위 구분자: '~' 뿐 아니라 '-' 로 쓰는 경우도 실데이터에 존재 (예: '7/27-8/24')
_DATE_SEP = r"[~-]"
# 날짜 칸 전체: m/d, ~m/d, m/d~, m/d~m/d(또는 '-' 구분), 날짜 미정('-'/빈값)도 허용
_DATE_TOKEN_RE = re.compile(
    rf"^(?:{_DATE_UNIT}{_DATE_SEP}{_DATE_UNIT}|{_DATE_UNIT}{_DATE_SEP}|{_DATE_SEP}{_DATE_UNIT}|{_DATE_UNIT}|-|)$"
)
_LABEL_DATE_TITLE_RE = re.compile(r"^(?P<label>[^()]+?)\s*\((?P<date>[^()]*)\)\s*$")


def _status_macro_colour(title: str) -> Optional[str]:
    """status 매크로 title 로 colour 결정.

    1) '상태(날짜)' 형식(예: '완료(8/6)', '진행중(~8/13)', '완료예정(8/E)')이면
       상태 텍스트로 결정: 대기·보류→Grey, 진행중→Yellow, 완료·완료예정→Green.
       상태 텍스트의 띄어쓰기는 무시(정규화)하지만, 인식되지 않는 텍스트거나 날짜 칸이
       날짜 형식이 아니면 색을 바꾸지 않음(None) — 오분류보다 미변경이 안전.
    2) 괄호 형식이 아니면 기존 %(0→Grey,1~99→Yellow,100→Green) 로직으로 폴백.
    """
    t = (title or "").strip()
    m = _LABEL_DATE_TITLE_RE.match(t)
    if m:
        label = re.sub(r"\s+", "", m.group("label"))
        date_part = m.group("date").strip()
        if label in _STATUS_LABEL_COLOURS and _DATE_TOKEN_RE.match(date_part):
            return _STATUS_LABEL_COLOURS[label]
        return None
    if "100%" in t or t in ("100", "100%"):
        return "Green"
    if not t or t in ("0%", "-%", "0", "-") or t.startswith("-"):
        return "Grey"
    return "Yellow"


def apply_status_chip_colors(html: str) -> str:
    """Confluence status 매크로 title 기준 colour 자동 지정 (판정 기준은 _status_macro_colour 참고)."""
    if not html or 'ac:name="status"' not in html:
        return html

    pattern = re.compile(
        r'<ac:structured-macro\s+ac:name="status"[^>]*>([\s\S]*?)</ac:structured-macro>',
        re.IGNORECASE,
    )

    def _replace(m):
        inner = m.group(1)
        title_m = re.search(r'<ac:parameter\s+ac:name="title"[^>]*>([\s\S]*?)</ac:parameter>', inner, re.IGNORECASE)
        title = re.sub(r"<[^>]+>", "", title_m.group(1) if title_m else "").strip()
        colour = _status_macro_colour(title)
        if colour is None:
            return m.group(0)
        colour_param = f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        if re.search(r'<ac:parameter\s+ac:name="colour"', inner, re.IGNORECASE):
            inner = re.sub(
                r'<ac:parameter\s+ac:name="colour"[^>]*>[^<]*</ac:parameter>',
                colour_param, inner, count=1, flags=re.IGNORECASE,
            )
        else:
            inner = inner.rstrip() + colour_param
        return '<ac:structured-macro ac:name="status">' + inner + "</ac:structured-macro>"

    return pattern.sub(_replace, html)


# ---------------------------------------------------------------------------
# 볼드 제목 옆 일반 텍스트 → 실제 Status 칩으로 변환 (ERP2)
# ---------------------------------------------------------------------------

# '(' ~ ')' 사이에 <span>/<strong> 같은 인라인 태그가 섞여 있어도 매칭 (짧은 주석용, 폭주 방지로 길이 제한)
_PAREN_WITH_INLINE_TAGS_RE = re.compile(r"\((?:[^()<]|<[^>]+>){1,200}?\)")


def _is_bold_adjacent(html: str, pos: int, window: int = 400) -> bool:
    """pos 시작 지점이 아직 안 닫힌 <strong> 안(볼드체 제목 뒤)인지 확인."""
    start = max(0, pos - window)
    segment = html[start:pos]
    last_open = segment.rfind("<strong")
    if last_open == -1:
        return False
    return not re.search(r"</strong\s*>", segment[last_open:], re.IGNORECASE)


def _split_label_and_date(inner_plain: str) -> Optional[tuple[str, str]]:
    """'(완료 예정, 8/13)' / '(8/13, 완료 예정)' / '(완료)' → (정규화된 라벨, 날짜부분).
    5개 키워드 중 정확히 하나와 일치할 때만 반환, 아니면 None(오탐 방지)."""
    body = inner_plain.strip()
    if not (body.startswith("(") and body.endswith(")")):
        return None
    body = body[1:-1]
    parts = body.split(",")
    if len(parts) == 1:
        label = re.sub(r"\s+", "", parts[0])
        if label in _STATUS_LABEL_COLOURS:
            return label, ""
        return None
    if len(parts) == 2:
        a = re.sub(r"\s+", "", parts[0])
        b = re.sub(r"\s+", "", parts[1])
        if b in _STATUS_LABEL_COLOURS:
            return b, parts[0].strip()
        if a in _STATUS_LABEL_COLOURS:
            return a, parts[1].strip()
    return None


def apply_bold_adjacent_status_chips(html: str) -> str:
    """볼드체 제목 옆에 '(날짜, 상태)'/'(상태, 날짜)'/'(상태)' 형식 일반 텍스트가 붙어 있으면
    실제 Status 매크로(칩)로 바꿔치기. 볼드체에 붙어있지 않거나 5개 키워드와 정확히
    일치하지 않으면 건드리지 않음(오탐 방지 — 제목 자체의 괄호, 예: '제조원가분석(견적용)' 등은 무시)."""
    if not html:
        return html

    def _replace(m: re.Match) -> str:
        raw = m.group(0)
        if not _is_bold_adjacent(html, m.start()):
            return raw
        inner_plain = re.sub(r"<[^>]+>", "", raw)
        parsed = _split_label_and_date(inner_plain)
        if parsed is None:
            return raw
        label, date_part = parsed
        colour = _STATUS_LABEL_COLOURS[label]
        title = f"{label}({date_part})" if date_part else label
        macro = (
            '<ac:structured-macro ac:name="status">'
            f'<ac:parameter ac:name="title">{title}</ac:parameter>'
            f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
            "</ac:structured-macro>"
        )
        # 매칭 구간 안의 <strong>/</strong> 개수가 안 맞으면(볼드 경계가 구간 중간에서
        # 열리거나 닫히는 실데이터 패턴) 구간 밖의 짝을 잃게 되므로 균형을 다시 맞춰줌:
        # 닫힘이 더 많으면(구간 밖에서 열린 것을 구간 안에서 닫음) 매크로 앞에 </strong> 보강,
        # 열림이 더 많으면(구간 안에서 열려 구간 밖에서 닫히는 것) 매크로 뒤에 <strong> 보강.
        opens = len(re.findall(r"<strong\s*>", raw, re.IGNORECASE))
        closes = len(re.findall(r"</strong\s*>", raw, re.IGNORECASE))
        net = opens - closes
        if net < 0:
            return "</strong>" * (-net) + macro
        if net > 0:
            return macro + "<strong>" * net
        return macro

    return _PAREN_WITH_INLINE_TAGS_RE.sub(_replace, html)

# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------

def normalize_member_name(name: str) -> str:
    """'김경원 (팀장)' → '김경원' — 괄호 이전 이름만 반환."""
    s = (name or "").strip()
    return s[:s.index("(")].strip() if "(" in s else s


def html_to_plain_text_block(html: str) -> str:
    """HTML 태그 제거 후 <p>...</p> 1블록 텍스트로 변환."""
    if not html or not html.strip():
        return ""
    s = re.sub(r"</?ac:[^>]*>", "", html, flags=re.IGNORECASE)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</tr>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&nbsp;", " ", s, flags=re.IGNORECASE)
    lines = [ln.strip() for ln in s.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    return "<p>" + "<br/>".join(lines) + "</p>"


def is_empty_or_placeholder_content(html: str) -> bool:
    """내용이 비어 있거나 '내용' 플레이스홀더뿐이면 True."""
    if not html or not html.strip():
        return True
    text = re.sub(r"<[^>]+>", "", html).strip()
    return not text or text == "내용"


# 일정 공유 표의 행 레이블 → 카테고리 키 매핑
# 원본 스크립트(create_confluence_page_from_template.py)의 _SCHEDULE_ROW_LABEL_KEYS와 동일
_SCHEDULE_ROW_LABELS = (
    ("출장", "business_trip"),
    ("외근", "field_work"),
    ("재택", "remote"),
    ("휴가", "vacation"),
)


def fill_schedule_table_vacations(
    html: str,
    category_map: dict[str, dict[str, str]],
) -> str:
    """'일정 공유' 표의 카테고리 행(출장/외근/재택/휴가)에 이번주/다음주 데이터를 채운다.

    원본의 _update_vacation_in_schedule_table 과 동일한 구조.
    category_map: {
        "business_trip": {"this_week": "<ul><li>...</li></ul>", "next_week": "..."},
        "remote":        {...},
        "vacation":      {...},
    }
    """
    if not category_map:
        return html
    idx = html.find("일정 공유")
    if idx == -1:
        idx = html.find("일정공유")
    if idx == -1:
        return html
    table_start = html.find("<table", idx)
    if table_start == -1:
        return html
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        return html
    table_end += len("</table>")
    block = html[table_start:table_end]

    row_pat = re.compile(r"(<tr[^>]*>)([\s\S]*?)(</tr>)", re.IGNORECASE)
    td_pat = re.compile(r"(<t[dh][^>]*>)([\s\S]*?)(</t[dh]>)", re.IGNORECASE)

    def _replace_row(m: re.Match) -> str:
        open_tr, inner, close_tr = m.group(1), m.group(2), m.group(3)
        cells = list(td_pat.finditer(inner))
        if len(cells) < 3:
            return m.group(0)
        first_text = re.sub(r"<[^>]+>", "", cells[0].group(2)).strip()
        category_key = None
        for label, key in _SCHEDULE_ROW_LABELS:
            if label in first_text:
                category_key = key
                break
        if category_key is None or category_key not in category_map:
            return m.group(0)
        data = category_map[category_key]
        new_inner = inner
        next_html = data.get("next_week") or ""
        this_html = data.get("this_week") or ""
        # 끝에서부터 교체해야 앞 셀 오프셋이 유지됨
        if next_html:
            c3 = cells[2]
            new_inner = new_inner[: c3.start(2)] + next_html + new_inner[c3.end(2) :]
        if this_html:
            c2 = cells[1]
            new_inner = new_inner[: c2.start(2)] + this_html + new_inner[c2.end(2) :]
        return open_tr + new_inner + close_tr

    new_block = row_pat.sub(_replace_row, block)
    return html[:table_start] + new_block + html[table_end:]


def remove_schedule_table_vertical_space(html: str) -> str:
    """'일정 공유' 표의 셀 내 위·아래 빈 줄 제거."""
    idx = html.find("일정 공유")
    if idx == -1:
        idx = html.find("일정공유")
    if idx == -1:
        return html
    table_start = html.find("<table", idx)
    if table_start == -1:
        return html
    table_end = html.find("</table>", table_start) + len("</table>")
    block = html[table_start:table_end]

    def _strip_cell(m):
        open_tag, content, close_tag = m.group(1), m.group(2), m.group(3)
        content = content.strip()
        empty_p = r"<p[^>]*>\s*(?:<br\s*/?\s*>)?\s*</p>"
        content = re.sub(r"^\s*(?:" + empty_p + r"\s*)+", "", content)
        content = re.sub(r"(?:\s*" + empty_p + r")+\s*$", "", content)
        return open_tag + content + close_tag

    block = re.sub(r"(<t[dh][^>]*>)([\s\S]*?)(</t[dh]>)", _strip_cell, block, flags=re.IGNORECASE | re.DOTALL)
    return html[:table_start] + block + html[table_end:]
