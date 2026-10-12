"""기존 일정 변경 전 원본 백업(Firestore) + 되돌리기.

`schedule_event_backups/{id}`:
- requester_email / acting_as(실제로 쓴 계정) / operation / calendar_id
- events_json: 변경 전 원본 이벤트(Calendar API 응답 그대로) 목록 — JSON 문자열(필드명 제약 회피)
- created_event_ids: 이번 작업으로 새로 만든 이벤트(되돌릴 때 삭제)
- status: pending → applied | failed → restored, result_lines

되돌리기 순서: 새로 만든 이벤트 삭제 → 원본 이벤트를 원래 필드로 PATCH(삭제된 일정은
status=confirmed 로 되살림). 반복 원본(master)을 먼저 복원해야 끊긴 회차가 돌아온다.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domains.schedule_management.calendar_client import delete_event, get_event, patch_event_raw

logger = logging.getLogger(__name__)

_COLLECTION = "schedule_event_backups"
_RESTORE_FIELDS = ("summary", "description", "location", "start", "end", "attendees", "recurrence")


def _client():
    from firestore.writes import get_client

    return get_client()


def snapshot_events(
    event_ids: list[str],
    *,
    calendar_id: str,
    access_token: str,
) -> list[dict[str, Any]]:
    """변경 전 원본을 Calendar API 에서 그대로 받아 둔다(병렬). 못 받은 건 건너뛴다."""
    ids = list(dict.fromkeys(i for i in event_ids if i))
    if not ids:
        return []

    def _one(eid: str) -> dict[str, Any] | None:
        res = get_event(calendar_id=calendar_id, event_id=eid, access_token=access_token)
        return res.created_event if res.ok and res.created_event else None

    with ThreadPoolExecutor(max_workers=min(6, len(ids))) as pool:
        found = list(pool.map(_one, ids))
    return [ev for ev in found if ev]


def save_backup(
    *,
    requester_email: str,
    acting_as: str,
    operation: str,
    calendar_id: str,
    events: list[dict[str, Any]],
    summary: str = "",
) -> str:
    ref = _client().collection(_COLLECTION).document()
    ref.set(
        {
            "requester_email": requester_email,
            "acting_as": acting_as,
            "operation": operation,
            "calendar_id": calendar_id,
            "summary": summary,
            "events_json": json.dumps(events, ensure_ascii=False),
            "event_ids": [str(e.get("id") or "") for e in events],
            "created_event_ids": [],
            "status": "pending",
            "created_at": datetime.utcnow(),
        }
    )
    return ref.id


def mark_backup(
    backup_id: str,
    *,
    status: str,
    result_lines: list[str] | None = None,
    created_event_ids: list[str] | None = None,
) -> None:
    if not backup_id:
        return
    payload: dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}
    if result_lines is not None:
        payload["result_lines"] = result_lines[:50]
    if created_event_ids is not None:
        payload["created_event_ids"] = [i for i in created_event_ids if i]
    try:
        _client().collection(_COLLECTION).document(backup_id).set(payload, merge=True)
    except Exception:
        logger.warning("백업 상태 갱신 실패 id=%s", backup_id, exc_info=True)


def get_backup(backup_id: str) -> dict[str, Any] | None:
    if not backup_id:
        return None
    snap = _client().collection(_COLLECTION).document(backup_id).get()
    return snap.to_dict() if snap.exists else None


@dataclass
class RestoreResult:
    ok: bool
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _restore_body(ev: dict[str, Any]) -> dict[str, Any]:
    body = {k: ev[k] for k in _RESTORE_FIELDS if k in ev}
    if "attendees" in body:
        # 응답 상태(responseStatus 등)는 되돌릴 대상이 아니다 — 이메일·리소스 여부만.
        body["attendees"] = [
            {k: a[k] for k in ("email", "resource", "optional") if k in a}
            for a in body["attendees"]
            if isinstance(a, dict) and a.get("email")
        ]
    body["status"] = "confirmed"
    return body


def _label(ev: dict[str, Any]) -> str:
    start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
    return f"{ev.get('summary') or '(제목 없음)'} {str(start)[:16].replace('T', ' ')}"


def restore_backup(backup: dict[str, Any], *, access_token: str) -> RestoreResult:
    calendar_id = str(backup.get("calendar_id") or "primary")
    try:
        events = json.loads(str(backup.get("events_json") or "[]"))
    except json.JSONDecodeError:
        events = []
    result = RestoreResult(ok=True)
    for eid in backup.get("created_event_ids") or []:
        res = delete_event(calendar_id=calendar_id, event_id=eid, access_token=access_token, send_updates="all")
        if res.ok or res.error_kind == "calendar_not_found":
            result.done.append("이번에 새로 만든 반복 일정 삭제")
        else:
            result.failed.append(f"새로 만든 일정 삭제({eid})")
    # 반복 원본(recurrence 보유)을 먼저 — 끊었던 회차가 되살아난 뒤 회차별 예외를 덮는다.
    events.sort(key=lambda e: 0 if e.get("recurrence") else 1)
    for ev in events:
        res = patch_event_raw(
            calendar_id=calendar_id,
            event_id=str(ev.get("id") or ""),
            body=_restore_body(ev),
            send_updates="all",
            access_token=access_token,
        )
        (result.done if res.ok else result.failed).append(_label(ev))
    result.ok = not result.failed
    return result
