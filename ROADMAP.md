# 외부 데이터 수집 로드맵 (`api/` 폴더 구축 + 주간보고초안 함수)

> "all-meet-agent 가 Calendar / Drive / Gmail / Confluence 에서 사용자별 한 주 활동 데이터를 모아 주간보고 초안을 자동 생성한다" 는 장기 비전의 **1단계 — `api/` 폴더에 generic CRUD 함수 + 첫 합성 함수(`주간보고초안`) 까지** 의 계획서. DWD 사용 여부 의사결정의 배경은 `DWD_GUIDE.md` 참조.

---

## Context

### 비전

스케줄러가 매주 자동으로 실행되어, 센터 팀원 리스트를 FOR 문으로 돌면서 각자의 한 주 활동(Calendar 회의 · Drive 파일 · Gmail 메일 · Confluence 페이지 작성/수정 이력)을 수집하고, 키워드 검색으로 "누가 무엇을 만들었는가" 까지 추적해 주간보고 초안을 자동 생성한다. 1차 목표는 그 데이터 수집 토대 + 첫 합성 함수(테스트 트리거 `주간보고초안`).

### 인증 결정 (확정)

| 서비스 | "키 한 번 등록" 모델 가능? | 1차 인증 방식 |
|---|---|---|
| Drive | ◯ | SA + Shared Drive 멤버 등록 |
| Confluence | ◎ | 봇 계정(`silverain@vntgcorp.com`) API token + email Basic auth. 토큰은 `config/team_list.global.api_token` 에 이미 저장됨 |
| Calendar (공용) | △ | 팀 공용 캘린더만 SA 공유 |
| Gmail | ✗ | **팀원별 OAuth refresh_token** — 옵션 B 하이브리드 (DWD 사용 불가 확정) |
| Calendar (개인) | ✗ | Gmail 과 동일 — OAuth `calendar.readonly` 스코프 같이 묶음 |

→ Gmail 과 개인 Calendar 만 OAuth 동의 트랙에 묶이고 Drive/Confluence/공용 Calendar 는 SA + 글로벌 토큰만으로 즉시 시작 가능 → **단계 분리**.

### Firestore 데이터 모델 (실제 운영 중 — 스크린샷 확인 기반)

```
config/
  team_list                                 ← 글로벌 + 팀 인덱스 (한 문서)
    global:                                 ← 모든 팀이 공유
      api_token                "ATATT3..."  ← Atlassian Cloud API token
      confluence_url           "https://vntg.atlassian.net"
      user_email               "silverain@vntgcorp.com"   ← Atlassian 봇 계정
      page_restrict_account_id, page_restrict_to_email
    teams: [
      { id: "PC2",  name: "PC2팀",  calendar_id, space_key, report_root_page_id, template_page_id, ... },
      { id: "MES2", name: "MES2팀", ... },
      ...
    ]
  PC2                                       ← 팀별 멤버 상세
    setup_completed: false
    team_members: [
      { email: "gwangjun.park@vntgcorp.com", name: "박광준", nickname: ["Jun", "광준", "준"] },
      { email: "e50271@vntgcorp.com",        name: "김태표", nickname: ["태표"] },
      { email: "hyeongi.hong@vntgcorp.com",  name: "홍현기", nickname: [...] },
      ...
    ]
    created_at
  MES2
    ...
  oauth_tokens/                             ← (Phase 2 신설)
    {user_email}:
      refresh_token
      scopes
      granted_at
      last_refreshed_at
      status                                ← "linked" | "expired" | "revoked"
```

**입력 흐름**:
- 사용자 이메일 → 팀 찾기: `config/PC2.team_members[].email`, `config/MES2.team_members[].email` ... 순회 매칭 → `team_id` 회수.
- 팀 → 캘린더/스페이스 정보: `config/team_list.teams[i]` 에서 `calendar_id`, `space_key` 등 추출.
- API token: `config/team_list.global.api_token` 한 곳에서 (글로벌 1개). 코드는 `get_token_for(user_email)` 인터페이스로 추상화 — 미래에 팀별로 나뉘어도 함수만 교체.

### 한 주 범위 정의

`주간보고초안` 함수가 데이터를 수집할 기간은 — **회의 일자가 속한 주 (이번주) + 그 직전 주 (전주)**, 합계 **두 주치 (14일)**, **KST 월~일 기준**.

```
예) meeting_date = 2026-05-07 (목요일, KST)
    이번주: 2026-05-04 00:00 KST  ~ 2026-05-10 23:59 KST  (월~일)
    전주:   2026-04-27 00:00 KST  ~ 2026-05-03 23:59 KST  (월~일)
    수집 범위: 2026-04-27 00:00 KST  ~ 2026-05-10 23:59 KST
```

회의가 미뤄져도 두 주치라 안전 흡수. 코드는 `_kst_two_weeks_around(meeting_date)` 헬퍼.

