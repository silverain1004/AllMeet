# 군산 회의실 Google Calendar 연동 설정

AllMeet가 Google Workspace 회의실 리소스를 사용하려면 서비스 계정(SA)이 리소스 캘린더와 집계 캘린더에 접근할 수 있어야 합니다.

## 1. 필요한 캘린더 ID 확인

Google Calendar → 설정 → 해당 캘린더 → **캘린더 통합**에서 ID를 복사합니다.

| 항목 | 형식 예시 |
|------|-----------|
| 군산 회의실 예약 (집계) | `c_...@group.calendar.google.com` |
| 개별 회의실 리소스 (4개) | `...@resource.calendar.google.com` |

## 2. 서비스 계정 이메일 확인

`GOOGLE_APPLICATION_CREDENTIALS` 키 파일의 `client_email` 값을 확인합니다.

```bash
# 예: all-meet-agent@ai-agent-test-482706.iam.gserviceaccount.com
```

## 3. 캘린더 공유 (권장)

Google Admin 또는 각 캘린더 설정에서 SA 이메일에 권한을 부여합니다.

### 3개 회의실 리소스 캘린더 (군산)

| 회의실 | 수용 인원 | 리소스 ID |
|--------|-----------|-----------|
| VNTG 군산 V-Room | 18 | `c_1885rgn3rk4pci2hkkv4lo7s65qcc@resource.calendar.google.com` |
| VNTG 군산 N-Room | 12 | `c_1880jg03klhiii2eltar07fc223gu@resource.calendar.google.com` |
| VNTG 군산 T-Room | 4 | `c_1881b3e97f71kit8l3u5h06auf63g@resource.calendar.google.com` |

캘린더 이름 끝 괄호 숫자 `(18)` 등이 수용 인원으로 자동 반영됩니다.

### 리소스 캘린더 공유

- **See only free/busy** 이상 (가용성 조회)
- **Make changes to events** (실제 리소스 예약)

### 군산 회의실 예약 집계 캘린더

- **See all event details** (기존 예약 조회)

## 4. AllMeet 설정

### UI (캘린더 설정 카드)

- **군산 집계 캘린더 ID**: 집계 캘린더 ID 입력
- **회의실 리소스 ID**: 동기화가 0건일 때 4개 ID를 줄바꿈 또는 쉼표로 입력
- **DWD 사용자 이메일** (선택): Domain-Wide Delegation 사용 시

### 환경 변수 (선택)

```bash
GUNSAN_ROOM_GROUP_CALENDAR_ID=c_b9eaaa762147e2838192050f2ae6ff03e9e0f38e242cc4394e963ee81212e454@group.calendar.google.com
GOOGLE_CALENDAR_IMPERSONATE_EMAIL=user@company.com
SCHEDULE_DRY_RUN=true    # 개발/테스트 시에만 (기본값: false = 실제 예약)
```

## 5. Domain-Wide Delegation (대안)

calendarList에 리소스가 보이지 않을 때:

1. Google Cloud Console → SA → Domain-wide delegation 활성화
2. Admin Console → API 제어 → Calendar API scope 추가:
   - `https://www.googleapis.com/auth/calendar.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
3. `impersonate_email`에 군산 회의실이 구독된 사용자 이메일 설정

## 6. 동기화 실행

AllMeet 메뉴 → **회의실 동기화** (`sm_sync_rooms`)

동기화 순서:

1. Firestore `room_resource_ids` 수동 등록
2. calendarList (+ DWD)
3. 집계 캘린더 이벤트에서 리소스 역추출

## 7. 문제 해결

| 증상 | 조치 |
|------|------|
| 동기화 0건 | SA에 4개 리소스 캘린더 공유 또는 수동 ID 등록 |
| 가용성 미확인 | `calendar_resource_id`가 비어 있음 → 동기화 재실행 |
| 예약 실패 (403) | OAuth 사용자 또는 SA에 리소스 예약 권한 확인 |
| 실제 예약이 안 됨 | `SCHEDULE_DRY_RUN=true`로 설정돼 있는지 확인 (기본은 false) |
