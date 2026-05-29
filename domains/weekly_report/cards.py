"""주간보고초안 결과 카드 빌더 — Google Chat cardsV2 스키마."""

from __future__ import annotations

import html
import re
from typing import Any, Callable

# task 끝의 진행률 표기 — ' -%' 또는 ' 100%', ' 50%' 등. 시각 강조용 분리에 사용.
_PROGRESS_SUFFIX_RE = re.compile(r"\s+(-%|\d{1,3}%)\s*$")


def build_in_progress_card() -> dict[str, Any]:
    """즉시 응답용 — '분석 중' 안내."""
    return {"text": "주간보고초안 분석 중이에요. 잠시 후 결과를 보내드릴게요."}


def build_team_not_found_card(user_email: str) -> dict[str, Any]:
    """사용자 이메일이 어떤 팀에도 등록 안 된 경우."""
    safe_email = html.escape(user_email)
    return {
        "text": (
            f"`{safe_email}` 으로 등록된 팀을 찾지 못했어요.\n"
            "팀 설정에서 본인 이메일이 멤버로 등록되어 있는지 확인해 주세요."
        )
    }


def build_no_data_card(user_name: str, team_name: str, meeting_date: str) -> dict[str, Any]:
    """모든 서비스에서 빈 결과 — 활동 없음 안내."""
    return {
        "text": (
            f"<b>{html.escape(user_name)}</b> 님의 한 주 활동을 찾지 못했어요.\n"
            f"팀: {html.escape(team_name)} · 회의 일자: {html.escape(meeting_date)}\n"
            "이번 두 주 동안 캘린더·Drive·Confluence 에 기록된 활동이 없네요."
        )
    }


def build_draft_card(
    *,
    user_name: str,
    user_email: str,
    team_name: str,
    meeting_date: str,
    raw: dict[str, Any],
    draft: dict[str, Any] | None,
    errors: dict[str, str],
) -> dict[str, Any]:
    """수집·분석 결과 통합 카드.

    Args:
        raw: 서비스별 raw 결과 (``calendar``, ``drive``, ``confluence``, ``gmail`` 키).
        draft: Vertex 분석 결과 dict 또는 None (실패).
        errors: 서비스별 ``error_kind`` (성공이면 키 없음).
    """
    sections: list[dict[str, Any]] = []

    # 헤더 정보
    sections.append(
        {
            "widgets": [
                {
                    "textParagraph": {
                        "text": (
                            f"<b>{html.escape(user_name)}</b> ({html.escape(user_email)})<br>"
                            f"팀: {html.escape(team_name)}<br>"
                            f"주간회의 일자: {html.escape(meeting_date)}"
                        )
                    }
                }
            ]
        }
    )

    # 회의 이력
    sections.append(
        _service_section(
            header="📅 회의 이력",
            items=raw.get("calendar") or [],
            error=errors.get("calendar"),
            fmt=lambda e: f"{(e.get('summary') or '-')} ({(e.get('start') or '-')[:10]})",
        )
    )

    # Drive
    sections.append(
        _service_section(
            header="📁 Drive 작성/수정 파일",
            items=raw.get("drive") or [],
            error=errors.get("drive"),
            fmt=lambda f: f"{(f.get('name') or '-')} ({(f.get('modified_time') or '-')[:10]})",
        )
    )

    # Confluence
    sections.append(
        _service_section(
            header="📄 Confluence 페이지",
            items=raw.get("confluence") or [],
            error=errors.get("confluence"),
            fmt=lambda p: f"{(p.get('title') or '-')} ({(p.get('modified_time') or '-')[:10]})",
        )
    )

    # Gmail (Phase 2 — error 가 auth_required 면 안내, 미연결이면 섹션 자체 생략)
    gmail_error = errors.get("gmail")
    gmail_items = raw.get("gmail") or []
    if gmail_error or gmail_items:
        sections.append(
            _service_section(
                header="📧 Gmail",
                items=gmail_items,
                error=gmail_error,
                fmt=lambda m: f"{(m.get('subject') or '-')} (~{(m.get('from') or '-')[:30]})",
            )
        )

    # 개인 Calendar (Phase 2)
    personal_cal_error = errors.get("personal_calendar")
    personal_cal_items = raw.get("personal_calendar") or []
    if personal_cal_error or personal_cal_items:
        sections.append(
            _service_section(
                header="🗓️ 개인 일정",
                items=personal_cal_items,
                error=personal_cal_error,
                fmt=lambda e: f"{(e.get('summary') or '-')} ({(e.get('start') or '-')[:10]})",
            )
        )

    # 내 Drive (Phase 2 — 본인 My Drive)
    personal_drv_error = errors.get("personal_drive")
    personal_drv_items = raw.get("personal_drive") or []
    if personal_drv_error or personal_drv_items:
        sections.append(
            _service_section(
                header="📁 내 드라이브",
                items=personal_drv_items,
                error=personal_drv_error,
                fmt=lambda f: f"{(f.get('name') or '-')} ({(f.get('modified_time') or '-')[:10]})",
            )
        )

    # Vertex 종합 초안 — 프로젝트 / 운영지원 두 카테고리.
    if draft:
        body = _render_draft_body(draft)
        if body:
            sections.append(
                {
                    "header": "✏️ 종합 초안",
                    "widgets": [{"textParagraph": {"text": body}}],
                }
            )

    return {
        "cardsV2": [
            {
                "cardId": "weekly_report_draft",
                "card": {
                    "header": {
                        "title": f"{user_name}님의 한 주 활동",
                        "subtitle": f"{team_name} · {meeting_date}",
                    },
                    "sections": sections,
                },
            }
        ]
    }