기존 `domains/weekly_meeting/schedule_lookup._week_range` 는 UTC 월요일 기준. **Phase 1 막바지에 KST 옵션을 받게 리팩터** (회귀 안전을 위해 옵션 인자, 기존 호출은 UTC default 유지).

### 응답 방식 — (A) 즉시 안내 + 백그라운드 푸시

`주간보고초안` 트리거가 들어오면 —

1. `hello_http` 가 즉시 `{"text": "주간보고초안 분석 중이에요, 잠시 후 결과를 보내드릴게요"}` 200 OK 반환 (1초 안).
2. 같은 Cloud Run 인스턴스에서 `threading.Thread(target=run_draft, daemon=False)` 로 백그라운드 실행.
3. 백그라운드 작업: 데이터 수집 → Vertex Gemini 분석 → 결과 카드 빌드.
4. Google Chat REST API (`spaces.messages.create`) 로 같은 스페이스에 결과 메시지 push.

**필요한 인프라**:
- Cloud Run 배포 시 `--cpu-always-allocated` 플래그 (응답 후 CPU 살아있어야 백그라운드 thread 가 동작). 기본 `--cpu-throttling` 모드면 응답 직후 CPU 정지.
- 봇 SA 가 `https://www.googleapis.com/auth/chat.bot` 스코프 보유 (Cloud Run 기본 SA 또는 Phase 1 의 `allmeet-bot` SA).

**한계**: Cloud Run 인스턴스가 갑자기 종료되면(스케일다운, 새 배포) 진행 중이던 분석이 사라짐. MVP 는 수용. 늘어나면 Cloud Tasks 큐로 마이그레이션.

### 작성자 매칭 — email only

Drive `lastModifyingUser.emailAddress`, Confluence `version.by.email`, Gmail `from.email` 모두 `team_members[].email` 과 정확 매칭. **`name` / `nickname` 매칭은 사용 안 함** (사용자 명시).

### Vertex AI

`gemini-2.0-flash-001` 기본 (이미 daily_chat 에서 사용 중). 환경변수 `ALLMEET_DRAFT_MODEL` 로 override 가능. JSON mode (`response_schema`) 로 구조화된 초안 출력 — `{meetings: [...], documents: [...], pages: [...], emails: [...], summary: "..."}`.

---

## 단계 구성

### Phase 1 — SA + 글로벌 토큰으로 가벼운 길

- 인증 모델: Service Account JSON 키 1개 + 글로벌 Atlassian token (`config/team_list.global.api_token`).
- 적용 서비스:
  - **Drive** — 센터 Shared Drive 에 SA 이메일 View 멤버 추가. 그 드라이브의 모든 파일·작성자·수정 이력 검색 가능.
  - **Confluence** — `config/team_list.global.{api_token, confluence_url, user_email}` 사용. 팀별 `space_key` 는 `config/team_list.teams[i]`.
  - **Calendar (공용 한정)** — 팀별 `config/team_list.teams[i].calendar_id` 캘린더에 SA 공유. 개인 캘린더는 Phase 2 로.
- 결과물:
  - `api/drive/files.py`, `api/confluence/pages.py`, `api/calendar/events.py` generic CRUD
  - `api/_auth/service_account.py`, `api/_auth/confluence_basic.py` 헬퍼
  - `firestore/team_config.py` 에 `get_global_config()`, `find_team_by_email(email)`, `get_token_for(user_email)` 추가
  - `domains/weekly_report/draft.py` 합성 함수 (`주간보고초안` 트리거)
  - `api/chat/messages.py` Chat REST 푸시 헬퍼
- 검증 가능한 가치: `주간보고초안` 트리거로 한 사용자 한 주 데이터 (Gmail 제외) 가 모인 카드가 푸시됨.

### Phase 2 — 옵션 B 하이브리드 토큰으로 Gmail · 개인 Calendar 통합

DWD 사용 불가 확정. 각 팀원이 챗에서 한 번 동의 클릭 → 봇이 `refresh_token` 을 Firestore 에 저장 → 스케줄러가 그 토큰으로 access_token 재발급.

**옵션 B 하이브리드 모델 (확정)**:
- **토큰 본체** = `oauth_tokens/{user_email}` 한 컬렉션에만 저장. 갱신 시 한 곳만 업데이트.
- **`team_members[].oauth_status`** 필드를 멤버에 추가 (`null` | `"linked"` | `"expired"` | `"revoked"`). UI 카드에서 "누가 연결됨" 빠르게 표시.
- **sync 헬퍼** `firestore/team_member_oauth_sync.py` — 동의/철회 이벤트 시 그 이메일이 속한 모든 팀의 멤버 status 만 동기화. 토큰 자체는 sync 안 함.
- 신규 멤버 추가 시 `oauth_status = null` 초기화. 그 사람이 이미 다른 팀에서 동의돼 있으면 `"linked"` 로 자동.

