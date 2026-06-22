"""주간보고 초안 → Confluence 담당자 표 삽입.

사용자가 카드의 "Confluence에 초안 그대로 삽입" 버튼을 클릭하면 호출됨.
Firestore weekly_drafts/{email} 에서 초안을 로드해 이번 주 주간회의 페이지의
담당자 표 '금주 계획 및 실적' 셀에 덮어씀.
"""
from __future__ import annotations

import html as _html
import logging
import re
from typing import Any

from api.confluence.client import ConfluenceClient
from api.confluence.previous_report import get_latest_weekly_report_page_id_for_team_root
from domains.weekly_report.cards import format_deadline_badge, normalize_deadline
from firestore.team_config import find_team_by_email, get_team_config, get_team_from_index

logger = logging.getLogger(__name__)


def handle_insert_to_confluence(*, user_email: str, draft_ref_id: str) -> dict[str, Any]:
    """CARD_CLICKED wr_insert_to_confluence 진입점 → 동기 응답."""
    # 1. Firestore 에서 초안 로드
    doc_data = _load_weekly_draft(draft_ref_id)
    if not doc_data:
        return {"text": "초안 데이터를 찾지 못했어요. 주간보고초안을 다시 요청해 주세요."}

    draft = doc_data.get("draft")
    if not draft:
        return {
            "text": "초안 내용이 없어요. Vertex 분석 결과가 없는 경우 삽입할 수 없습니다."
        }

    # 2. 팀 조회
    team_id = find_team_by_email(user_email)
    if not team_id:
        return {"text": "소속 팀을 찾지 못했어요. 팀 설정에서 이메일을 확인해 주세요."}

    team_index = get_team_from_index(team_id) or {}
    team_name = str(team_index.get("name") or team_id)
    report_root_raw = str(team_index.get("report_root_page_id") or "").strip()
    report_root_id = _extract_numeric_id(report_root_raw)

    if not report_root_id:
        return {"text": f"{team_name}의 report_root_page_id 가 설정되지 않았어요."}

    # 3. 팀 멤버 이름 조회 (email → name)
    team_cfg = get_team_config(team_id) or {}
    member_name = _member_name_by_email(team_cfg, user_email)
    if not member_name:
        return {
            "text": (
                "팀 설정에서 이메일을 찾지 못했어요. "
                "team_members 배열에 본인 이메일이 등록되어 있는지 확인해 주세요."
            )
        }

    # 4. 최근 주간회의 페이지 ID 조회
    client = ConfluenceClient()
    try:
        page_id = get_latest_weekly_report_page_id_for_team_root(
            client, report_root_id, team_name
        )
    except Exception as e:
        logger.warning("페이지 ID 조회 실패: %s", e)
        page_id = None

    if not page_id:
        return {
            "text": (
                "이번 주 주간회의 페이지를 찾지 못했어요. "
                "페이지가 아직 생성되지 않았을 수 있습니다 (목요일 이후 시도해 주세요)."
            )
        }

    # 5. 페이지 HTML 로드
    try:
        page_html = client.get_page_storage(page_id)
        page_info = client.get_page(page_id, expand="version,title")
        page_title = page_info.get("title") or page_id
    except Exception as e:
        logger.warning("페이지 로드 실패 page_id=%s: %s", page_id, e)
        return {"text": f"페이지 로드에 실패했어요 (page_id={page_id})."}

    # 6. 초안 → Confluence Storage Format HTML
    new_this_week_html = _draft_to_confluence_html(draft)

    # 7. 담당자 표 해당 멤버 금주 셀 업데이트
    updated_html = _update_member_this_week(page_html, member_name, new_this_week_html)
    if updated_html == page_html:
        return {
            "text": (
                f"'{member_name}' 행을 찾지 못했어요. "
                f"페이지 제목: {page_title}\n"
                "담당자 표에 이름이 등록되어 있는지 확인해 주세요."
            )
        }

    # 8. 페이지 저장
    try:
        client.update_page(page_id, html_content=updated_html)
    except Exception as e:
        logger.warning("페이지 업데이트 실패 page_id=%s: %s", page_id, e)
        return {"text": f"페이지 저장에 실패했어요: {e}"}

    logger.info(
        "confluence_insert 완료 user_email=%s member_name=%s page_id=%s",
        user_email,
        member_name,
        page_id,
    )
    page_url = f"{client._base_url}/wiki/spaces/{_space_key(team_index)}/pages/{page_id}"
    return {
        "cardsV2": [
            {
                "cardId": "wr_insert_result",
                "card": {
                    "sections": [
                        {
                            "widgets": [
                                {
                                    "textParagraph": {
                                        "text": (
                                            f"✅ <b>{member_name}</b>님의 금주 계획을 "
                                            f"Confluence에 추가했어요!<br>"
                                            f"페이지: {_xe(page_title)}"
                                        )
                                    }
                                },
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {
                                                "text": "페이지 열기",
                                                "onClick": {"openLink": {"url": page_url}},
                                            }
                                        ]
                                    }
                                },
                            ]
                        }
                    ]
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _load_weekly_draft(draft_ref_id: str) -> dict | None:
    """Firestore weekly_drafts/{draft_ref_id} 로드."""
    try:
        from firestore import get_client

        db = get_client()
        snap = db.collection("weekly_drafts").document(draft_ref_id).get()
        if not snap.exists:
            return None
        return snap.to_dict()
    except Exception as e:
        logger.warning("weekly_draft 로드 실패 ref=%s: %s", draft_ref_id, e)
        return None


def _extract_numeric_id(url_or_id: str) -> str:
    """URL 또는 순수 숫자 ID → 숫자 ID 추출.

    예: 'https://.../folder/2275213400?...' → '2275213400'
    """
    url_or_id = (url_or_id or "").strip()
    m = re.search(r"/(?:folder|pages?)(?:view)?/(\d+)", url_or_id)
    if m:
        return m.group(1)
    if url_or_id.isdigit():
        return url_or_id
    return ""


def _member_name_by_email(team_cfg: dict, user_email: str) -> str | None:
    """팀 설정 team_members[] 에서 이메일로 이름 조회."""
    needle = (user_email or "").strip().lower()
    for m in team_cfg.get("team_members") or []:
        if not isinstance(m, dict):
            continue
        if str(m.get("email") or "").strip().lower() == needle:
            name = str(m.get("name") or "").strip()
            return name or None
    return None


def _draft_to_confluence_html(draft: dict) -> str:
    """draft → Confluence Storage Format HTML (담당자 표 금주 셀용).

    진행률은 Confluence Status 매크로(색칠된 네모 배지)로 렌더링.
    deadline 이 있으면 배지 제목에 함께 표기 — 예) '-% 06/26', '100% 06/E'.

    구조:
        <p><strong>[프로젝트]</strong></p>
        <ul>
          <li>task명 {status_macro: -% 06/26}
            <ul><li>detail1</li></ul>
          </li>
        </ul>
    """
    parts: list[str] = []
    for header, key in (("프로젝트", "projects"), ("운영지원", "operations")):
        items = draft.get(key) or []
        if not items:
            continue
        parts.append(f"<p><strong>{header}</strong></p>")
        li_items: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            task = str(it.get("task") or "").strip()
            if not task:
                continue
            deadline = normalize_deadline(str(it.get("deadline") or ""))
            details = [str(d).strip() for d in (it.get("details") or []) if str(d or "").strip()]

            # task 끝의 진행률 분리 → Status 매크로 (마감기한은 배지 안에 함께: '-% 06/26')
            task_text, status_macro = _split_task_and_status(task, deadline)
            task_html = f"{_xe(task_text)} {status_macro}"

            if details:
                sub = "".join(f"<li>{_xe(d)}</li>" for d in details)
                li_items.append(f"<li>{task_html}<ul>{sub}</ul></li>")
            else:
                li_items.append(f"<li>{task_html}</li>")
        if li_items:
            parts.append("<ul>" + "".join(li_items) + "</ul>")
    return "".join(parts)


_PROGRESS_RE = re.compile(r"\s+(-%|\d{1,3}%)\s*$")


def _split_task_and_status(task: str, deadline: str = "") -> tuple[str, str]:
    """task 문자열에서 진행률을 분리해 (task_text, status_macro_html) 반환.

    진행률 없으면 status_macro 는 Grey '-%' 배지.
    deadline 이 있으면 배지 제목에 함께 표기 — 예) '-% 06/26', '100% 06/E'.
    """
    m = _PROGRESS_RE.search(task)
    if m:
        prog = m.group(1).strip()
        task_text = task[: m.start()].rstrip()
    else:
        prog = "-%"
        task_text = task

    dl = format_deadline_badge(deadline)
    title = f"{prog} {dl}" if dl else prog
    colour = _progress_to_colour(prog)
    macro = (
        f'<ac:structured-macro ac:name="status" ac:schema-version="1">'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        f'<ac:parameter ac:name="title">{_xe(title)}</ac:parameter>'
        f'</ac:structured-macro>'
    )
    return task_text, macro


def _progress_to_colour(prog: str) -> str:
    """진행률 문자열 → Confluence Status 매크로 colour."""
    if prog in ("-%", "0%", "-"):
        return "Grey"
    if prog == "100%":
        return "Green"
    return "Yellow"


def _space_key(team_index: dict) -> str:
    return str(
        team_index.get("space_key") or team_index.get("confluence_space_key") or ""
    ).strip()


def _xe(s: str) -> str:
    return _html.escape(s)


def _section_ul_range(html: str, header: str) -> tuple[int, int]:
    """'<p><strong>{header}</strong></p>' 다음 외부 <ul> 범위 반환 (ul_start, ul_end).

    ul_end 는 '</ul>' 다음 인덱스. 없으면 (-1, -1).
    depth-aware 파싱으로 중첩 <ul> (details) 에 걸리지 않음.
    """
    marker = f"<p><strong>{header}</strong></p>"
    idx = html.find(marker)
    if idx == -1:
        return -1, -1
    after = idx + len(marker)
    ul_start = html.lower().find("<ul>", after)
    if ul_start == -1:
        return -1, -1
    # 다음 섹션 헤더보다 앞에 있어야 함
    next_p = html.find("<p><strong>", after)
    if next_p != -1 and ul_start > next_p:
        return -1, -1
    # depth-aware </ul> 탐색
    depth = 0
    i = ul_start
    while i < len(html):
        if html[i : i + 4].lower() == "<ul>":
            depth += 1
            i += 4
        elif html[i : i + 5].lower() == "</ul>":
            depth -= 1
            if depth == 0:
                return ul_start, i + 5
            i += 5
        else:
            i += 1
    return -1, -1


_BLOCK_OPEN_RE = re.compile(r"<(?:p|h[1-6])(?:\s[^>]*)?>", re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r"</(?:p|h[1-6])>", re.IGNORECASE)


def _find_section_tag_start(html: str, header: str) -> int:
    """기존 HTML에서 header 텍스트만 포함하는 블록 태그의 시작 인덱스를 반환.

    태그 구조(속성, 중첩 인라인 태그, local-id 등)와 무관하게 텍스트 내용만 비교.
    Confluence Storage Format의 <p local-id="...">, <strong local-id="...">,
    <span>, <h2>/<h4> 등 모든 형식을 지원. 없으면 -1.

    header 텍스트 위치를 먼저 찾은 뒤 '가장 가까운 앞쪽 블록 여는 태그'에 앵커링한다.
    전체 <p>…</p> 블록을 한 번에 매칭하지 않으므로, 사용자가 손수 편집해 닫히지 않은
    <p>(dangling tag) 가 섞여 있어도 non-greedy 매칭이 헤더를 통째로 삼켜 -1 이 되는
    버그가 발생하지 않는다.
    """
    pos = 0
    while True:
        idx = html.find(header, pos)
        if idx == -1:
            return -1
        pos = idx + len(header)
        # 헤더 텍스트를 감싸는 가장 가까운 블록 여는 태그 (<p>/<hN>)
        last_open = None
        for m in _BLOCK_OPEN_RE.finditer(html, 0, idx):
            last_open = m
        if last_open is None:
            continue
        # 헤더 텍스트 뒤의 첫 블록 닫는 태그까지가 한 블록 — 텍스트가 정확히 header 면 채택
        close_m = _BLOCK_CLOSE_RE.search(html, idx)
        if close_m is None:
            continue
        block_text = re.sub(
            r"<[^>]+>", "", html[last_open.start() : close_m.end()]
        ).strip()
        if block_text == header:
            return last_open.start()


def _merge_draft_sections(existing_html: str, new_html: str) -> str:
    """프로젝트/운영지원 섹션별로 기존 내용 뒤에 AI 항목 추가.

    전략:
    1. AI new_html 에서 각 섹션의 <ul>…</ul> 블록 추출 (구조 고정).
    2. existing_html 에서 '운영지원' 섹션 헤더 위치를 태그 무관하게 텍스트로 탐색.
    3. 운영지원 헤더 앞 = 프로젝트 영역, 뒤 = 운영지원 영역.
       각 영역 끝에 해당 AI <ul> 블록을 이어붙임.
    4. 경계를 못 찾으면 전체 뒤에 일괄 추가.
    """
    # AI 초안에서 섹션별 <ul>…</ul> 추출 (알려진 구조이므로 _section_ul_range 사용)
    proj_start, proj_end = _section_ul_range(new_html, "프로젝트")
    ops_start, ops_end = _section_ul_range(new_html, "운영지원")

    new_proj_ul = new_html[proj_start:proj_end] if proj_start != -1 else ""
    new_ops_ul = new_html[ops_start:ops_end] if ops_start != -1 else ""

    if not new_proj_ul and not new_ops_ul:
        return existing_html

    # 기존 HTML에서 '운영지원' 섹션 헤더 시작 위치 (태그 무관 탐색)
    ops_pos = _find_section_tag_start(existing_html, "운영지원")
    _ops_text_idx = existing_html.find("운영지원")
    logger.info(
        "merge_draft_sections ops_pos=%s proj_ul_len=%d ops_ul_len=%d "
        "ops_text_at=%s html_len=%d ops_context=%r",
        ops_pos,
        len(new_proj_ul),
        len(new_ops_ul),
        _ops_text_idx,
        len(existing_html),
        existing_html[max(0, _ops_text_idx - 60): _ops_text_idx + 80]
        if _ops_text_idx != -1
        else "(운영지원 텍스트 없음)",
    )

    if ops_pos != -1:
        before_ops = existing_html[:ops_pos]
        from_ops = existing_html[ops_pos:]
        return before_ops + new_proj_ul + from_ops + new_ops_ul
    else:
        # 경계 못 찾음 → 프로젝트/운영지원 순서대로 뒤에 추가
        return existing_html + new_proj_ul + new_ops_ul


def _update_member_this_week(
    page_html: str, member_name: str, new_this_week_html: str
) -> str:
    """담당자 표에서 member_name 행의 금주 셀(cells[1])에 내용 추가(append).

    기존 내용이 있으면 뒤에 이어쓰고, 비어 있으면 새 내용으로 채운다.
    덮어쓰지 않으므로 직접 작성한 휴가/메모 등 기존 내용이 보존된다.
    HTML 을 변경한 경우에만 수정본, 변경 없으면 원본 그대로 반환
    (호출자가 동등 비교로 '미매칭' 감지).
    """
    # "담당자" + "금주" 키워드가 모두 있는 테이블 찾기
    table_start, table_end = _find_assignee_table(page_html)
    if table_start == -1:
        return page_html

    block = page_html[table_start:table_end]

    row_pat = re.compile(r"(<tr[^>]*>)([\s\S]*?)(</tr>)", re.IGNORECASE)
    cell_pat = re.compile(r"(<t[dh][^>]*>)([\s\S]*?)(</t[dh]>)", re.IGNORECASE)

    matched = False

    def _replace_row(m: re.Match) -> str:
        nonlocal matched
        open_tr, inner, close_tr = m.group(1), m.group(2), m.group(3)
        cells = list(cell_pat.finditer(inner))
        if len(cells) < 2:
            return m.group(0)
        first_text = re.sub(r"<[^>]+>", "", cells[0].group(2)).strip()
        # 이름 정규화: '김철수(팀장)' → '김철수'
        norm = first_text
        if "(" in norm:
            norm = norm[: norm.index("(")].strip()
        if norm != member_name:
            return m.group(0)
        # cells[1] = 금주 계획 및 실적 — 섹션별 append (프로젝트/운영지원 독립 병합)
        c2 = cells[1]
        existing = c2.group(2)
        combined = _merge_draft_sections(existing, new_this_week_html) if existing.strip() else new_this_week_html
        new_inner = inner[: c2.start(2)] + combined + inner[c2.end(2) :]
        matched = True
        return open_tr + new_inner + close_tr

    new_block = row_pat.sub(_replace_row, block)
    if not matched:
        return page_html
    return page_html[:table_start] + new_block + page_html[table_end:]


def _find_assignee_table(html: str) -> tuple[int, int]:
    """'담당자' + '금주' 키워드가 있는 테이블 범위 반환. 없으면 (-1, -1)."""
    pos = 0
    while True:
        t_start = html.find("<table", pos)
        if t_start == -1:
            return -1, -1
        # depth-aware </table> 탐색
        depth = 1
        p = html.find(">", t_start) + 1
        t_end = -1
        while p < len(html):
            next_open = html.find("<table", p)
            next_close = html.find("</table>", p)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                p = next_open + 6
            else:
                depth -= 1
                if depth == 0:
                    t_end = next_close + len("</table>")
                    break
                p = next_close + 8
        if t_end == -1:
            return -1, -1
        block = html[t_start:t_end]
        if "담당자" in block and "금주" in block:
            return t_start, t_end
        pos = t_end
