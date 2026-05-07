# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **언어 규칙:** 사용자와의 대화·코드 주석·문서·봇 응답 문구는 모두 **한국어**로 작성합니다. 식별자(패키지/함수/변수명)만 ASCII `snake_case`를 사용합니다.

## 프로젝트 개요

`all-meet-agent` — Functions Framework 기반의 Google Chat 봇이며 Cloud Run 에 배포됩니다. 단일 HTTP 엔드포인트(`hello_http`)가 사용자 메시지를 `UserIntent` 로 분류해 4개 도메인 핸들러 중 하나로 분기합니다. 코드 주석과 봇 응답은 모두 한국어입니다 — 새 문자열·주석을 작성할 때도 한국어 톤을 유지하세요.

## 자주 쓰는 명령

Cloud Run 배포 (소스 빌드):
```bash
gcloud run deploy all-meet-agent --source . --region asia-northeast3 --allow-unauthenticated
```
- 프로젝트: `ai-agent-test-482706`
- 런타임: `python-3.12` (`runtime.txt`)
- Procfile: `functions-framework --target hello_http --port $PORT`

로컬 HTTP 서버 (Procfile 과 동일):
```bash
functions-framework --target=hello_http --debug --port=8080
```

운영용 스크립트 (pytest 스위트는 없습니다 — 아래는 CLI 도구):
- `python -m tests.manage_team_members` — Firestore 의 팀/팀원 목록을 인터랙티브하게 조작. `--list` 는 첫 팀의 팀원만 출력.
- `python -m tests.test_weekly_meeting_schedule [--next]` — 하드코딩된 샘플 일정 출력 (Firestore/Calendar 호출 없음).

## 아키텍처

### 요청 흐름 (`main.py`)
1. `hello_http` 가 JSON 본문을 파싱하고 `payload["type"]` 으로 분기합니다.
   - `ADDED_TO_SPACE` → `welcome_with_capabilities_text()` 반환.
   - `CARD_CLICKED` → `common.invokedFunction`(또는 `action.function`) 을 읽어 `wm_` 로 시작하면 `domains.weekly_meeting.handle_weekly_meeting_action` 으로 위임. payload 에서 추출한 `parameters` / `formInputs` 도 함께 전달. 그 외 prefix 는 안내 텍스트만 반환.
   - `MESSAGE` 또는 단순 POST(`{text|user_message|query|message}`) → `match_user_intent` → `_dispatch_by_intent`.
2. `match_user_intent` 는 키워드/정규식 기반이며 우선순위가 있습니다: `WEEKLY_MEETING` > `SCHEDULE_MANAGEMENT` > `EXPERT_FINDER` > 폴백 `DAILY_CHAT`. 덜 흔한 키워드를 먼저 매칭해 오탐을 줄이는 구조이므로, 패턴을 추가할 때도 이 순서를 깨지 마세요.
3. **응답 스키마 제약:** Google Chat 동기 응답은 REST `Message` 스키마(`text`, `cardsV2`, `accessoryWidgets`, `actionResponse`)만 인정합니다. 디버그용 키(예: `intent`)를 끼워 넣으면 HTTP 200 이어도 챗 클라이언트에서 "응답하지 않음" 으로 처리됩니다. 진단 정보는 `logger.info` 로만 남기고, 응답 dict 에는 절대 넣지 마세요.

### 도메인 (`domains/<name>/`)
- `daily_chat` — 실제 구현 완료. `chat.reply_daily_chat` 흐름:
  1. Firestore `conversations` 컬렉션에서 최근 대화를 로드해 `ctx_block` 구성.
  2. LLM yes/no 분류기(`_yes_no_classify`)로 (a) 봇 능력 질문이면 고정 `WHAT_I_CAN_DO_TEXT` 반환, (b) 실시간 정보가 필요하면 `google-genai` + `GoogleSearch` 도구로 검색 답변.
  3. 그 외에는 Vertex `GenerativeModel` 에 최근 대화 블록을 포함한 프롬프트로 호출.
  4. 이번 턴(user/assistant) 을 Firestore 에 기록.
  - 모델 기본값은 `gemini-2.0-flash-001`, `ALLMEET_CHAT_MODEL` 환경변수로 덮어쓸 수 있음. 모델 클라이언트는 `_get_generative_model()` 로 lazy 싱글톤.
- `weekly_meeting` — 카드 기반 플로우 전체 구현. `handler.handle_weekly_meeting_action` 은 `invoked_function` 에 대한 평면적 디스패치 테이블이며 `wm_*` 액션을 모두 다룹니다: 일정 조회(이번주/다음주 주간회의 + 팀원 휴가, Calendar API), 팀 CRUD, 팀원 CRUD/순서 변경, 컨플루언스 설정(스페이스 키, 루트 페이지, 템플릿), 스케줄러 테스트. 카드는 `cards.py` 의 `_wrap_card` 로 빌드하며, `include_action_response=True` 를 넘기면 `{"actionResponse": {"type": "UPDATE_MESSAGE"}}` 가 붙어 챗 클라이언트가 기존 카드를 **추가가 아니라 교체** 합니다.
- `expert_finder`, `schedule_management` — 샘플 카드 스텁만 존재. 의도 라우팅 확인용이므로 실제 로직 구현 시 통째로 교체하면 됩니다.