**인프라 작업**:
1. GCP Console → APIs & Services → OAuth consent screen (User type = **Internal**, vntgcorp.com 내부)
2. 허용 스코프: `gmail.readonly`, `calendar.readonly`
3. OAuth 2.0 Client ID (Web application) → Redirect URI = `<Cloud Run URL>/oauth/callback`
4. client_id / client_secret → Secret Manager
5. Firestore 보안 규칙 — `oauth_tokens` 는 봇 SA 만 read/write

**코드 작업**:
- `api/_auth/user_oauth.py` — refresh_token 으로 access_token 재발급, 만료/거부 예외 매핑
- `firestore/oauth_tokens.py` — `oauth_tokens/{email}` read/write 헬퍼
- `firestore/team_member_oauth_sync.py` — status 동기화
- `main.py` 에 `/oauth/callback` GET 엔드포인트
- 카드 액션 `wm_oauth_link` — "내 데이터 연결" 버튼
- `api/gmail/messages.py` 채우기
- `api/calendar/events.py` 의 개인 캘린더 호출 분기에서 `user_oauth` credentials 사용
- `domains/weekly_report/draft.py` 가 OAuth 미동의 팀원은 메일/개인 캘린더만 빠뜨리고 진행 (부분 결손 허용)

**운영**:
- 신규 팀원 합류 시 `wm_tm_do_register_member` 직후 "OAuth 연결" 카드 자동 발송 (검토)
- API 호출이 `auth_required` 로 실패하면 그 사람에게 재동의 안내 카드 자동 발송

---

## 새 테스트 함수 — `주간보고초안`

### 트리거

채팅에서 사용자가 `주간보고초안` (또는 임시로 `A함수호출`) 메시지 전송 시 매칭. `main.py` 의 `_dispatch_by_intent` 에 `UserIntent.WEEKLY_REPORT_DRAFT` 추가, `_weekly_meeting_like` 보다 먼저 매칭.

### 파이프라인 (의사 코드)

```python
def handle_weekly_report_draft(user_message, chat_event):
    # 0. 즉시 응답 (Cloud Run 동기 응답)
    space_name = chat_event["space"]["name"]   # "spaces/m0g1TiAAAAE"
    user_email = chat_event["user"]["email"]
    threading.Thread(target=_run_in_background, args=(space_name, user_email), daemon=False).start()
    return {"text": "주간보고초안 분석 중이에요, 잠시 후 결과를 보내드릴게요"}

def _run_in_background(space_name, user_email):
    # 1. 사용자 → 팀 (firestore.team_config.find_team_by_email)
    team_id = find_team_by_email(user_email)         # "PC2"
    if not team_id:
        post_message_to_space(space_name, {"text": "소속 팀을 찾을 수 없습니다. 팀에 email 등록 필요"})
        return
    team = get_team_from_index(team_id)              # config/team_list.teams[i]
    members = get_team_config(team_id)["team_members"]
    user = next((m for m in members if m["email"] == user_email), None)

    # 2. 주간회의 일자 찾기 (KST 기준 ±30일 내 가장 가까운 미래/최근)
    meetings = api.calendar.events.list_events(
        calendar_id=team["calendar_id"],
        time_min=now_kst() - 30d,
        time_max=now_kst() + 7d,
        q="주간회의",
    )
    meeting_date = pick_nearest(meetings)            # 가장 가까운 회의일

    # 3. 한 주 범위 = 회의주 + 전주 (KST 월~일)
    time_min, time_max = _kst_two_weeks_around(meeting_date)

    # 4. raw 병렬 수집 (서비스별 try/except 격리)
    results = {
        "calendar":   safe_call(api.calendar.events.list_events, calendar_id=team["calendar_id"], time_min, time_max),
        "drive":      safe_call(api.drive.files.list_files, modified_by=user_email, time_min, time_max),
        "confluence": safe_call(api.confluence.pages.list_pages, space_key=team["space_key"], modified_by=user_email, time_min, time_max),
        "gmail":      safe_call(api.gmail.messages.list_messages, user_email, time_min, time_max),  # Phase 2
    }

    # 5. (선택) 핵심 항목 본문 일부 fetch — Confluence 페이지 N개, Drive 문서 N개 export
    #    이 단계는 Vertex 분석 정확도 ↑ but 비용·시간 ↑

    # 6. Vertex Gemini (gemini-2.0-flash) JSON mode 호출
    draft = vertex.generate_content(
        prompt=build_draft_prompt(user, meeting_date, results),
        response_schema={
            "meetings":  [{"summary", "date"}],
            "documents": [{"title", "modified_at", "type"}],          # type: "매뉴얼"|"회의록"|"기타"
            "pages":     [{"title", "modified_at", "type"}],
            "emails":    [{"subject", "sent_at", "counterparty"}],    # Phase 2
            "summary":   "한 단락 한국어 종합 초안"
        },
    )

    # 7. 결과 카드 빌드 + Chat REST API 로 push
    card = build_draft_card(draft)
    post_message_to_space(space_name, card)
```

### 도메인 키워드 분리 (CONVENTIONS.md §11)

