"""도구(tool) 레지스트리 — '사전 정의된 API 리스트'.

각 도구는 기존 함수를 얇은 어댑터로 감싼 것으로, 항상
``{"ok": bool, "error_kind": str, "detail": str, ...}`` 형태의 dict 를 반환한다.
플래너 LLM 은 이 레지스트리의 name/description/parameters 만 보고 워크플로우를 설계한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    """실행 가능한 도구 한 개의 메타데이터 + 실행 함수.

    Attributes:
        name: 고유 도구 이름 (계획 step.tool 과 매칭).
        description: LLM 이 "언제·어떻게 쓰는지" 판단하는 핵심 설명. 선행 조건·반환값을 적는다.
        parameters: JSON schema (type=object). 인자 추출 가이드.
        run: ``run(**args) -> dict`` 실제 실행 어댑터. 정규화된 결과 dict 반환.
        side_effect: True 면 되돌릴 수 없는 작업 → 승인 게이트/dry-run 대상.
        examples: 플래너 few-shot용 대표 사용예. ``[{"when": "언제 쓰는지", "args": {...}}]``.
            카탈로그 직렬화·검색(select_catalog) 에 함께 노출돼 플래너의 도구 선택을 돕는다.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    run: Callable[..., dict[str, Any]] = field(default=lambda **_: {"ok": False})
    side_effect: bool = False
    examples: list[dict[str, Any]] = field(default_factory=list)


TOOL_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    """도구를 전역 레지스트리에 등록 (이름 중복 시 덮어씀)."""
    TOOL_REGISTRY[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    return TOOL_REGISTRY.get((name or "").strip())


def list_tools() -> list[Tool]:
    return list(TOOL_REGISTRY.values())


def _serialize(tool: Tool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "side_effect": tool.side_effect,
    }
    if tool.examples:  # examples 없는 도구는 프롬프트 비대 방지를 위해 키 자체를 생략
        out["examples"] = tool.examples
    return out


def tools_catalog() -> list[dict[str, Any]]:
    """플래너 프롬프트에 주입할 직렬화된 도구 카탈로그(전체)."""
    return [_serialize(t) for t in TOOL_REGISTRY.values()]


_TOKEN_RE = re.compile(r"[a-z0-9]+|[가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    """영문/숫자 토큰 + 2자 이상 한글 토큰 집합."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _score_tool(tool: Tool, query_tokens: set[str]) -> int:
    """요청 토큰이 도구 name+description+examples 에 얼마나 등장하는지(부분일치) 셈."""
    example_text = " ".join(str(e.get("when") or "") for e in tool.examples)
    text = f"{tool.name} {tool.description} {example_text}".lower()
    return sum(1 for t in query_tokens if t in text)


def select_catalog(user_message: str, *, max_tools: int = 30) -> list[dict[str, Any]]:
    """요청과 관련된 도구만 골라 직렬화한 카탈로그(retrieval).

    - 등록 도구 수가 ``max_tools`` 이하면 전체 반환(현재 ~19개 규모에선 전체 노출 = 동작 불변).
    - 초과 시: side_effect 도구는 누락 위험을 없애려 항상 포함하고, 나머지(읽기/생성)는
      요청 키워드(+examples) 스코어 상위로 채워 ``max_tools`` 개로 제한한다.
      도구가 더 늘어 가지치기가 켜지면, generate_content 처럼 사실상 범용인 도구는 항상포함을
      검토할 것(키워드 미스 시 누락 방지).
    - 등록 순서를 보존해 직렬화한다.
    """
    tools = list(TOOL_REGISTRY.values())
    if len(tools) <= max_tools:
        return tools_catalog()

    query_tokens = _tokens(user_message)
    side = [t for t in tools if t.side_effect]
    rest = [t for t in tools if not t.side_effect]
    rest_sorted = sorted(rest, key=lambda t: _score_tool(t, query_tokens), reverse=True)
    budget = max(0, max_tools - len(side))
    selected = side + rest_sorted[:budget]

    order = {t.name: i for i, t in enumerate(tools)}
    selected.sort(key=lambda t: order[t.name])
    return [_serialize(t) for t in selected]
