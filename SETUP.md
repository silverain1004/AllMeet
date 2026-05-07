# SETUP — 주간보고초안 동작 시키기 위한 사용자 작업

ROADMAP #1~#8 코드는 완료. 이 문서는 **GCP / Workspace / Cloud Run 측 작업** 만 정리. 자세한 설계 배경은 `ROADMAP.md`.

---

## Prerequisites (사용자 직접 작업)

### 1. Service Account (SA)

**옵션 A — Cloud Run 기본 SA 사용 (간단)**
- 그대로 두고 권한만 부여 (`Drive Admin`, `Calendar`, `Chat Bot`, `Vertex AI User` 등 필요 스코프).
- 추가 SA 생성 / 키 발급 불필요. ADC 자동 동작.

**옵션 B — 전용 SA + JSON 키 (권장)**
```
GCP Console → IAM & Admin → Service Accounts → CREATE
  이름: allmeet-bot
  ID: allmeet-bot@ai-agent-test-482706.iam.gserviceaccount.com
역할: 부여 안 함 (개별 리소스에 직접 멤버로 추가)
KEYS 탭 → ADD KEY → Create new key (JSON) → 다운로드
```
- JSON 키는 Secret Manager 에 저장하거나 Cloud Run 의 attached SA 로 사용.
- Cloud Run 배포 시 `--service-account=allmeet-bot@...iam.gserviceaccount.com`.

### 2. Shared Drive 멤버 등록

```
drive.google.com → 좌측 "공유 드라이브"
센터 자료 드라이브 클릭 → 우측 상단 "멤버 관리"
SA 이메일 추가 → 권한 "뷰어" (또는 그 이상)
```
SA 가 그 드라이브 안 모든 파일 메타데이터 + 작성자/수정자 정보를 볼 수 있게 됨.

### 3. `SHARED_DRIVE_ID` 환경변수

```
공유 드라이브 URL 끝의 문자열이 driveId
예: drive.google.com/drive/folders/0AB1xyz...   ← "0AB1xyz..." 부분
```
Cloud Run 배포 시 환경변수로 주입 (아래 §배포 참고).

### 4. 팀 캘린더 SA 공유

```
calendar.google.com → 좌측 "다른 캘린더"
PC2팀 calendar_id (c_b9eaaa762147e2838192050f2ae6ff03e9e0f38e242cc4...) 캘린더 찾기
캘린더 옆 ⋮ → "설정 및 공유"
"특정 사용자 또는 그룹과 공유" → SA 이메일 추가
권한: "일정 세부정보 보기"
```
**팀이 늘어날 때마다 새 팀 calendar_id 마다 같은 작업 필요.**

### 5. Confluence

이미 `config/team_list.global.api_token` 에 저장되어 있음. **별도 작업 X.**

### 6. Cloud Run 배포 옵션

`--cpu-always-allocated` 필수. 안 붙이면 Cloud Run 이 응답 후 즉시 CPU 정지 → 백그라운드 thread 가 죽음 → 결과 카드 푸시 안 됨.

### 7. Phase 2 OAuth (Gmail/개인 Calendar — 선택)

미설정이면 Gmail/개인 캘린더만 카드에 "OAuth 미연결" 로 빠지고, Drive/Confluence/공용 캘린더는 정상 동작. 즉 **Phase 1 데모는 이거 없이도 가능**.

설정하려면:

```
GCP Console → APIs & Services → OAuth consent screen
  User type: Internal (vntgcorp.com 내부)
  허용 스코프 추가:
    https://www.googleapis.com/auth/gmail.readonly
    https://www.googleapis.com/auth/calendar.readonly

GCP Console → APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
  Application type: Web application
  Authorized redirect URI: https://<Cloud Run URL>/oauth/callback
  → Client ID / Client secret 받음
```

세 환경변수 설정:
- `OAUTH_CLIENT_ID=...`
- `OAUTH_CLIENT_SECRET=...`
- `OAUTH_REDIRECT_URI=https://<Cloud Run URL>/oauth/callback`

---

## 환경변수 한눈에

| 환경변수 | 필수? | 값 | 용도 |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | ✓ | `ai-agent-test-482706` | Cloud Run 자동 주입 |
| `SHARED_DRIVE_ID` | ✓ | `0AB1xyz...` | Drive 검색 대상 |
| `ALLMEET_DRAFT_MODEL` | 선택 | `gemini-2.0-flash-001` | Vertex 모델 (default OK) |
| `ALLMEET_CHAT_MODEL` | 선택 | `gemini-2.0-flash-001` | daily_chat 모델 |
| `OAUTH_CLIENT_ID` | Phase 2 | `...apps.googleusercontent.com` | OAuth 동의 |
| `OAUTH_CLIENT_SECRET` | Phase 2 | `GOCSPX-...` | OAuth 동의 |
| `OAUTH_REDIRECT_URI` | Phase 2 | `https://<run-url>/oauth/callback` | OAuth 동의 |

---

## 배포

```bash
gcloud run deploy all-meet-agent \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated \
  --cpu-always-allocated \
  --service-account=allmeet-bot@ai-agent-test-482706.iam.gserviceaccount.com \
  --set-env-vars SHARED_DRIVE_ID=0AB1xyz... \
  --set-env-vars OAUTH_CLIENT_ID=...,OAUTH_CLIENT_SECRET=...,OAUTH_REDIRECT_URI=https://...
```