`api/*` 모듈은 **도메인 키워드 금지** — `주간회의`, `휴가`, `팀원` 같은 단어 함수명·매개변수·주석에 등장 X. 그 합성은 모두 `domains/weekly_report/draft.py` 에서.

---

## 코드 구조

```
api/
  _auth/
    service_account.py     # Phase 1: SA 키 로드, scope 별 credentials
    confluence_basic.py    # Phase 1: confluence_url + user_email + api_token Basic auth
    user_oauth.py          # Phase 2: 사용자 OAuth refresh_token 으로 credentials
  calendar/
    __init__.py
    events.py              # list_events, get_event ...
  drive/
    __init__.py
    files.py               # list_files, get_file_metadata, search_by_query ...
  confluence/
    __init__.py
    pages.py               # list_pages, get_page, list_pages_modified_by ...
  gmail/                   # Phase 2
    __init__.py
    messages.py
  chat/
    __init__.py
    messages.py            # post_message_to_space() — Google Chat REST API 푸시

domains/
  weekly_report/           # 신설
    __init__.py
    draft.py               # 주간보고초안 합성 함수 (위 파이프라인)
    prompts.py             # Vertex 프롬프트 템플릿
    cards.py               # 결과 카드 빌더
    timewindow.py          # _kst_two_weeks_around, KST 계산 헬퍼

firestore/
  team_config.py           # 수정: get_global_config, find_team_by_email, get_token_for 추가
  oauth_tokens.py          # Phase 2 신설
  team_member_oauth_sync.py  # Phase 2 신설
```

---

## 변경/추가될 파일

**신규 (Phase 1):**
- `api/_auth/__init__.py`, `api/_auth/service_account.py`, `api/_auth/confluence_basic.py`
- `api/calendar/__init__.py`, `api/calendar/events.py`
- `api/drive/__init__.py`, `api/drive/files.py`
- `api/confluence/__init__.py`, `api/confluence/pages.py`
- `api/chat/__init__.py`, `api/chat/messages.py`
- `domains/weekly_report/__init__.py`, `draft.py`, `prompts.py`, `cards.py`, `timewindow.py`

**신규 (Phase 2):**
- `api/_auth/user_oauth.py`
- `api/gmail/__init__.py`, `api/gmail/messages.py`
- `firestore/oauth_tokens.py`
- `firestore/team_member_oauth_sync.py`

**수정:**
- `main.py` — `UserIntent.WEEKLY_REPORT_DRAFT` 추가, `_dispatch_by_intent` 분기, (Phase 2) `/oauth/callback` 엔드포인트
- `config/settings.py` — 새 환경변수 추가
- `requirements.txt` — 새 라이브러리 추가
- `firestore/team_config.py` — `get_global_config()`, `find_team_by_email(email)`, `get_token_for(user_email)`, `get_team_from_index(team_id)` 추가
- `domains/weekly_meeting/schedule_lookup.py` — 기존 `_calendar_list_events` / `_get_access_token` 을 `api.calendar.events.list_events` 호출로 점진 교체. `_week_range` 에 KST 옵션 추가.
- (Phase 2) `domains/weekly_meeting/cards.py` — "OAuth 연결" 카드 빌더
- (Phase 2) `domains/weekly_meeting/handler.py` — `wm_oauth_link` 디스패치
- `ARCHITECTURE.md` — 새 폴더 구조 반영
- `CONVENTIONS.md` §11.5 — Phase 1 완료 시점에 schedule_lookup 메모 갱신

---

## 환경변수 / 의존성

`config/settings.py` 추가:
- `BOT_SA_KEY_SECRET_NAME` — Secret Manager 의 SA 키 이름 (또는 ADC 사용 시 미사용)
- `ALLMEET_DRAFT_MODEL` — Vertex 모델 (default `gemini-2.0-flash-001`)
- (Phase 2) `OAUTH_CLIENT_ID_SECRET_NAME`, `OAUTH_CLIENT_SECRET_SECRET_NAME`, `OAUTH_REDIRECT_URI`

> Confluence 글로벌 설정 (`api_token`, `confluence_url`, `user_email`) 은 환경변수 X — `config/team_list.global` 에서 직접 read.

`requirements.txt` 추가:
- `google-auth>=2.0`
- `google-api-python-client>=2.0`
- `google-cloud-secret-manager>=2.0`
- `requests>=2.30` (Confluence REST + Chat REST 푸시)
- (Phase 2) `google-auth-oauthlib>=1.0`

---

## 재사용할 기존 코드

- `domains/weekly_meeting/schedule_lookup.py` 의 `_week_range`, `_get_access_token`, `_calendar_list_events` — `api/calendar/events.py` 로 옮기면서 generic 화. `_get_access_token` 만 SA credentials 기반으로 교체.
- `LookupResult` dataclass 패턴 (`ok`, `events`/`items`, `error_kind`) — 모든 `api/*` 모듈 결과 객체. `error_kind` 후보: `not_found`, `http_error`, `auth_error`, `rate_limited`, `auth_required`(OAuth 미동의).
- `firestore.documents` 의 헬퍼 — 직접 `.get()` 대신 그대로 활용.
- `domains/daily_chat/chat.py` 의 `_get_generative_model()` 싱글톤 패턴 — `domains/weekly_report/draft.py` 에서 동일 패턴으로 Vertex client.

