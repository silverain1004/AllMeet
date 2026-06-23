"""Google Chat 로딩/진행 표시 공용 헬퍼.

Cards V2 에 네이티브 스피너가 없으므로 ⏳·브레일 프레임 등 텍스트로 통일한다.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

ICON_PENDING = "▫️"
ICON_ACTIVE = "⏳"
ICON_DONE = "✅"

BRAILLE_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
DOT_FRAMES = ("◐", "◓", "◑", "◒")


def braille_frame(n: int) -> str:
    return BRAILLE_FRAMES[n % len(BRAILLE_FRAMES)]


def dot_frame(n: int) -> str:
    return DOT_FRAMES[n % len(DOT_FRAMES)]


def loading_text(label: str, *, prefix: str = ICON_ACTIVE) -> dict[str, Any]:
    """MESSAGE/CARD 즉시 응답용."""
    text = (label or "처리 중").strip()
    if not text.startswith(prefix):
        text = f"{prefix} {text}"
    return {"text": text}


def loading_update(label: str, *, prefix: str = ICON_ACTIVE) -> dict[str, Any]:
    """CARD_CLICKED — 클릭한 카드를 로딩 문구로 즉시 교체."""
    out = loading_text(label, prefix=prefix)
    out["actionResponse"] = {"type": "UPDATE_MESSAGE"}
    return out


def start_background(
    fn: Callable[..., None],
    /,
    *args: Any,
    **kwargs: Any,
) -> None:
    """daemon=False — Cloud Run 요청 종료 후에도 thread 완료까지 유지."""
    threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=False).start()