Phase 2 안 쓰면 OAUTH_* 세 개는 빼도 됨.

---

## 로컬 테스트

```bash
# 인증 없이 syntax 만 확인 (코드 작성 후 Python 파싱 OK 확인 완료)
functions-framework --target=hello_http --debug --port=8080

# 진짜 동작은 Cloud Run 배포 후 챗에서.
# 로컬에서 외부 API 도 호출하려면 GOOGLE_APPLICATION_CREDENTIALS 환경변수에 SA 키 경로.
```

---

## 챗에서 동작 확인

1. PC2팀 멤버(예: 박광준 분) 계정으로 봇이 있는 스페이스에 진입
2. 메시지 입력: `주간보고초안` (또는 `A함수호출`)
3. 즉시 "주간보고초안 분석 중이에요. 잠시 후 결과를 보내드릴게요." 응답
4. 30초~수 분 후 같은 스페이스에 결과 카드 푸시:
   - 📅 회의 이력
   - 📁 Drive 작성/수정 파일
   - 📄 Confluence 페이지
   - 📧 Gmail (Phase 2 동의자만)
   - 🗓️ 개인 일정 (Phase 2 동의자만)
   - ✏️ 종합 초안 (Vertex Gemini)

OAuth 연결 (Phase 2):
- 메인 메뉴 (`주간 회의` 등으로 진입) → "6. 내 데이터 연결 (Gmail/개인 Calendar)" 클릭
- 카드의 "🔗 Google 동의 페이지 열기" 버튼 → 브라우저 동의
- 동의 후 Cloud Run 의 `/oauth/callback` 에 redirect → "연결 완료 ✅" 페이지
- `oauth_tokens/{email}` Firestore 문서 + 그 사람 모든 팀의 `oauth_status="linked"`

---

## 알려진 한계 / 주의

- **데이터 모델 "수정 중"** — `config/team_list.teams[i].space_key` 가 PC2팀 문서에 빈 문자열인 듯. 코드는 `space_key` / `confluence_space_key` 둘 다 시도하게 짜놨음. 모델 확정되면 한쪽으로 통일.
- **MES2 팀의 `space_key: "PC2"`** — 데이터 그대로 코드 흐름엔 영향 없음. 정정 필요하면 사용자가 Firestore 에서 수정.
- **Vertex Gemini 실패 시 폴백** — 종합 초안만 빠지고 raw 데이터 카드는 그대로 표시.
- **OAuth refresh_token 폐기 감지** — `refresh()` 실패 시 자동 `revoked` 마킹. 단 `team_members[].oauth_status` sync 는 명시 호출 필요 (현재는 callback 시점에만 sync). 폐기 감지 시 sync 호출은 추후 작업.
- **백그라운드 thread 가 Cloud Run 인스턴스 종료 시 중단** — 갑작스러운 스케일다운/새 배포 시 진행 중 분석이 사라짐. MVP 수용. 데이터량 늘면 Cloud Tasks 큐로 전환.
- **평문 토큰 (Firestore)** — `config/team_list.global.api_token` 평문 저장 — 사용자 명시로 "보안 섹션 추후" 보류.
- **Calendar 검색 q 파라미터** — Google Calendar 의 `q` 는 부분 일치라 "주간회의" 가 다른 단어에 포함된 일정도 잡힐 수 있음 (드물지만).
- **봇 운영 계정 단일 의존** — silverain 분 Atlassian 권한 / Cloud Run SA 권한에 의존. 그 계정 회수 시 시스템 정지.

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 챗에서 "분석 중" 응답만 오고 결과 카드 안 옴 | (a) Cloud Run 에 `--cpu-always-allocated` 안 붙임 (b) SA 가 `chat.bot` 스코프 없음 (c) Cloud Run 로그에 백그라운드 예외 — 확인 필요 |
| "소속 팀을 찾지 못했어요" | `config/{team_id}.team_members[].email` 에 그 사용자 이메일 등록 안 됨. Firestore 직접 또는 카드로 추가 |
| Drive 섹션 "shared_drive_id_missing" | `SHARED_DRIVE_ID` 환경변수 미설정 |
| Drive 섹션 "auth_error" 또는 "not_found" | SA 가 Shared Drive 멤버 아니거나 ID 가 잘못됨 |
| Confluence 섹션 "auth_error" | `config/team_list.global.api_token` 토큰 만료/오타 |
| Calendar 섹션 "not_found" | 팀 `calendar_id` 가 잘못됐거나 SA 가 그 캘린더에 공유 안 받음 |
| Gmail/개인 Calendar 섹션 "auth_required" | 그 사용자가 OAuth 동의 안 함. 메뉴에서 "내 데이터 연결" 클릭하면 됨 |
| `/oauth/callback` 500 | OAUTH_CLIENT_ID/SECRET/REDIRECT_URI 환경변수 미설정 또는 redirect URI 가 GCP Console 등록값과 다름 |
| Vertex Gemini 호출 실패 (`종합 초안` 안 나옴) | (a) Vertex AI API 활성화 안 됨 (b) SA 가 `aiplatform.user` 역할 없음 (c) `LOCATION` 이 `us-central1` 아님 |

Cloud Run 로그 확인:
```bash
gcloud run services logs read all-meet-agent --region asia-northeast3 --limit 50
```