---

## Prerequisites (현 상태 반영)

### Phase 1 시작 전

| # | 작업 | 현재 상태 |
|---|---|---|
| 1 | GCP Service Account 생성 (`allmeet-bot@ai-agent-test-482706.iam.gserviceaccount.com` 또는 유사) | **확인 필요** — IAM Console |
| 2 | SA JSON 키 발급 → Secret Manager 저장 | **확인 필요** — Secret Manager |
| 3 | 센터 Shared Drive 에 SA 이메일을 View 멤버로 추가 | **확인 필요** — drive.google.com → 공유 드라이브 |
| 4 | Confluence 봇 계정 API token 발급 + 저장 | **완료** — `config/team_list.global.api_token` 에 저장됨. 정석 Secret Manager 마이그레이션은 추후 보안 섹션에서 |
| 5 | 팀별 `config/team_list.teams[i].calendar_id` 캘린더에 SA 공유 (일정 세부정보 보기) | **확인 필요** — calendar.google.com 각 캘린더 설정 |

### Phase 2 추가

6. GCP Console → OAuth consent screen 설정 (User type = Internal). 스코프 `gmail.readonly` + `calendar.readonly`.
7. OAuth 2.0 Client ID (Web application) 생성. Redirect URI = `<Cloud Run URL>/oauth/callback`. client_id/secret → Secret Manager.
8. Firestore 보안 규칙 — `oauth_tokens` 컬렉션은 봇 SA 만 read/write.

### 배포 단계 추가

9. Cloud Run 배포 시 `--cpu-always-allocated` 플래그 (백그라운드 thread 동작에 필수).

---

## Verification

### Phase 1

- `python -m api.drive.files --user "user@vntgcorp.com" --since 2026-04-30 --until 2026-05-07` — 단독 호출, JSON 출력으로 파일 메타데이터 확인.
- `python -m api.confluence.pages --user "user@vntgcorp.com" --since 2026-04-30` 동일.
- `python -m api.chat.messages --space "spaces/XXX" --text "테스트"` — Chat REST 푸시 정상 동작.
- 기존 `wm_schedule_meeting_this` / `wm_schedule_meeting_next` 카드 액션 회귀 — 새 `api.calendar.events.list_events` 사용해도 동일 결과.
- Firestore 팀 데이터 dry-run — PC2 팀 한 명(`gwangjun.park@vntgcorp.com`) 을 입력으로 Drive/Confluence 호출 → 한 주치 메타데이터가 잡히는지.
- **End-to-end** — 챗에서 박광준 분 계정으로 `주간보고초안` 입력 → 즉시 "분석 중" 응답 → 30~60초 뒤 같은 스페이스에 결과 카드 푸시. Calendar/Drive/Confluence 섹션이 비어 있지 않은지.

### Phase 2

- 팀원 1명이 챗에서 `wm_oauth_link` 카드 클릭 → 브라우저 동의 → `/oauth/callback` 정상 처리되어 `oauth_tokens/{email}` 문서 생성.
- 그 사람이 속한 모든 팀의 `team_members[].oauth_status` 가 `"linked"` 로 동기화 (sync 헬퍼).
- `python -m api.gmail.messages --user "<동의한 이메일>" --since ...` — refresh_token 으로 access_token 재발급 후 메일 fetch.
- 미동의 팀원에 대해 `error_kind == "auth_required"` 로 빠지고, `주간보고초안` 결과 카드에 "이 팀원 메일 미연결" 표시.
- refresh_token 폐기 시뮬레이션 → 다음 호출 실패 → 그 사람에게 재동의 안내 카드 자동 발송.

---

## 가정 / 미해결 항목

