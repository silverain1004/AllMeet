"""Service Account credentials — Cloud Run ADC 또는 로컬 키 파일.

Cloud Run 환경에서는 attached 된 SA 의 ADC 가 자동 감지된다.
로컬에서는 ``GOOGLE_APPLICATION_CREDENTIALS`` 환경변수의 키 파일 사용.

Drive / Calendar (공용) / Chat REST 푸시 모두 이 모듈에서 credentials 회수.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import google.auth
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_creds_cache: dict[tuple[str, ...], object] = {}


# 함수 — 스코프 별 SA credentials 싱글톤 (만료 시 자동 refresh).
def get_sa_credentials(scopes: Iterable[str]):
    """주어진 OAuth 스코프 집합의 SA credentials.

    같은 스코프 조합은 캐시. 캐시된 credentials 가 만료됐으면 refresh.
    """
    key = tuple(sorted(scopes))
    with _lock:
        creds = _creds_cache.get(key)
    if creds is not None:
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning("SA credentials refresh 실패: %s", e)
                raise
        return creds

    creds, _project = google.auth.default(scopes=list(scopes))
    if not creds.valid:
        creds.refresh(Request())
    with _lock:
        _creds_cache[key] = creds
    return creds


# 함수 — bearer token 만 필요할 때 (urllib 직접 호출용).
def get_access_token(scopes: Iterable[str]) -> str:
    """SA bearer token. 만료 시 자동 refresh."""
    creds = get_sa_credentials(scopes)
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("no_access_token")
    return token