### 캘린더 연동 (`domains/weekly_meeting/schedule_lookup.py`)
`google.auth.default()` 로 `calendar.readonly` 스코프의 베어러 토큰을 발급받아 `urllib` 로 Calendar v3 REST API 를 직접 호출합니다. `_week_range` 는 UTC 월요일 기준으로 한 주 구간을 잡습니다. `lookup_member_vacation` 은 팀원 키워드(이름·닉네임)별로 검색을 1번씩 보내고, 결과를 제목으로 후처리해 `휴가/연차/반차/vacation` 만 추립니다. `LookupResult.error_kind` 는 `calendar_not_found`(404) 또는 일반 오류 문자열이며, `handler.py` 에서 이 값으로 분기합니다.

### Firestore (`firestore/`)
- `writes.get_client()` — 스레드 안전 싱글톤 `firestore.Client(project=PROJECT_ID)`.
- `documents.py` — 도메인 무관 공통 헬퍼: `ensure_document` (없으면 생성, 있으면 `updated_at` 만 병합), `append_subcollection_docs`, `list_subcollection_recent_chronological` (DESC 로 가져온 뒤 뒤집어 오래된 순 → 최신 순으로 반환). 트랜잭션이 꼭 필요한 게 아니라면 raw 클라이언트 호출 대신 이 헬퍼들을 거치세요.
- `team_config.py` — 주간보고용 스키마.
  - `config/{team_id}`: `team_name`, `team_members` (`[{name, nickname: [...]}]`), `space_id`, `confluence_space_key`, `root_pages` (`[{level, page_id}]`), `template_page_url`, `template_page_id`, `setup_completed`, `user_context`, `created_at`, `updated_at`.
  - 특수 문서 `config/team_list` 은 인덱스(`{teams: [{id, name}, ...]}`). `_upsert_team_list` / `rename_team_in_list` / `remove_team_from_list` 가 동기화하므로 팀 문서를 직접 쓰지 말고 반드시 `upsert_team_config` / `update_team_name` / `delete_team` 를 거치세요. 인덱스가 없을 때는 `get_team_list` 가 `config` 컬렉션을 스트리밍해 폴백을 만듭니다.
  - `team_id` 정규식: `[a-z0-9_-]+`. 저장 규칙: 신규는 `created_at`+`updated_at` 동시, 갱신은 `updated_at` 만. `user_context.department` 누락 시 `"미지정"` 으로 정규화.
- `conversations/{space_id_slug}` (`daily_chat` 사용) — slug 는 표시명이 있으면 정규화해 붙입니다(`conversation_doc_id` 참조). 서브컬렉션 `messages` 는 `{role, content, created_at}` 를 저장하고, `RECENT_LIMIT = 20` 건을 프롬프트 컨텍스트로 로드합니다.

### 설정 (`config/settings.py`)
`PROJECT_ID` 우선순위: `GOOGLE_CLOUD_PROJECT` → `GCLOUD_PROJECT` → `PROJECT_ID` → 리터럴 `"ai-agent-test-482706"`. `LOCATION` 기본값은 `us-central1` (Vertex 리전). Cloud Run 은 `GOOGLE_CLOUD_PROJECT` 를 자동 주입하고 로컬 실행은 보통 리터럴 폴백에 의존하므로 이 체인은 그대로 유지하세요.

## 작업 컨벤션

> 코드 스타일 · 주석 톤 · 함수 분리 · 카드 빌더/핸들러 템플릿 등 **세부 코딩 규약은 [`CONVENTIONS.md`](./CONVENTIONS.md)** 에 있습니다. 새 파일·함수를 추가하기 전에 먼저 그 문서의 해당 섹션을 확인하세요.

- **새 의도 추가:** `UserIntent` 에 멤버, 우선순위에 맞춘 `_xxx_like` 술어, `_dispatch_by_intent` 의 `case` 까지 함께 추가합니다. 도메인 코드는 `domains/<name>/` 아래에 두고 `__init__.py` 에서 진입점을 re-export.
- **새 카드 액션 추가:** `handle_weekly_meeting_action` 의 디스패치에 `wm_*` 함수명을 더하고 `cards.py` 에 빌더를 추가합니다. 폼 값은 `{stringInputs: {value: [...]}}` 형태로 들어오므로 `_safe_form_value` 로 꺼내세요. `{"actionResponse": {"type": "UPDATE_MESSAGE"}, "text": ...}` 를 반환하면 카드를 텍스트 안내로 교체하고, `build_*(..., include_action_response=True)` 로 새 카드를 반환하면 카드를 카드로 교체합니다.
- **응답에 디버그 키를 넣지 않기.** Google Chat REST `Message` 스키마 외 키는 침묵 실패의 원인입니다. 진단은 로그로만.
- **시크릿은 코드에 두지 않기.** `.gcloudignore` 가 `.env*`, `key.json`, `*.pem` 등을 배포 번들에서 이미 제외합니다. 런타임 자격 증명은 Cloud Run 기본 서비스 계정에 의존하고, 토큰이 필요할 때는 `google.auth.default()` 로 발급받습니다.
