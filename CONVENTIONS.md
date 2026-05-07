# 코드 컨벤션 & 템플릿

이 문서는 `all-meet-agent` 저장소에서 새 코드를 짤 때 따라야 할 **실제 코드 패턴 모음**입니다. 큰 그림(아키텍처·데이터 모델)은 `CLAUDE.md` 와 `ARCHITECTURE.md` 에 있고, 여기에는 "주석은 어떻게 달고, 함수는 어떻게 쪼개고, 응답 dict 는 어떻게 만드는가" 같은 구체적 템플릿만 둡니다.

> 새 파일·함수를 만들 때는 이 문서의 스니펫을 그대로 복사해 시작하세요. 기존 파일을 고칠 때는 그 파일의 스타일에 맞추는 게 우선입니다.
>
> **외부 API 연동을 추가하려는 경우, 먼저 §11 (`api/` vs `domains/` 레이어 분리) 부터 읽으세요.** "어떤 함수를 어디에 둘 것인가" 는 코드를 쓰기 전 결정해야 합니다.

---

## 1. 파일 구조

모든 `.py` 파일은 다음 구조를 따릅니다.

```python
"""한 줄 요약 — 이 모듈이 무엇을 하는지.

(선택) 줄을 비우고, 이 모듈을 누가 어떻게 쓰는지 2~4줄 설명.
'어디서 import 하는지' 까지 적어 두면 호출 그래프가 보입니다.
"""

from __future__ import annotations  # 거의 항상 첫 줄

import logging                       # 1) 표준 라이브러리
import re
from datetime import datetime
from typing import Any

import functions_framework           # 2) 외부 패키지
from google.cloud import firestore

from config.settings import PROJECT_ID   # 3) 프로젝트 내부 모듈
from firestore.documents import document_ref

logger = logging.getLogger(__name__)
```

- 모듈 docstring 은 한국어, 1줄 요약 + 필요 시 짧은 본문.
- `from __future__ import annotations` 는 거의 모든 파일에 포함합니다 (`tests/*` 의 단순 스크립트는 예외).
- import 그룹은 빈 줄로 분리: 표준 → 외부 → 내부.
- 무거운 의존성(Vertex SDK, `google-genai`)은 **함수 안에서 lazy import** 합니다 — 콜드스타트 비용 절감 (`chat.py` 의 `_get_generative_model` 참고).

---

## 2. 주석 & docstring

### Docstring
- **모든 public 함수에 한국어 docstring** 을 답니다.
- 1줄로 끝낼 수 있으면 1줄, 동작 단계가 여러 개면 줄바꿈 후 본문.
- "무엇을 하는지" + (필요 시) "예외/폴백 동작" 정도. 매개변수 설명은 타입 힌트로 대체합니다.

```python
def conversation_doc_id(space_id: str, user_name: str | None) -> str:
    """Firestore 루트 문서 ID 문자열만 계산한다(문서 생성/조회 아님).

    * `user_name` 없음 → ``space_id`` 그대로.
    * 있으면 ``{space_id}_{표시명 슬러그}`` (비문자·연속 밑줄 정리, 최대 80자).
    """
```

### 인라인 주석 패턴
- **함수 위 한 줄 요약** — 같은 파일에 비슷한 함수가 여러 개일 때 빠르게 훑게 해줍니다 (`chat.py` 스타일).

  ```python
  # 함수 — 사용자가 봇 능력·기능을 묻는지 LLM으로 판별.
  def _user_asks_capabilities(user_message: str, ctx_block: str) -> bool:
      ...
  ```

- **단계 번호 주석** — 한 함수 안에서 단계가 분명할 때 (`reply_daily_chat` 스타일).

  ```python
  # 1. 입력 정리
  msg = (user_message or "").strip()
  ...

  # 2. MESSAGE + 유효 space면 Firestore 루트 보장·user_context 반영
  if chat_event and chat_event.get("type") == "MESSAGE":
      ...
  ```

