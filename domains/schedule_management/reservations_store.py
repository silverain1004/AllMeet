"""예약 이력 Firestore (감사·취소용)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from firestore.writes import get_client

_COLLECTION = "schedule_reservations"


def save_reservation(
    *,
    user_email: str,
    calendar_id: str,
    event_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    html_link: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    db = get_client()
    ref = db.collection(_COLLECTION).document()
    payload: dict[str, Any] = {
        "user_email": user_email,
        "calendar_id": calendar_id,
        "event_id": event_id,
        "summary": summary,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "html_link": html_link,
        "created_at": datetime.utcnow(),
        "status": "active",
    }
    if extra:
        payload.update(extra)
    ref.set(payload)
    return ref.id
