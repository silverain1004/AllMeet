"""Firestore 연동 — 클라이언트."""

from .writes import get_client
from .team_config import get_team_config, upsert_team_config

__all__ = ["get_client", "get_team_config", "upsert_team_config"]
