"""주간보고 초안 편집 Dialog — 버튼 클릭 시 팝업 열기 + 제출 처리.

흐름:
1. 초안 카드의 "초안 수정 후 삽입" 버튼 (OPEN_DIALOG) → build_edit_dialog() 반환
2. 사용자가 Dialog 에서 task / details 수정 후 "저장 후 Confluence 삽입" 클릭
3. handle_save_and_insert() → Firestore draft 업데이트 → Dialog 즉시 닫기 →
   백그라운드 thread 에서 Confluence 삽입 → Chat 에 결과 push
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dialog 열기
# ---------------------------------------------------------------------------

def build_edit_dialog(*, draft_ref_id: str, user_email: str) -> dict[str, Any]:
    """Firestore 초안 로드 → 편집 Dialog 카드 반환.

    예외가 발생해도 반드시 dialog 형식을 반환한다 (text 형식 반환 시 Chat 오류).
    """
    try:
        return _build_edit_dialog_inner(draft_ref_id=draft_ref_id, user_email=user_email)
    except Exception as e:
        logger.exception("build_edit_dialog 오류: %s", e)
        return _error_dialog(f"대화상자 로드 중 오류가 발생했어요: {e}")


def _build_edit_dialog_inner(*, draft_ref_id: str, user_email: str) -> dict[str, Any]:
    from domains.weekly_report.confluence_insert import _load_weekly_draft

    doc_data = _load_weekly_draft(draft_ref_id)
    if not doc_data or not doc_data.get("draft"):
        return _error_dialog("초안 데이터를 찾지 못했어요. 주간보고초안을 다시 요청해 주세요.")

    draft = doc_data["draft"]
    widgets: list[dict[str, Any]] = []

    for header, key, prefix in (
        ("프로젝트", "projects", "proj"),
        ("운영지원", "operations", "ops"),
    ):
        items = draft.get(key) or []
        if not items:
            continue
        widgets.append({"textParagraph": {"text": f"<b>── {header} ──</b>"}})
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            task = str(it.get("task") or "").strip()
            details = [str(d).strip() for d in (it.get("details") or []) if str(d or "").strip()]
            lines = [task] + [f"- {d}" for d in details]
            widgets.append(
                {
                    "textInput": {
                        "label": f"{header} {i + 1}",
                        "name": f"{prefix}_{i}",
                        "value": "\n".join(lines),
                        "type": "MULTIPLE_LINE",
                        "hintText": "첫 줄: 업무명  -% / 이후 줄: - 세부내용",
                    }
                }
            )

    if not widgets:
        return _error_dialog("수정할 초안 내용이 없어요.")

    return {
        "action_response": {
            "type": "DIALOG",
            "dialog_action": {
                "dialog": {
                    "body": {
                        "sections": [{"widgets": widgets}],
                        "fixedFooter": {
                            "primaryButton": {
                                "text": "저장 후 Confluence 삽입",
                                "onClick": {
                                    "action": {
                                        "function": "wr_save_and_insert",
                                        "parameters": [
                                            {"key": "user_email", "value": user_email},
                                            {"key": "draft_ref_id", "value": draft_ref_id},
                                        ],
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    }


# ---------------------------------------------------------------------------
# Dialog 제출 처리
# ---------------------------------------------------------------------------

def handle_save_and_insert(
    *,
    form_inputs: dict[str, Any],
    user_email: str,
    draft_ref_id: str,
    space_name: str = "",
) -> dict[str, Any]:
    """Dialog 제출 → draft 업데이트 → Dialog 즉시 닫기 → 백그라운드에서 Confluence 삽입."""
    from domains.weekly_report.confluence_insert import _load_weekly_draft

    doc_data = _load_weekly_draft(draft_ref_id)
    if not doc_data or not doc_data.get("draft"):
        return _close_with_message("초안 데이터를 찾지 못했어요.")

    draft = doc_data["draft"]

    # form_inputs: {"proj_0": {"stringInputs": {"value": ["..."]}, ...}}
    for prefix, key in (("proj", "projects"), ("ops", "operations")):
        items = draft.get(key) or []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            raw_list = (
                ((form_inputs.get(f"{prefix}_{i}") or {}).get("stringInputs") or {})
                .get("value") or [""]
            )
            text = (raw_list[0] if raw_list else "").strip()
            if not text:
                continue
            task_line, details = _parse_task_text(text)
            if task_line:
                it["task"] = task_line
            if details is not None:
                it["details"] = details

    # Firestore 업데이트
    _update_draft_in_firestore(draft_ref_id, draft)

    # Confluence 삽입은 백그라운드에서 — Chat 30초 타임아웃 회피
    threading.Thread(
        target=_insert_and_notify,
        args=(user_email, draft_ref_id, space_name),
        daemon=False,
    ).start()

    return _close_with_message("Confluence 삽입 중이에요. 잠시 후 결과를 알려드릴게요.")


# ---------------------------------------------------------------------------
# 백그라운드 Confluence 삽입
# ---------------------------------------------------------------------------

def _insert_and_notify(user_email: str, draft_ref_id: str, space_name: str) -> None:
    """Confluence 삽입 후 Chat 에 결과 push."""
    try:
        from api.chat.messages import post_message_to_space
        from domains.weekly_report.confluence_insert import handle_insert_to_confluence

        result = handle_insert_to_confluence(user_email=user_email, draft_ref_id=draft_ref_id)
        if space_name:
            post_message_to_space(space_name=space_name, payload=result)
    except Exception:
        logger.exception("Confluence 삽입 백그라운드 실패 user_email=%s", user_email)
        try:
            from api.chat.messages import post_message_to_space

            if space_name:
                post_message_to_space(
                    space_name=space_name,
                    payload={"text": "Confluence 삽입 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."},
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_task_text(text: str) -> tuple[str, list[str]]:
    """multiLine 텍스트 → (task_line, details).

    첫 줄이 task, 이후 '- ...' 형식 줄이 details.
    """
    lines = text.splitlines()
    if not lines:
        return "", []
    task_line = lines[0].strip()
    details: list[str] = []
    for line in lines[1:]:
        s = line.strip()
        if not s:
            continue
        details.append(s[2:].strip() if s.startswith("- ") else s)
    return task_line, details


def _update_draft_in_firestore(draft_ref_id: str, draft: dict) -> None:
    try:
        from datetime import datetime, timezone

        from firestore import get_client

        db = get_client()
        db.collection("weekly_drafts").document(draft_ref_id).update(
            {"draft": draft, "updated_at": datetime.now(timezone.utc)}
        )
    except Exception as e:
        logger.warning("draft Firestore 업데이트 실패: %s", e)


def _close_with_message(message: str) -> dict[str, Any]:
    """Dialog 닫기 + toast 알림."""
    return {
        "action_response": {
            "type": "DIALOG",
            "dialog_action": {
                "action_status": {
                    "status_code": "OK",
                    "user_facing_message": message,
                }
            },
        }
    }


def _error_dialog(message: str) -> dict[str, Any]:
    """오류 내용을 보여주는 Dialog."""
    return {
        "action_response": {
            "type": "DIALOG",
            "dialog_action": {
                "dialog": {
                    "body": {
                        "sections": [
                            {"widgets": [{"textParagraph": {"text": message}}]}
                        ]
                    }
                }
            },
        }
    }
