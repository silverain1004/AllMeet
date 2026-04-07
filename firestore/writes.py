"""Firestore 클라이언트 싱글톤."""

from __future__ import annotations

import threading
from typing import Optional

from google.cloud import firestore

from config.settings import PROJECT_ID

_client: Optional[firestore.Client] = None
_lock = threading.Lock()


def get_client() -> firestore.Client:
    """프로젝트 기준 Firestore 클라이언트(스레드 안전 싱글톤)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = firestore.Client(project=PROJECT_ID)
    return _client
