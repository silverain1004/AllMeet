"""시작/종료 시간 드롭다운 — 기본 30분 단위, 직접입력은 10분 단위, 목록 밖 값은 제자리 삽입."""

from __future__ import annotations


def _dropdown_values(widget: dict) -> list[str]:
    return [i["value"] for i in widget["selectionInput"]["items"] if i.get("value")]


def _start_dropdown(state: dict) -> dict:
    from domains.schedule_management.cards import _time_row_widget

    row = _time_row_widget(state, {"compose_step": "quick"})
    return row["columns"]["columnItems"][0]["widgets"][0]


def test_default_mode_uses_30_minute_steps():
    values = _dropdown_values(_start_dropdown({"duration_mode": "1h", "meeting_time": "15:00"}))
    assert values[:3] == ["08:00", "08:30", "09:00"]
    assert len(values) == 23


def test_custom_mode_uses_10_minute_steps_for_both_dropdowns():
    from domains.schedule_management.cards import _time_row_widget

    row = _time_row_widget(
        {"duration_mode": "custom", "meeting_time": "15:00", "meeting_end_time": "16:10"},
        {"compose_step": "quick"},
    )
    start, end = (c["widgets"][0] for c in row["columns"]["columnItems"])
    assert _dropdown_values(start)[:3] == ["08:00", "08:10", "08:20"]
    assert "16:10" in _dropdown_values(end)


def test_off_grid_selected_time_inserted_in_order():
    values = _dropdown_values(_start_dropdown({"duration_mode": "1h", "meeting_time": "15:10"}))
    i = values.index("15:10")
    assert values[i - 1] == "15:00" and values[i + 1] == "15:30"
