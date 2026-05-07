# DWD (Domain-Wide Delegation) 가이드

이 문서는 "all-meet-agent 가 사용자별 Gmail · 개인 Calendar 데이터를 자동 수집하려면 어떤 인증을 써야 하는가" 를 검토하면서 후보로 올랐던 **DWD (Domain-Wide Delegation)** 의 개념과 현실적 적용성을 정리한 것입니다. 본 프로젝트는 결국 DWD 를 **사용하지 않기로** 결정했고 (`ROADMAP.md` Phase 2 = OAuth refresh_token 모델), 이 문서는 그 결정의 근거를 설명하고, 만약 향후 정책이 바뀌어 DWD 가 가능해지면 어떻게 변하는지를 기록합니다.

## 한 줄 요약

DWD = "Workspace 도메인 모든 사용자의 메일·드라이브를 봇이 사용자 동의 없이 impersonate" 하는 강력 권한. **해커톤 시간 안에 받기는 사실상 불가** → OAuth refresh_token 모델 채택.

## DWD 가 무엇인가

**DWD = Google Workspace 도메인의 모든 사용자를 Service Account 가 impersonate 할 수 있게 해주는 권한**.

비유로 풀면 — 회사 회의실 하나에 사번 카드를 등록해 출입자 명단에 추가하는 게 아니라, 회사의 모든 회의실(메일·드라이브·캘린더) 마스터키를 발급받는 것. 마스터키만 있으면 봇은 누구의 이름으로든 들어갈 수 있고, 그 사람 동의 클릭이 필요 없음.

기술적으로는 — Service Account 가 OAuth 2.0 JWT 를 발급할 때 `sub` claim 에 임의의 사용자 이메일(예: `e50271@vntgcorp.com`)을 넣어 그 사용자 권한으로 access_token 을 받습니다. 봇은 그 토큰으로 그 사용자의 Gmail 받은편지함, 개인 Drive, 개인 Calendar 등 모든 데이터에 접근 가능.

## 활성화에 필요한 두 단계

### 1. GCP 측 (개발자/프로젝트 오너 클릭)

- IAM & Admin → Service Accounts → 대상 SA 선택
- "Domain-wide delegation 사용" 토글 ON
- 자동 생성되는 **Client ID (숫자 21자리)** 복사 — 다음 단계에서 Workspace 관리자에게 전달
- (keyless 방식) 호출 측 SA 가 대상 SA 에 대해 `roles/iam.serviceAccountTokenCreator` 보유 필요
- (key file 방식) SA JSON 키 발급해 Secret Manager 등 안전한 곳에 보관

### 2. Workspace 측 (도메인 관리자 클릭) — **결정적인 단계**

- Google Workspace Admin Console → Security → Access and data control → API controls → Domain-wide delegation
- "Add new" → 위에서 받은 Client ID 입력
- 허용할 OAuth 스코프 명시 (예: `https://www.googleapis.com/auth/gmail.readonly`, `.../calendar.readonly`, `.../drive.readonly`)
- 저장 후부터 그 SA 가 화이트리스트된 스코프 범위 내에서 도메인 모든 사용자 impersonate 가능

**핵심**: 1단계는 GCP 권한만 있으면 본인이 직접 가능. 그러나 2단계는 **vntgcorp.com 도메인의 Workspace 관리자(보통 IT/보안팀)** 만 가능합니다. 이게 해커톤 일정의 병목.

## 권한 범위와 보안 영향

DWD 가 부여되면 SA 는 다음을 *사용자 동의 없이* 할 수 있습니다:

- 모든 직원의 Gmail 받은편지함 조회 (스코프에 따라 본문까지)
- 모든 직원의 Drive 파일 (개인 자료 포함) 조회
- 모든 직원의 Calendar 일정 조회
- (스코프 추가 시) 메일 발송, 파일 수정, 일정 등록까지

**이 강력함이 보안팀 검토를 까다롭게 만드는 이유:**

- SA 키가 유출되면 도메인 전체가 노출 (key file 방식 시)
- keyless 방식이라도 호출 SA 의 IAM 권한이 잘못 잡히면 권한 escalation 가능
- 스코프 화이트리스트가 좁게 잡혀 있어도, 일단 한 번 부여되면 코드가 어떻게 쓰는지 회사 측에서 직접 감시하기 어려움
- 감사 로그(GWS Admin Audit, Cloud Audit) 설정·모니터링 필수
- 일반적으로 **검토 + 승인까지 1주 ~ 수 주** 소요

## 해커톤 컨텍스트 평가

**컨텍스트:** vntgcorp.com Workspace 사내 해커톤 (`vntg_ai_license_03@vntgcorp.com` 라이선스 계정 사용). 시간이 짧고 (보통 며칠~몇 주), IT 보안팀이 별도의 심사 큐에서 운영됨.

| 시나리오 | DWD 받을 가능성 |
|---|---|
| 사내 해커톤 + 책임자가 IT 보안 의사결정권자 직접 | ◯ 빠르게 가능 |
| 사내 해커톤 + IT 보안팀 정상 검토 절차 | △ 1~수 주 소요, 해커톤 일정에 안 맞음 |
| 외부 해커톤 또는 vntgcorp.com 외 도메인 | ✗ 사실상 불가 |
| 회사 보안 정책상 "강력 권한 거부" 결론 | ✗ 회사에 따라 자주 발생 |