- **VNTG 센터 자료가 Shared Drive 안에 정리되어 있음** — 사용자 확언. 일부가 "내 드라이브" 폴더 공유 형태면 폴더별 SA 공유가 추가로 필요.
- **`config/{team_id}.team_members[].email` 신뢰 가능** — 현재 PC2 문서에 들어 있음 확인. 신규 팀원 등록 시 (`wm_tm_do_register_member`) email 받게 되어 있는지는 별도 점검 필요 (현재는 `name` + `nickname` 만 받는 코드로 보임).
- **데이터 모델 변동 가능** — 사용자 시그널: `team_list.global` 위치, 팀별 필드 구성 (`space_key`, `report_root_page_id` 등) 이 수정 중. ROADMAP 의 모델 가정은 2026-05-07 기준 스크린샷이며, 코드 작성 시 한 번 더 확인.
- **Confluence = Atlassian Cloud** — `vntg.atlassian.net` 으로 사실상 확정. Server/DC 면 인증이 PAT 또는 Basic 으로 다름.
- **DWD 사용 불가 확정** — 해커톤 컨텍스트 + Workspace 관리자 협조 어려움. 자세한 검토는 `DWD_GUIDE.md`.
- **OAuth consent User type = Internal 가능 가정** — Workspace 도메인이라 internal 가능. External + Google 검수면 일정 추가 (수 주).
- **레이트 리밋** — MVP 는 코드 안 짜고 가정 항목으로만. 팀 5개 이상 늘면 백오프 헬퍼 (`api/*/_retry.py`) 추가.
- **PII / 메일 본문 처리 (Phase 2)** — 1차는 메타(제목·시간·상대) 만. 본문은 별도 검토 후 진입. Vertex 가 사내 GCP 프로젝트라 외부 노출은 0이나, 로그/캐시 마스킹 정책은 별도 결정.
- **`template_page_id` URL 통째 저장** — 보류. 호출 측에서 기존 `parse_template_page_id` 로 매번 ID 추출 (이미 동작).
- **평문 Atlassian token (Firestore 평문)** — "보안 섹션 추후" 로 사용자 지시. Phase 1 은 그대로, 정석은 Secret Manager 이전.
- **응답 푸시 인스턴스 종료 위험** — Cloud Run 인스턴스 갑작스러운 종료(스케일다운, 새 배포) 시 진행 중 분석 사라짐. MVP 수용. Cloud Tasks 큐로 마이그레이션은 데이터량 많아지면.
- **Vertex AI 모델 — gemini-2.0-flash 기본** — 분석 깊이 부족하면 `gemini-1.5-pro` / `gemini-2.0-pro` 로 교체 (`ALLMEET_DRAFT_MODEL` 환경변수).
- **봇 운영 계정 단일 의존점** — Atlassian/Drive/Calendar 모두 silverain 분 계정 또는 권한에 의존. 그 계정 회수/이탈 시 시스템 정지. backup 계정 또는 SA 일원화 검토 필요.
- **Cloud Scheduler 트리거 형태** — 본 ROADMAP 은 `주간보고초안` 챗 트리거 (수동) 기반. 실제 자동 스케줄러 (매주 금요일 18시 등) 는 Phase 2 이후 추가. Cloud Scheduler + Cloud Run HTTP 가 가장 자연스러움.
- **dev / staging 환경 분리** — 현재 `ai-agent-test-482706` 단일 프로젝트. 실 사용자 데이터 만지는 단계 들어오면 분리 권장.

---

## 작업 순서 (Implementation Order)

> 사용자가 `#1 구현해줘`, `#2 구현해줘` 식으로 따라가며 요청. 각 단계는 **그 자체로 챗에서 검증 가능** 한 단위. end-to-end 최소 슬라이스부터 시작해 서비스를 한 개씩 끼워 넣는 옵션 C 흐름.

### #1 — 첫 슬라이스: Confluence + Chat 푸시 (Phase 1 의 핵심 골격)

박광준 분이 챗에서 `주간보고초안` → "분석 중" 즉시 응답 → 백그라운드에서 그 사람 한 주치 Confluence 페이지 메타데이터 수집 → 같은 스페이스에 카드 푸시. **이 슬라이스 하나로 인증·푸시·이메일→팀 매칭·시간 범위 계산·카드 빌드까지 다 검증됨.** 이후 단계는 여기에 서비스를 더하는 식.

**짤 파일:**
- `api/_auth/__init__.py`, `api/_auth/service_account.py` — SA credentials 발급 (Cloud Run ADC fallback 포함)
- `api/_auth/confluence_basic.py` — `config/team_list.global` 에서 `api_token`, `confluence_url`, `user_email` 읽어 Basic auth 헤더 생성
- `api/chat/__init__.py`, `api/chat/messages.py` — `post_message_to_space(space_name, payload)` Chat REST API 푸시
- `api/confluence/__init__.py`, `api/confluence/pages.py` — `list_pages_modified(*, space_key, modified_by_email, time_min, time_max)` generic CRUD
- `domains/weekly_report/__init__.py`
- `domains/weekly_report/timewindow.py` — `_kst_two_weeks_around(meeting_date)` (Phase 1 임시: meeting_date = 오늘 KST 기준 이번주 금요일)
- `domains/weekly_report/cards.py` — 결과 카드 빌더 (Confluence 섹션만)
- `domains/weekly_report/draft.py` — 합성 함수, 백그라운드 thread 트리거

