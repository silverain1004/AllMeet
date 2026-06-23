"""일일 브리핑 데이터 수집 (LLM 2-pass).

흐름:
  1. 전 소스 메타 수집 (어제 Gmail·Drive·Confluence, 오늘 Calendar)
  2. Gmail 노이즈 필터 (_filter_gmail_noise)
  3. LLM 1차 호출 — 제목 목록 → 본문 필요 항목 선별
  4. 선별된 Gmail / Confluence 항목 본문 fetch
  5. 풍부해진 raw 반환 → 2차 LLM 분석(auto_notify에서 호출)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from api.calendar.events import list_events
from api.confluence.pages import get_page_body, list_pages_modified
from api.drive.files import list_files_modified
from api.gmail.messages import get_message_body, list_messages
from config.settings import ALLMEET_DRAFT_MODEL, LOCATION, PROJECT_ID
from domains.weekly_report.draft import (
    _confluence_fallback_names,
    _filter_gmail_noise,
    _only_my_modifications,
)
from domains.weekly_report.timewindow import kst_now

logger = logging.getLogger(__name__)

_vertex_lock = threading.Lock()
_vertex_model_select = None

# 개인 사진·미디어 파일은 할일과 무관 — 파일명만으로는 LLM이 noise로 거르지 못해
# (noise 기준이 메일/알림 위주라) FYI로 새어 나간다. 수집 단계에서 확장자로 결정적 제거.
_MEDIA_EXTENSIONS = {
    ".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff",
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".wmv", ".hevc",
}


def _is_media_file(f: dict) -> bool:
    name = str(f.get("name") or "").lower()
    return any(name.endswith(ext) for ext in _MEDIA_EXTENSIONS)


def _drop_media_files(files: list[dict]) -> list[dict]:
    """개인 사진/동영상 파일 제거 — 할일 브리핑 대상이 아님."""
    return [f for f in files if not _is_media_file(f)]


# ---------------------------------------------------------------------------
# 시간 범위
# ---------------------------------------------------------------------------

def _yesterday_range_utc() -> tuple[str, str]:
    """전날 00:00 KST ~ 오늘 현재 시각 UTC."""
    now = kst_now()
    yesterday_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        yesterday_midnight.astimezone(timezone.utc).strftime(fmt),
        now.astimezone(timezone.utc).strftime(fmt),
    )


def _today_range_utc() -> tuple[str, str]:
    """오늘 00:00 KST ~ 오늘 23:59 KST UTC."""
    now = kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        today_start.astimezone(timezone.utc).strftime(fmt),
        today_end.astimezone(timezone.utc).strftime(fmt),
    )


# ---------------------------------------------------------------------------
# 메인 수집 함수
# ---------------------------------------------------------------------------

def collect_for_user(
    *,
    user_email: str,
    team_index: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """전날 데이터 수집 + LLM 1차 선별 + 본문 fetch.

    Returns:
        (raw, errors) — raw 에 body 필드 보강된 상태.
    """
    yesterday_min, yesterday_max = _yesterday_range_utc()
    today_min, today_max = _today_range_utc()

    calendar_id = str(team_index.get("calendar_id") or "").strip()
    space_key = str(
        team_index.get("space_key") or team_index.get("confluence_space_key") or ""
    ).strip()
    raw_drive_ids = team_index.get("shared_drive_ids") or []
    shared_drive_ids = [str(x).strip() for x in raw_drive_ids if str(x).strip()]

    team_id = str(team_index.get("id") or team_index.get("team_id") or "").strip()
    confluence_names = _confluence_fallback_names(team_id, user_email)

    raw: dict[str, Any] = {}
    errors: dict[str, str] = {}

    # 1. Gmail 메타 (어제)
    _collect_gmail_meta(raw, errors, user_email, yesterday_min, yesterday_max)

    # 2. Drive (어제) — 공유 + 개인
    _collect_drive(raw, errors, user_email, shared_drive_ids, yesterday_min, yesterday_max)

    # 3. Confluence 메타 (어제)
    _collect_confluence_meta(raw, errors, user_email, space_key, confluence_names, yesterday_min, yesterday_max)

    # 4. 오늘 일정 (Calendar)
    _collect_today_calendar(raw, errors, user_email, calendar_id, today_min, today_max)

    # 5. LLM 1차 — 제목 보고 본문 필요 항목 선별
    selected = _select_items_needing_body(raw)

    # 6. 선별 항목 본문 fetch
    _enrich_gmail_body(raw, user_email, selected.get("gmail_ids") or [])
    _enrich_confluence_body(raw, selected.get("confluence_ids") or [])

    return raw, errors


# ---------------------------------------------------------------------------
# 서비스별 메타 수집
# ---------------------------------------------------------------------------

def _collect_gmail_meta(
    raw: dict, errors: dict, user_email: str, time_min: str, time_max: str
) -> None:
    try:
        res = list_messages(user_email=user_email, time_min=time_min, time_max=time_max, max_results=50)
        if res.ok:
            raw["gmail"] = _filter_gmail_noise(res.messages, user_email)
        else:
            errors["gmail"] = res.error_kind or "unknown"
    except Exception as e:
        logger.warning("daily_brief gmail 메타 수집 실패: %s", e)
        errors["gmail"] = "exception"


def _collect_drive(
    raw: dict, errors: dict, user_email: str,
    shared_drive_ids: list[str], time_min: str, time_max: str,
) -> None:
    # 공유 드라이브
    if shared_drive_ids:
        merged: list[dict] = []
        seen: set[str] = set()
        per_errors: list[str] = []
        for d_id in shared_drive_ids:
            try:
                res = list_files_modified(
                    modified_by_email=user_email,
                    time_min=time_min,
                    time_max=time_max,
                    drive_id=d_id,
                    credentials_source="user_oauth",
                    user_email=user_email,
                )
                if res.ok:
                    for f in _drop_media_files(_only_my_modifications(res.files, user_email)):
                        fid = str(f.get("id") or "")
                        if fid and fid not in seen:
                            seen.add(fid)
                            merged.append(f)
                else:
                    per_errors.append(res.error_kind or "unknown")
            except Exception as e:
                logger.warning("daily_brief drive 수집 실패 drive_id=%s: %s", d_id, e)
                per_errors.append("exception")
        raw["drive"] = merged
        if per_errors and not merged:
            errors["drive"] = ";".join(per_errors)
    else:
        raw["drive"] = []

    # 개인 드라이브
    try:
        res = list_files_modified(
            modified_by_email=user_email,
            time_min=time_min,
            time_max=time_max,
            credentials_source="user_oauth",
            user_email=user_email,
        )
        if res.ok:
            raw["personal_drive"] = _drop_media_files(_only_my_modifications(res.files, user_email))
        else:
            errors["personal_drive"] = res.error_kind or "unknown"
    except Exception as e:
        logger.warning("daily_brief personal_drive 수집 실패: %s", e)
        errors["personal_drive"] = "exception"


def _collect_confluence_meta(
    raw: dict, errors: dict, user_email: str, space_key: str,
    confluence_names: list[str], time_min: str, time_max: str,
) -> None:
    if not space_key:
        errors["confluence"] = "space_key_missing"
        return
    try:
        res = list_pages_modified(
            space_key=space_key,
            modified_by_email=user_email,
            time_min=time_min,
            time_max=time_max,
            fallback_display_names=confluence_names,
        )
        if res.ok:
            raw["confluence"] = res.pages
        else:
            errors["confluence"] = res.error_kind or "unknown"
    except Exception as e:
        logger.warning("daily_brief confluence 수집 실패: %s", e)
        errors["confluence"] = "exception"


def _collect_today_calendar(
    raw: dict, errors: dict, user_email: str,
    calendar_id: str, time_min: str, time_max: str,
) -> None:
    try:
        res = list_events(
            calendar_id="primary",
            time_min=time_min,
            time_max=time_max,
            credentials_source="user_oauth",
            user_email=user_email,
        )
        if res.ok:
            raw["today_calendar"] = res.events
        else:
            errors["today_calendar"] = res.error_kind or "unknown"
    except Exception as e:
        logger.warning("daily_brief today_calendar 수집 실패: %s", e)
        errors["today_calendar"] = "exception"


# ---------------------------------------------------------------------------
# LLM 1차 — 본문 필요 항목 선별
# ---------------------------------------------------------------------------

def _select_items_needing_body(raw: dict[str, Any]) -> dict[str, list[str]]:
    """제목 목록을 Gemini Flash에 넘겨 본문 fetch 필요 항목 ID 선별."""
    from domains.daily_brief.prompts import SELECT_SCHEMA, build_select_prompt

    has_gmail = bool(raw.get("gmail"))
    has_confluence = bool(raw.get("confluence"))
    if not has_gmail and not has_confluence:
        return {"gmail_ids": [], "confluence_ids": []}

    prompt = build_select_prompt(raw)
    try:
        model = _get_select_model()
        from vertexai.generative_models import GenerationConfig
        res = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.0,  # 본문 필요 ID 선별은 순수 분류 — 재현성 위해 0
                max_output_tokens=1024,
                response_mime_type="application/json",
                response_schema=SELECT_SCHEMA,
            ),
        )
        text = (res.text or "").strip()
        if not text:
            return {"gmail_ids": [], "confluence_ids": []}
        data = json.loads(text)
        return {
            "gmail_ids": [str(x) for x in (data.get("gmail_ids") or [])],
            "confluence_ids": [str(x) for x in (data.get("confluence_ids") or [])],
        }
    except Exception as e:
        logger.warning("daily_brief 1차 LLM 선별 실패, 전체 본문 fetch로 폴백: %s", e)
        # 폴백: 전체 선택
        return {
            "gmail_ids": [m["id"] for m in (raw.get("gmail") or []) if m.get("id")],
            "confluence_ids": [p["id"] for p in (raw.get("confluence") or []) if p.get("id")],
        }


# ---------------------------------------------------------------------------
# 본문 fetch
# ---------------------------------------------------------------------------

def _enrich_gmail_body(raw: dict[str, Any], user_email: str, ids: list[str]) -> None:
    """선별된 Gmail ID의 본문을 fetch해서 raw["gmail"] 각 항목에 body 필드 추가."""
    if not ids:
        return
    id_set = set(ids)
    for m in raw.get("gmail") or []:
        if m.get("id") not in id_set:
            continue
        try:
            body = get_message_body(message_id=m["id"], user_email=user_email)
            m["body"] = body
        except Exception as e:
            logger.warning("daily_brief gmail body fetch 실패 id=%s: %s", m.get("id"), e)


def _enrich_confluence_body(raw: dict[str, Any], ids: list[str]) -> None:
    """선별된 Confluence 페이지 ID의 본문을 fetch해서 raw["confluence"] 각 항목에 body 추가."""
    if not ids:
        return
    id_set = set(ids)
    for p in raw.get("confluence") or []:
        if p.get("id") not in id_set:
            continue
        try:
            body = get_page_body(page_id=p["id"])
            p["body"] = body
        except Exception as e:
            logger.warning("daily_brief confluence body fetch 실패 id=%s: %s", p.get("id"), e)


# ---------------------------------------------------------------------------
# Vertex 모델 (1차 선별용 Flash)
# ---------------------------------------------------------------------------

def _get_select_model():
    global _vertex_model_select
    if _vertex_model_select is not None:
        return _vertex_model_select
    with _vertex_lock:
        if _vertex_model_select is not None:
            return _vertex_model_select
        import vertexai
        from vertexai.generative_models import GenerativeModel

        vertexai.init(project=PROJECT_ID, location=LOCATION)
        # 1차 선별은 Flash로 (빠르고 저렴). 하드코딩한 gemini-2.0-flash-001 은
        # 배포 리전(us-central1)에 없어 404 → 매번 폴백했으므로, 동작이 확인된
        # 설정값(ALLMEET_DRAFT_MODEL, 기본 gemini-2.5-flash)을 재사용한다.
        _vertex_model_select = GenerativeModel(ALLMEET_DRAFT_MODEL)
    return _vertex_model_select