**현실적으로**: vntgcorp.com 사내 해커톤이라도 DWD 같은 도메인 전체 영향 권한을 해커톤 일정 안에 받기는 매우 어렵습니다. 실제 본 프로젝트에서도 사용자가 "DWD 는 사용 못할 것 같다" 고 명확히 시그널하여, **OAuth refresh_token 운영 모델로 확정**되었습니다.

만약 그래도 시도해 보고 싶다면, 화이트리스트 요청 시 IT 보안팀에 다음을 같이 제출하면 통과 가능성이 올라갑니다:
- SA 의 정확한 Client ID
- 요청 스코프 (read-only 만, 가능하면 `metadata.readonly` 같은 좁은 범위)
- 데이터 사용 목적과 보관 기간
- 감사 로그 설정 계획
- 키 회전 / 폐기 절차 (key file 방식인 경우)
- "해커톤 종료 후 화이트리스트 자동 회수" 합의

## 본 프로젝트의 대안 — OAuth refresh_token 운영

`ROADMAP.md` Phase 2 참조. 핵심 차이를 표로 비교:

| 항목 | DWD | OAuth refresh_token (본 plan) |
|---|---|---|
| 사용자 동의 클릭 | 0번 (관리자가 화이트리스트만) | 각 팀원 1회 (챗에서 카드 클릭) |
| Workspace 관리자 협조 | **필수** | 불필요 |
| 보안팀 검토 | 강력 권한이라 까다로움 | 일반 OAuth 앱 등록 수준 |
| 검토 소요 시간 | 1주~수 주 | 즉시 (Internal 사용자 타입) |
| 미동의 팀원 처리 | 동의 개념 없음 — 모두 자동 수집 | 그 사람 데이터만 빠짐 (부분 결손) |
| 토큰 회전 / 만료 코드 | 불필요 (SA 가 매번 새 발급) | 필요 (refresh_token 갱신, 만료 처리) |
| 운영 부담 | 신규 팀원 추가 시 작업 0 | 신규 팀원 동의 안내 필요 |
| 코드 복잡도 | 낮음 | 중간 (callback 엔드포인트, 토큰 저장소) |
| Drive 봇 계정 SA + Shared Drive 멤버 | 동시 사용 가능 | 동시 사용 가능 (Phase 1 그대로) |

**결론**: 보안 부담은 OAuth 가 압도적으로 작고, 운영 부담은 살짝 있지만 코드로 카드/안내를 자동화 가능. 해커톤 시간 안에 출시 가능한 길은 OAuth 가 사실상 유일.

## 부분 결손은 받아들일 만한가?

OAuth 모델의 한계는 "동의 클릭 안 한 팀원의 메일/개인 캘린더 데이터가 빠진다" 는 것. 이게 주간보고 초안의 가치를 얼마나 깎는가:

- **Drive 와 Confluence 는 SA 모델이라 100% 자동 수집** — 작성자 메타데이터(`owners`, `lastModifyingUser`)가 함께 나오므로 "누가 무엇을 만들었나" 추적은 그대로 가능.
- **공용 Calendar 도 SA 공유 모델이라 자동 수집** — 팀 공용 캘린더의 회의 일정은 다 잡힘.
- **Gmail / 개인 Calendar 만** 동의한 팀원 한정으로 수집.

즉 미동의 팀원이라도 "그 사람이 작성/수정한 Drive 파일·Confluence 페이지", "팀 공용 회의에 참여한 일정" 은 다 잡힙니다. 빠지는 건 "그 사람이 메일·1:1 협업으로만 한 일" 뿐. 1차 MVP 의 가치 손실로는 받아들일 만한 수준.

## 만약 향후 DWD 가 통과되면 (마이그레이션 가이드)

`ROADMAP.md` Phase 2 의 OAuth 트랙을 다음과 같이 교체:

- `api/_auth/user_oauth.py` → `api/_auth/delegated.py` 로 신설 (keyless: IAM Credentials API `signJwt` 활용)
- `oauth_tokens/{email}` Firestore 컬렉션 폐기 (불필요해짐)
- `/oauth/callback` 엔드포인트 폐기
- `wm_oauth_link` 카드 폐기
- 미동의 팀원 부분 결손 로직 폐기 (모두 자동 수집)

**코드 측 마이그레이션 비용은 작음** — `api/gmail/messages.py` 와 `api/calendar/events.py` 의 credentials 발급 부분만 교체하면 됨. CONVENTIONS.md §11 의 레이어 분리 규약을 지킨 덕분에, 인증 모듈만 바꾸고 비즈니스 로직은 그대로 유지 가능.

## 추가 참고 자료

- Google 공식 — Service Account delegation: https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority
- DWD 보안 모범 사례: https://cloud.google.com/iam/docs/service-account-creds#delegation
- OAuth consent screen Internal vs External: https://support.google.com/cloud/answer/10311615
- IAM Credentials API `signJwt` (keyless impersonation): https://cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/signJwt
