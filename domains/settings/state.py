"""설정 화면 상태 파싱."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TeamSettingsContext:
    team_id: str = ""
    member_mode: str = "list"
    team_meta_mode: str = ""
    calendar_test_message: str = ""


def _safe_form_value(form_inputs: dict[str, Any], key: str) -> str:
    raw = form_inputs.get(key)
    if isinstance(raw, dict):
        string_inputs = raw.get("stringInputs") or {}
        values = string_inputs.get("value") or []
        if isinstance(values, list) and values:
            return str(values[0]).strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _safe_team_id(form_inputs: dict[str, Any], parameters: dict[str, str]) -> str:
    from firestore.team_config import normalize_team_id

    team_id = (parameters.get("team_id") or _safe_form_value(form_inputs, "team_id") or "").strip()
    if team_id == "__none__":
        return ""
    return normalize_team_id(team_id)


def parse_team_context(
    *,
    parameters: dict[str, str],
    form_inputs: dict[str, Any],
    teams: list[dict[str, str]],
) -> TeamSettingsContext:
    team_id = _safe_team_id(form_inputs, parameters)
    if not team_id and teams:
        team_id = str(teams[0].get("id") or "")
    return TeamSettingsContext(
        team_id=team_id,
        member_mode=str(parameters.get("member_mode") or "list").strip() or "list",
        team_meta_mode=str(parameters.get("team_meta_mode") or "").strip(),
        calendar_test_message=str(parameters.get("calendar_test_message") or "").strip(),
    )