- **섹션 구분선** — 한 파일에 여러 책임이 섞일 때 (`main.py` 스타일).

  ```python
  # ---------------------------------------------------------------------------
  # 의도 분류 (진입점 전용) — 키워드·패턴 기준. 나중에 LLM 분류로 바꿀 수 있음.
  # ---------------------------------------------------------------------------
  ```

- **비명시적 동작에만 `# 주석`** — "왜 이렇게 했는가" 가 코드만 봐선 모를 때만 답니다. `# x를 1 증가` 같은 자명한 주석은 쓰지 않습니다.

---

## 3. 함수 분리 패턴

### 3.1 헬퍼 네이밍 (모두 `_` 프리픽스, 모듈 외부 노출 금지)

| 접두어 | 역할 | 예시 |
|---|---|---|
| `_safe_*` | 입력 dict 에서 None-safe 하게 값 꺼내기 | `_safe_form_value`, `_safe_team_id` |
| `_normalize_*` | 외부 입력(폼 문자열, dict 리스트) 을 표준 형태로 변환 | `_normalize_nicknames`, `_normalize_members` |
| `_parse_*` | 문자열 → 구조 파싱 | `_parse_card_parameters`, `parse_root_page_ids` |
| `_get_*` | 싱글톤·캐시된 리소스 가져오기 | `_get_generative_model`, `get_client` |
| `_<verb>_like` | 의도 분류용 boolean 술어 | `_weekly_meeting_like`, `_schedule_like` |
| `_action_*` | 인터랙티브 CLI 한 화면 분량 (tests/*) | `_action_register`, `_action_edit` |

### 3.2 keyword-only 인자

매개변수가 3개를 넘거나 의미가 헷갈릴 수 있으면 `*` 로 keyword-only 강제. 위치 인자 호출 실수를 막습니다.

```python
def upsert_team_config(
    *,
    team_id: str,
    team_name: str,
    space_id: str,
    user_context: dict[str, Any] | None,
    updates: dict[str, Any],
) -> dict[str, Any]:
    ...

# 호출 시
upsert_team_config(team_id=tid, team_name=name, space_id=sid, user_context=ctx, updates={...})
```

### 3.3 dataclass 는 결과 객체에만

요청·이벤트 페이로드는 그대로 `dict[str, Any]` 로 다룹니다 (Google Chat 스키마가 자주 바뀌고, 키 누락에 관대해야 함).
**오직 함수 반환 결과** 가 여러 필드를 가질 때만 `@dataclass` 를 씁니다.

```python
@dataclass
class LookupResult:
    ok: bool
    events: list[dict[str, str]]
    error_kind: str = ""
```

### 3.4 평면 dispatch (match/case 가 아니라 if-chain)

`handle_weekly_meeting_action` 처럼 액션이 30개+ 일 때는 **평면 if-chain** 을 씁니다 — 그룹별로 한 줄 빈 줄로 시각 분리만 하고, match/case 로 묶지 않습니다 (액션이 늘어날 때 머지 충돌이 적음).

```python
def handle_weekly_meeting_action(*, invoked_function: str, ...) -> dict[str, Any]:
    # 메인 분기
    if invoked_function == "wm_open_menu":
        return build_weekly_meeting_menu_card(include_action_response=True)
    if invoked_function == "wm_open_schedule_menu":
        return build_schedule_menu_card(get_team_list(), include_action_response=True)

    # 일정 조회
    if invoked_function in {"wm_schedule_meeting_this", "wm_schedule_meeting_next"}:
        ...

    # 팀 설정
    if invoked_function == "wm_team_open_list":
        ...

    return {"text": "지원하지 않는 카드 액션입니다."}
```

반대로 **유한·고정 enum** (예: `UserIntent` 4개) 은 `match/case` 가 더 명확합니다 — `_dispatch_by_intent` 참고.

---

## 4. 타입 힌트

- Python 3.12 빌트인 제네릭 사용: `list[...]`, `dict[...]`, `tuple[...]`. `typing.List` 등은 쓰지 않습니다.
- `Optional[X]` 대신 **`X | None`**.
- 외부 페이로드는 `dict[str, Any]`. 깊게 풀어서 TypedDict 로 만들지 않습니다 — 챗 이벤트 키가 자주 변하기 때문.
- 함수 반환은 가능하면 명시적으로 (`-> dict[str, Any]`, `-> str | None`, `-> None`). 헬퍼라도 마찬가지.

---

## 5. 에러 처리 & 로깅

### 5.1 외부 호출 = `try/except` + 폴백 + 로그

LLM·Calendar·Firestore 등 **모든 외부 I/O** 는 try/except 로 감싸고, 실패 시 사용자에게는 한국어 안내 문자열을, 로그에는 원인을 남깁니다.

```python
try:
    res = _get_generative_model().generate_content(prompt)
    out = (res.text or "").strip()
    if not out:
        out = "지금은 답변을 만들지 못했어요. 조금 다시 말씀해 주시겠어요?"
except Exception as e:
    logger.warning("daily_chat Gemini 호출 실패, 폴백 사용: %s", e)
    out = "[데모 모드] 일상 대화로 받았어요. ..."
```

- **분류 실패 = `False` 폴백**. 분류기(`_yes_no_classify`)는 예외 시 안전한 기본값(예: 일반 대화 흐름)으로 빠지게 합니다.
- **HTTP 핸들러의 최상위 try** 는 `logger.exception` 으로 스택까지 남기고, 사용자에게는 "처리 중 오류가 발생했습니다" 류 메시지만 보냅니다 (`main.py` 의 `CARD_CLICKED` 핸들러 참고).

### 5.2 logger 는 모듈마다 1개

```python
logger = logging.getLogger(__name__)
```

`print()` 는 `tests/*` CLI 스크립트에서만 사용. 운영 코드는 모두 `logger.{info,warning,exception}` 로 통일.

---

## 6. Google Chat 응답 dict

### 6.1 반환 스키마 화이트리스트

`main.py` 가 그대로 JSON 직렬화하므로, 핸들러 반환 dict 의 최상위 키는 **Google Chat REST `Message` 스키마** 만 허용됩니다: `text`, `cardsV2`, `accessoryWidgets`, `actionResponse`. 디버그용 `intent` 같은 키를 끼워 넣으면 챗 클라이언트가 침묵 실패합니다.

### 6.2 카드 빌더 패턴 (cards.py)

새 카드 빌더는 항상 `_wrap_card` 를 거치고, 키워드 인자 `include_action_response` 를 노출합니다.

```python
def build_<feature>_card(
    teams: list[dict[str, str]],     # 데이터 인자(있으면)
    *,
    include_action_response: bool = False,
) -> dict[str, Any]:
    widgets = [
        {"textParagraph": {"text": "<b>제목</b><br>설명"}},
        {"selectionInput": {"name": "team_id", "label": "팀 선택", "type": "DROPDOWN", "items": _team_items(teams)}},
        {"buttonList": {"buttons": [{"text": "확인", "onClick": {"action": {"function": "wm_<feature>_do"}}}]}},
        _menu_back_button(),  # 또는 _member_menu_back_button() 등
    ]
    return _wrap_card(
        "wm_<feature>",
        {"title": "AllMeet", "subtitle": "<섹션> > <화면>"},
        widgets,
        include_action_response=include_action_response,
    )
```

규칙:
- `cardId` 는 `"wm_<feature>"` 같은 영문 소문자 + 언더스코어.
- 헤더는 항상 `{"title": "AllMeet", "subtitle": "<경로>"}` 형태로 위치를 보여줍니다.
- 사용자 입력값을 텍스트로 끼울 때는 **반드시 `html.escape`**.
- 뒤로 가기 버튼은 헬퍼 (`_menu_back_button`, `_member_menu_back_button`) 를 재사용.

### 6.3 액션 결과 응답 패턴 (handler.py)

```python
# 카드 -> 텍스트 안내로 교체
return {
    "actionResponse": {"type": "UPDATE_MESSAGE"},
    "text": f"✅ 팀이 추가되었습니다: {team_name} (config/{team_id})",
}

# 카드 -> 새 카드로 교체
return build_team_list_card(get_team_list(), include_action_response=True)

# 검증 실패 -> 그냥 짧은 텍스트 (UPDATE_MESSAGE 안 붙임 = 새 메시지로 안내)
return {"text": "팀 ID는 영문 소문자/숫자/_/- 형식으로 입력해 주세요."}
```

성공 메시지는 `"✅ ..."` 로 시작, 실패 메시지는 이모지 없이 무엇을 어떻게 고쳐야 하는지 한 문장.

### 6.4 폼 입력 추출 패턴

폼 값은 항상 `{"stringInputs": {"value": [...]}}` 로 옵니다. 직접 풀어 쓰지 말고 `_safe_form_value` 같은 헬퍼를 쓰세요.

```python
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

# 사용
team_id = _safe_form_value(form_inputs, "team_id")
team_name = _safe_form_value(form_inputs, "new_team_name")
```

dropdown 의 "선택 안 함" 은 `"__none__"` 으로 들어오므로 `_safe_team_id` 가 빈 문자열로 정규화합니다.

---

## 7. Firestore 호출 패턴

### 7.1 raw 클라이언트 대신 헬퍼

`firestore/documents.py` 의 `ensure_document`, `append_subcollection_docs`, `list_subcollection_recent_chronological` 를 거치세요. 트랜잭션이 정말 필요할 때만 `get_client()` 를 직접 사용합니다.

### 7.2 신규 vs 갱신 분기

```python
ref = db.collection("config").document(team_id)
snap = ref.get()
now = datetime.utcnow()

payload = {
    "team_name": team_name,
    "updated_at": now,
    ...
}
if snap.exists:
    ref.set(payload, merge=True)
else:
    payload["created_at"] = now    # 신규일 때만 created_at 추가
    ref.set(payload, merge=True)
```

`created_at` 은 **신규 생성에만**, `updated_at` 은 **항상** 갱신.

### 7.3 인덱스 동기화 (config/team_list)

팀 문서를 직접 만들거나 지우면 `config/team_list` 인덱스가 어긋납니다. 항상 다음 함수를 거치세요.

- 추가/수정: `upsert_team_config(...)` → 내부에서 `_upsert_team_list` 호출
- 이름 변경: `update_team_name(...)` → `rename_team_in_list` 호출
- 삭제: `delete_team(team_id)` → `remove_team_from_list` 호출

---

## 8. 테스트·운영 스크립트 (tests/)

`tests/` 는 pytest 가 아니라 **CLI 도구** 입니다. 새 스크립트를 만들 때 패턴:

```python
from __future__ import annotations

import argparse


def _action_<name>(...) -> ...:
    """한 화면 분량의 인터랙티브 작업."""
    ...


def main() -> None:
    parser = argparse.ArgumentParser(description="<무엇을 하는 스크립트>")
    parser.add_argument("--<flag>", action="store_true", help="...")
    args = parser.parse_args()
    if args.<flag>:
        _run_<flag>_mode()
    else:
        _run_interactive()


if __name__ == "__main__":
    main()
```

- 인터랙티브 분기는 `_action_*` 함수로 쪼개고, 메뉴 출력은 `_show_menu`, 선택은 `_pick_team` 같이 재사용 가능한 헬퍼로.
- `print()` 만 사용 (logger 안 씀).
- `_run_interactive()` 와 `_run_<flag>_mode()` 두 진입점을 두는 게 일반적.

---

## 9. 문자열 & 포매팅

- 사용자에게 보이는 모든 문자열 = **한국어**, 친근한 존댓말. 시스템 메시지는 짧게.
- 카드 본문 HTML 에 사용자 입력을 넣을 때는 **반드시 `html.escape`**.
- f-string 우선, `%` 포매팅은 `logger` 호출에서만 사용 (lazy 평가).

  ```python
  logger.info("intent=%s message=%s", intent.value, user_message[:200])  # OK
  logger.info(f"intent={intent.value}")                                  # 지양
  ```

- 긴 문자열은 암묵적 연결 사용:

  ```python
  WHAT_I_CAN_DO_TEXT = (
      "제가 잘하는 업무는 이런 게 있어요.\n\n"
      "• 💬 일상 대화 · 질문 답변\n"
      "• 🌐 최신 정보 ...\n"
  )
  ```

---

## 10. 새 기능을 추가할 때 체크리스트

새 의도(`UserIntent`) 를 추가하는 경우:
- [ ] `main.py` 의 `UserIntent` enum 멤버
- [ ] 우선순위에 맞춘 `_<name>_like` 술어 (덜 흔한 키워드 먼저)
- [ ] `_dispatch_by_intent` 의 `case`
- [ ] `domains/<name>/__init__.py` 에서 핸들러 re-export
- [ ] 핸들러 시그니처: `def handle_<name>(user_message: str, chat_event: dict[str, Any] | None = None) -> str | dict[str, Any]`

새 카드 액션(`wm_*`) 을 추가하는 경우:
- [ ] `cards.py` 에 `build_<feature>_card` 빌더
- [ ] `handler.py` 의 `handle_weekly_meeting_action` 에 `if invoked_function == "wm_..."` 분기
- [ ] 폼 입력은 `_safe_form_value` / `_safe_team_id` 로만 추출
- [ ] 응답은 §6.3 패턴 중 하나 선택
- [ ] 팀 데이터 변경 시 `upsert_team_config` 등 인덱스-안전 함수 사용

새 외부 API 연동을 추가하는 경우 (자세한 규약은 §11 참조):
- [ ] `api/<service>/` 에는 **얇은 CRUD 래퍼만** 둠 — 도메인 용어 금지
- [ ] 자격 증명은 `google.auth.default()` 또는 환경변수 (코드에 키 박지 않기)
- [ ] try/except + 한국어 폴백 메시지 + `logger.warning`/`exception`
- [ ] 결과는 `@dataclass` 로 (`LookupResult` 처럼 `ok`/`error_kind` 포함)
- [ ] 비즈니스 로직(검색어 조립·결과 필터·카드 가공)은 도메인 또는 별도 services 디렉토리에서 합성

---

## 11. 레이어 분리: `api/` vs `domains/` vs 별도 디렉토리

이 규약은 새 외부 연동을 추가할 때 **가장 먼저 결정해야 할 사항**입니다. "어떤 함수를 어디에 둘 것인가" 의 답은 다음 3가지뿐입니다.

### 11.1 `api/<service>/` — 얇은 CRUD 래퍼만

`api/calendar/`, `api/confluence/`, `api/drive/`, `api/gmail/` 등 **외부 서비스의 1:1 매핑** 을 두는 자리입니다.

여기 있어야 할 것:
- 인증·토큰 발급 (`_get_access_token` 류)
- 단일 endpoint 호출 + 응답 파싱
- 도메인 의미 없는 generic CRUD: `list_events()`, `get_event(event_id)`, `create_event(...)`, `patch_event(event_id, ...)`, `delete_event(event_id)`

여기 두면 안 되는 것:
- **도메인 키워드** (`주간회의`, `휴가`, `팀원`, `weekly_meeting`, `vacation` 등) — 함수명·매개변수·주석 어디에도 등장하면 안 됩니다.
- 검색어 자동 조립 ("팀명 + 주간회의" 같은 합성)
- 결과를 비즈니스 의미로 후처리 (휴가 키워드 필터링 같은 것)
- Google Chat 카드 dict 생성

함수 시그니처 예:
```python
# api/calendar/events.py — 좋은 예
def list_events(
    *,
    calendar_id: str,
    time_min: str,
    time_max: str,
    q: str | None = None,
    max_results: int = 30,
) -> ListEventsResult: ...

def get_event(*, calendar_id: str, event_id: str) -> EventResult: ...
def create_event(*, calendar_id: str, event: dict[str, Any]) -> EventResult: ...
def patch_event(*, calendar_id: str, event_id: str, patch: dict[str, Any]) -> EventResult: ...
def delete_event(*, calendar_id: str, event_id: str) -> DeleteResult: ...
```

```python
# api/calendar/events.py — 나쁜 예 (도메인 키워드가 들어왔음)
def list_weekly_meeting_events(team_name: str, ...) -> ...:  # ❌ 도메인 로직
def filter_vacation_events(...) -> ...:                       # ❌ 도메인 의미 부여
```

### 11.2 `domains/<feature>/` — `api/*` 를 합성해 비즈니스 로직 구성

도메인은 `api/*` 의 CRUD 함수들을 **import 해 호출** 하면서 비즈니스 의미를 부여합니다.

```python
# domains/weekly_meeting/schedule_lookup.py (이상적인 형태)
from api.calendar.events import list_events  # api 의 generic 함수만 호출

def lookup_weekly_meeting(*, team_name: str, is_next_week: bool, calendar_id: str) -> LookupResult:
    time_min, time_max = _week_range(is_next_week=is_next_week)
    return list_events(
        calendar_id=calendar_id,
        time_min=time_min,
        time_max=time_max,
        q=f"{team_name} 주간회의",   # ← 검색어 조립은 도메인의 책임
    )

def lookup_member_vacation(*, member_keywords: list[str], ...) -> LookupResult:
    # 키워드별 list_events 호출 + "휴가/연차/반차" 후처리도 도메인의 책임
    ...
```

도메인이 책임지는 것:
- 어떤 endpoint 를 어떤 인자로 부를지 결정 (검색어·기간·정렬 조립)
- 결과를 비즈니스 규칙으로 필터·정렬·집계
- 사용자에게 보여줄 카드(`cards.py`) / 텍스트로 가공
- Firestore 의 도메인 컬렉션 (`config/`, `conversations/`) 읽고 쓰기

### 11.3 별도 디렉토리 — **2개 이상 도메인이 같은 합성을 쓸 때만**

도메인-중립적이지만 raw API 보다는 비즈니스 의미가 살짝 있는 합성(예: "캘린더에서 한 주 범위로 이벤트를 가져오는 로직")이 **여러 도메인에서 반복** 되면, 그때 비로소 새 디렉토리를 만듭니다.

```text
all-meet/
  api/           ← 얇은 외부 서비스 래퍼
  services/      ← (예) 여러 도메인이 공유하는 합성 로직
    scheduling/
  domains/       ← 의도별 비즈니스 로직
```

규칙:
- **첫 번째 공유 사례가 등장하기 전에 만들지 않습니다.** 한 도메인만 쓰는 합성은 그냥 그 도메인 폴더 안 모듈로 둡니다 (현재 `domains/weekly_meeting/schedule_lookup.py` 처럼).
- 새 디렉토리를 추가하려면 `ARCHITECTURE.md` 의 폴더 구조 다이어그램을 함께 갱신합니다.
- `services/<name>/` 자체도 §11.1 의 "도메인 키워드 금지" 규칙을 따릅니다 — 도메인 키워드가 들어가는 순간 그 모듈은 도메인으로 옮겨야 합니다.

### 11.4 의사결정 플로우차트

```text
새 함수를 추가할 때
└─ 외부 서비스 호출 그 자체인가? (인증·endpoint·파싱만)
    ├─ 예 → api/<service>/  (도메인 키워드 금지)
    └─ 아니오 → 비즈니스 의미가 있는가?
        ├─ 한 도메인만 사용 → domains/<feature>/<module>.py
        └─ 2개 이상 도메인이 공유 → services/<name>/  (디렉토리 신설)
```

### 11.5 현재 저장소의 상태 (참고)

- `domains/weekly_meeting/schedule_lookup.py` 는 현재 Calendar REST 를 **직접** 호출합니다. 차후 `api/calendar/events.py` 가 만들어지면 generic CRUD 부분(`_get_access_token`, `_calendar_list_events`)을 그쪽으로 옮기고, `lookup_weekly_meeting` / `lookup_member_vacation` 만 도메인에 남기는 것이 §11 규약의 목표 형태입니다.
- `api/calendar/`, `api/confluence/`, `api/drive/`, `api/gmail/` 폴더는 현재 README 만 있고 비어 있습니다 — 새 외부 연동을 추가할 때 이 폴더를 채워 가세요.
