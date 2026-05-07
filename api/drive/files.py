"""Google Drive v3 — `files.list` generic CRUD.

Shared Drive (``corpora=drive`` + ``driveId``) 한정. 작성자/수정자 메타데이터 포함.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from api._auth.service_account import get_access_token
from config.settings import SHARED_DRIVE_ID

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


@dataclass
class ListFilesResult:
    ok: bool
    files: list[dict[str, Any]] = field(default_factory=list)
    error_kind: str = ""


def list_files_modified(
    *,
    modified_by_email: str,
    time_min: str,
    time_max: str,
    drive_id: str | None = None,
    page_size: int = 100,
) -> ListFilesResult:
    """주어진 사용자가 ``time_min ~ time_max`` 사이 수정한 Shared Drive 파일.

    Args:
        modified_by_email: 작성자/수정자 이메일 (작성자 매칭은 email only).
        time_min, time_max: RFC3339 (예: ``"2026-04-30T00:00:00Z"``).
        drive_id: Shared Drive ID. None 이면 ``SHARED_DRIVE_ID`` 환경변수.

    Returns:
        ``ListFilesResult`` — ``error_kind`` 후보:
        ``"shared_drive_id_missing"``, ``"not_found"``, ``"http_error"``, ``"auth_error"``.
    """
    drive_id_eff = (drive_id or SHARED_DRIVE_ID or "").strip()
    if not drive_id_eff:
        return ListFilesResult(ok=False, error_kind="shared_drive_id_missing")

    q_parts = [
        f"modifiedTime > '{time_min}'",
        f"modifiedTime < '{time_max}'",
        f"'{modified_by_email}' in writers",
        "trashed = false",
    ]
    q = " and ".join(q_parts)

    params = {
        "q": q,
        "corpora": "drive",
        "driveId": drive_id_eff,
        "includeItemsFromAllDrives": "true",
        "supportsAllDrives": "true",
        "fields": (
            "files(id,name,mimeType,modifiedTime,webViewLink,"
            "owners(emailAddress,displayName),"
            "lastModifyingUser(emailAddress,displayName))"
        ),
        "pageSize": str(page_size),
        "orderBy": "modifiedTime desc",
    }
    encoded = urllib.parse.urlencode(params)
    url = f"https://www.googleapis.com/drive/v3/files?{encoded}"

    try:
        token = get_access_token([_SCOPE])
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ListFilesResult(ok=False, error_kind="not_found")
        if e.code in (401, 403):
            return ListFilesResult(ok=False, error_kind="auth_error")
        logger.warning("drive list_files HTTP %s", e.code)
        return ListFilesResult(ok=False, error_kind="http_error")
    except Exception as e:
        logger.warning("drive list_files 실패: %s", e)
        return ListFilesResult(ok=False, error_kind="auth_error")

    data = json.loads(payload)
    files: list[dict[str, Any]] = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        last_user = item.get("lastModifyingUser") or {}
        files.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "mime_type": str(item.get("mimeType") or ""),
                "modified_time": str(item.get("modifiedTime") or ""),
                "web_view_link": str(item.get("webViewLink") or ""),
                "last_modifier_email": str(last_user.get("emailAddress") or ""),
                "last_modifier_name": str(last_user.get("displayName") or ""),
            }
        )
    return ListFilesResult(ok=True, files=files)