**수정:**
- `firestore/team_config.py` — `get_global_config()`, `find_team_by_email(email)`, `get_token_for(user_email)` 추가
- `main.py` — `UserIntent.WEEKLY_REPORT_DRAFT` 멤버 + `_weekly_report_draft_like()` 키워드 매칭 (`주간보고초안` 또는 `A함수호출`) + `_dispatch_by_intent` 분기. 우선순위는 `_weekly_meeting_like` 보다 먼저.
- `requirements.txt` — `requests>=2.30` 추가
- `config/settings.py` — `ALLMEET_DRAFT_MODEL` 환경변수 (Vertex 는 #4 에서 사용하지만 미리 정의)

**검증**: 챗에서 박광준 분 계정으로 `주간보고초안` → "분석 중이에요" 즉시 응답 → 30초 안에 같은 스페이스에 "박광준 한 주 활동" 카드 푸시. 카드에 Confluence 페이지 N건 (제목·수정일) 표시.

**의존**: 사용자 직접 작업 — Phase 1 prerequisite #3(Drive 멤버 추가)는 #2 직전까지로 미룰 수 있음. #1 검증에는 Confluence 만 있으면 됨. 단 Cloud Run 배포 시 `--cpu-always-allocated` 플래그.

---

### #2 — Drive 추가

**짤 파일:**
- `api/drive/__init__.py`, `api/drive/files.py` — `list_files_modified(*, drive_id, modified_by_email, time_min, time_max)` (Shared Drive `corpora=drive` + `driveId`)

**수정:**
- `domains/weekly_report/draft.py` — Drive 호출 추가 (서비스별 try/except 격리)
- `domains/weekly_report/cards.py` — Drive 섹션 추가

**검증**: 카드에 "Drive 작성/수정 파일" 섹션이 추가로 보임. Confluence 빈 결과여도 Drive 결과는 표시되는지 (격리 검증).

**의존**: Prerequisite #3 — 센터 Shared Drive 에 SA 멤버 등록 완료.

---

### #3 — Calendar (공용) 추가 + 주간회의 일자 정확히 찾기

`timewindow.py` 의 임시 meeting_date 를 진짜 캘린더 조회로 교체.

**짤 파일:**
- `api/calendar/__init__.py`, `api/calendar/events.py` — `list_events(*, calendar_id, time_min, time_max, q)` generic CRUD

**수정:**
- `domains/weekly_report/draft.py` — 첫 단계에서 `api.calendar.events.list_events(calendar_id, q="주간회의")` 로 회의 일자 회수 → 그 일자 기준 `_kst_two_weeks_around` 계산
- `domains/weekly_report/timewindow.py` — KST 월~일 경계 정확히. UTC 변환 안전화.
- `domains/weekly_meeting/schedule_lookup.py` — `_get_access_token`, `_calendar_list_events` 를 `api.calendar.events.list_events` 호출로 점진 교체. `_week_range` 에 KST 옵션 추가 (default UTC 유지로 회귀 방지).
- `domains/weekly_report/cards.py` — Calendar 섹션 추가 (회의 이력)

**검증**: 카드에 "회의 이력" 섹션 + 한 주 범위가 회의 일자 기준 정확. 기존 `wm_schedule_meeting_this` / `_next` 카드 액션 회귀 없는지.

**의존**: Prerequisite #5 — 팀 캘린더에 SA 공유 완료.

---

### #4 — Vertex AI 분석 + 종합 초안

원시 메타데이터 나열만 하던 카드를 LLM 분석으로 강화. 회의는 "X 회의 진행" / 페이지는 "X 매뉴얼 작성" 등 자연어로.

**짤 파일:**
- `domains/weekly_report/prompts.py` — Vertex 프롬프트 템플릿 + `response_schema`

**수정:**
- `domains/weekly_report/draft.py` — Vertex `gemini-2.0-flash-001` 호출 (JSON mode), 결과 dict 를 카드에 맵핑
- `domains/weekly_report/cards.py` — "종합 초안" 단락 (마지막 섹션)

**검증**: 카드 하단에 한국어 종합 초안 한 단락 + 각 항목이 원시 제목이 아닌 LLM 분류 결과("매뉴얼 작성" / "회의록 정리" 등) 로 표시.

**의존**: 없음 (Vertex 는 daily_chat 에서 이미 사용 중).

---

### #5 — 다중 사용자 / 부분 결손 / 안내 카드 정밀화

지금까지 박광준 한 사람으로 검증. 이제 다른 팀원 (김태표·홍현기·...) 도 챗에서 트리거 → 그 사람 데이터로 동작. 매칭 실패 / 데이터 빈 결과 처리.

**수정:**
- `domains/weekly_report/draft.py` — 사용자 → 팀 매칭 실패 시 안내 카드, 모든 서비스 빈 결과 시 "이 한 주에 활동이 없네요" 안내
- `domains/weekly_report/cards.py` — 빈 섹션 처리, 에러 섹션 ("Confluence 조회 실패" 같은)

**검증**: PC2팀 다른 멤버 계정으로 트리거 → 그 사람 데이터로 카드 생성. email 등록 안 된 사람은 안내 카드만.

**의존**: 없음.

---

### #6 — Phase 2 OAuth 인프라 (Gmail · 개인 Calendar 통합 준비)

**사용자 직접 작업** (Prerequisites #6, #7, #8):
- GCP OAuth consent screen (Internal) + 스코프 등록
- OAuth 2.0 Client ID 생성 + Redirect URI
- client_id / secret → Secret Manager
- Firestore 보안 규칙

**짤 파일:**
- `api/_auth/user_oauth.py` — refresh_token → access_token 재발급, 만료/거부 예외
- `firestore/oauth_tokens.py` — `oauth_tokens/{email}` read/write
- `firestore/team_member_oauth_sync.py` — 동의/철회 시 모든 팀의 `team_members[].oauth_status` sync

**수정:**
- `main.py` — `/oauth/callback` GET 엔드포인트 (동의 코드 → 토큰 교환 → 저장 → status sync)
- `domains/weekly_meeting/cards.py` — "내 데이터 연결" (`wm_oauth_link`) 카드 빌더
- `domains/weekly_meeting/handler.py` — `wm_oauth_link` 디스패치 (동의 URL 발급해 사용자에게 반환)
- `requirements.txt` — `google-auth-oauthlib>=1.0` 추가

**검증**: 박광준 분이 챗에서 `wm_oauth_link` 카드 클릭 → 브라우저 동의 → `/oauth/callback` 처리 → `oauth_tokens/{박광준이메일}` 문서 생성 + PC2 팀 (그리고 다른 팀에 같이 있다면 그 팀들도) 의 박광준 멤버에 `oauth_status = "linked"`.

**의존**: Prerequisites #6~#8 완료.

---

### #7 — Gmail 추가

**짤 파일:**
- `api/gmail/__init__.py`, `api/gmail/messages.py` — `list_messages(*, user_email, time_min, time_max)`. credentials 는 `user_oauth.get_credentials(user_email)` 로.

**수정:**
- `domains/weekly_report/draft.py` — Gmail 호출 추가. `auth_required` 면 그 사람 메일만 빠뜨리고 다른 서비스 결과는 그대로 (부분 결손).
- `domains/weekly_report/cards.py` — Gmail 섹션 추가, 미동의 시 "메일 미연결" 안내
- `domains/weekly_report/prompts.py` — Vertex 프롬프트에 메일 항목 추가

**검증**: OAuth 동의한 박광준 분 트리거 → 카드에 "보낸/받은 메일" 섹션. 미동의한 다른 팀원 트리거 → 메일 섹션에 "미연결" 표시되고 나머지는 정상.

**의존**: #6 완료.

---

### #8 — 개인 Calendar 추가 (Phase 2 의 Calendar 부분)

`api/calendar/events.py` 의 호출 분기에서 "공용 캘린더 → SA credentials, 개인 캘린더 → user_oauth credentials" 로 분기.

**수정:**
- `api/calendar/events.py` — `credentials_source: "service_account" | "user_oauth"` 인자 추가
- `domains/weekly_report/draft.py` — 개인 캘린더 호출 추가 (사용자가 동의했을 때만)
- `domains/weekly_report/cards.py` — Calendar 섹션을 "팀 회의" + "개인 일정" 으로 분리

**검증**: 동의한 사용자의 개인 일정도 카드에 보임. 미동의면 "개인 일정 미연결" 표시.

**의존**: #6 완료.

---

### #9 — 자동 스케줄러 (선택 — 데모 단계 이후)

**짤 파일:**
- (선택) `services/scheduling/weekly_runner.py` — 모든 팀 모든 멤버 FOR 문 합성. CONVENTIONS.md §11.3 의 "2개 이상 도메인 공유" 조건이 아직이라 `domains/weekly_report/runner.py` 로 시작도 OK.

**수정:**
- `main.py` — `/jobs/weekly_collect` POST 엔드포인트 (Cloud Scheduler 가 호출)
- 인프라: Cloud Scheduler job 등록 (매주 금요일 18:00 KST)

**검증**: Cloud Scheduler 에서 수동 트리거 → 모든 setup_completed=true 팀의 모든 멤버에게 카드 자동 푸시.

**의존**: #5 까지는 끝나야 의미 있음. Phase 2 (#6~#8) 는 선택 — Phase 1 만 자동 스케줄러도 가치 있음.

---

### #10 — 운영 강화 (선택)

레이트 리밋 백오프, PII 마스킹 정책, 평문 토큰 → Secret Manager 마이그레이션, 로그/모니터링, dev/staging 분리. 한 번에 안 하고 필요할 때 하나씩.

---

## 진행 시 주의

- 각 #N 끝에서 **챗에서 직접 동작 확인**. 통과 안 하면 다음으로 안 넘어가기.
- 데이터 모델 (`config/team_list.global`, `teams[]`, `config/{team_id}.team_members[]`) 이 사용자 표현으로 "수정 중" 이므로 #1 시작 시 Firestore 한 번 더 확인.
- 새 코드는 모두 `CONVENTIONS.md` (특히 §11 레이어 분리, §6 카드 빌더 패턴, §5 에러 처리) 따름.
- 단계 사이에서 사용자가 "이거 잘못 짠 것 같아" 피드백 주면 그 단계만 고치고 다음으로.
