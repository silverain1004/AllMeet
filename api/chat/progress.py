"""Chat 진행상황 라이브 메시지 — 한 메시지를 patch 로 단계마다 갱신.

백그라운드 thread 가 오래 걸리는 작업(주간보고초안·전문가 찾기 등)을 돌릴 때,
사용자에게 '뭐뭐 파악했습니다' 식 중간 진행상황을 보여주기 위한 공용 헬퍼.

``begin`` 으로 메시지를 만들어 이름을 확보하고, 단계별 ``done``/``active`` 가 patch 한다.
마지막 ``finish`` 가 같은 메시지를 결과 카드로 교체 → 채팅에 메시지 1개만 남는다.
메시지 생성/patch 실패는 모두 베스트-에포트 — 실패해도 호출 측 작업 흐름은 계속된다.
"""

from __future__ import annotations

from typing import Any

from api.chat.loading import ICON_ACTIVE, ICON_DONE, ICON_PENDING
from api.chat.messages import create_message_in_space, update_message_in_space

# 단계별 상태 아이콘 — loading.py 와 통일.
_ICON = {"done": ICON_DONE, "active": ICON_ACTIVE, "pending": ICON_PENDING}


class LiveProgress:
    """단계 목록을 받아 라이브 진행 메시지를 관리.

    Args:
        space_name: ``"spaces/XXX"`` 또는 ``"XXX"``.
        title: 메시지 상단 제목 (예: ``"주간보고초안 분석 중..."``).
        stages: ``[(key, label), ...]`` — 표시 순서. ``key`` 로 ``done``/``active`` 를 호출.
    """

    def __init__(
        self, *, space_name: str, title: str, stages: list[tuple[str, str]]
    ) -> None:
        self.space_name = space_name
        self.title = title
        self._order = [k for k, _ in stages]
        self._label = {k: label for k, label in stages}
        self.status: dict[str, str] = {k: "pending" for k in self._order}
        self.count: dict[str, int] = {}
        self.detail: dict[str, str] = {}
        self.message_name: str | None = None

    @property
    def live(self) -> bool:
        return self.message_name is not None

    def begin(self) -> bool:
        """진행 메시지 생성 후 첫 단계를 active 로. 생성 실패 시 live=False 폴백."""
        if self._order:
            self.status[self._order[0]] = "active"
        self.message_name = create_message_in_space(
            space_name=self.space_name, payload=self._card()
        )
        return self.live

    def done(self, key: str, count: int | None = None) -> None:
        """단계 완료 표시(+건수) 후 다음 단계를 active 로. patch."""
        if key in self.status:
            self.status[key] = "done"
            if count is not None:
                self.count[key] = count
        self._activate_next()
        self._patch()

    def active(self, key: str, detail: str = "") -> None:
        """단계를 진행 중으로. ``detail`` 은 진행 부가설명(예: 점진확장 윈도우)."""
        if key not in self.status or self.status[key] == "done":
            return
        changed = self.status[key] != "active" or self.detail.get(key, "") != detail
        self.status[key] = "active"
        if detail:
            self.detail[key] = detail
        if changed:
            self._patch()

    def finish(self, result_card: dict[str, Any]) -> None:
        """진행 메시지를 최종 결과 카드로 교체."""
        if self.live:
            update_message_in_space(
                message_name=self.message_name or "", payload=result_card
            )

    def _activate_next(self) -> None:
        for k in self._order:
            if self.status[k] == "pending":
                self.status[k] = "active"
                break

    def _card(self) -> dict[str, Any]:
        lines = [f"📊 *{self.title}*", ""]
        for k in self._order:
            status = self.status[k]
            icon = _ICON.get(status, "▫️")
            label = self._label[k]
            if status == "done":
                count = self.count.get(k)
                suffix = f" {count}건" if isinstance(count, int) else " 완료"
                lines.append(f"{icon} {label}{suffix}")
            elif status == "active":
                detail = self.detail.get(k)
                tail = f" 확인 중... ({detail})" if detail else " 확인 중..."
                lines.append(f"{icon} {label}{tail}")
            else:
                lines.append(f"{icon} {label}")
        return {"text": "\n".join(lines)}

    def _patch(self) -> None:
        if self.live:
            update_message_in_space(
                message_name=self.message_name or "", payload=self._card()
            )