def _render_draft_body(draft: dict[str, Any]) -> str:
    """draft = {projects: [...], operations: [...]} → cardsV2 textParagraph HTML.

    출력 양식 — 사용자 선호에 따라 평문 ``-`` 하이픈으로 통일. 위계는 들여쓰기로만 구분.
    진행률(100%/-%) 색상·볼드 강조는 유지. ``<font>``·``<b>`` 는 평문 복사 시 제거되므로
    클립보드엔 ``- [1제강] 전기로 ... 100%`` 형태로 떨어져 Confluence/노션 붙여넣기에 깔끔.

    구조:
        <b>[프로젝트]</b>
        - task1  100%               ← 100% 는 녹색·볼드
        &nbsp;&nbsp;&nbsp;&nbsp;- detail1
        &nbsp;&nbsp;&nbsp;&nbsp;- detail2
                                    ← 빈 줄
        - task2  -%                 ← -% 는 회색·볼드 (사용자가 채울 자리)
        ...
    """
    blocks: list[str] = []
    for header, key in (("프로젝트", "projects"), ("운영지원", "operations")):
        items = draft.get(key) or []
        if not items:
            continue
        task_blocks: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            task = str(it.get("task") or "").strip()
            if not task:
                continue
            task_lines = [f"- {_format_task_with_progress(task)}"]
            for d in it.get("details") or []:
                detail = str(d or "").strip()
                if not detail:
                    continue
                task_lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;- {html.escape(detail)}")
            task_blocks.append("<br>".join(task_lines))
        if task_blocks:
            # task 사이엔 빈 줄(<br><br>) — 본인 평소 작성 스타일이 task 간 빈 줄 분리.
            blocks.append(f"<b>[{header}]</b><br>" + "<br><br>".join(task_blocks))
    return "<br><br>".join(blocks)


def _format_task_with_progress(task: str) -> str:
    """task 끝의 ' -%' / ' 100%' 진행률을 색상·볼드로 분리 강조.

    매치 없으면 task 전체를 escape 만 해서 반환.
    """
    m = _PROGRESS_SUFFIX_RE.search(task)
    if not m:
        return html.escape(task)
    head = task[: m.start()].rstrip()
    prog = m.group(1)
    color = "#9aa0a6" if prog == "-%" else "#1e8e3e"  # 회색 / 녹색
    return f"{html.escape(head)} <font color=\"{color}\"><b>{prog}</b></font>"


def _service_section(
    *,
    header: str,
    items: list[Any],
    error: str | None,
    fmt: Callable[[Any], str],
) -> dict[str, Any]:
    if error == "auth_required":
        body = "🔒 OAuth 미연결 — '내 데이터 연결' 카드를 클릭해 권한을 부여해 주세요."
    elif error == "shared_drive_id_missing":
        body = "⚠️ Shared Drive ID 미설정 — `SHARED_DRIVE_ID` 환경변수 확인 필요."
    elif error == "space_key_missing":
        body = "⚠️ Confluence 스페이스 키 미설정 — 팀 설정에서 `space_key` 확인 필요."
    elif error == "calendar_id_missing":
        body = "⚠️ 팀 캘린더 ID 미설정."
    elif error:
        body = f"⚠️ 조회 실패 ({html.escape(error)})"
    elif not items:
        body = "(이 한 주에 활동이 없어요)"
    else:
        body = "<br>".join(f"{i + 1}. {html.escape(fmt(it))}" for i, it in enumerate(items))
    return {"header": header, "widgets": [{"textParagraph": {"text": body}}]}
