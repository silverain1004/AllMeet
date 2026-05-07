# all-meet 아키텍처 가이드

## 원칙
- 루트 `main.py`는 HTTP·의도 분류(`UserIntent`, 키워드 매칭)·도메인 호출만 담당하는 진입점으로 사용
- 도메인별 로직은 `domains` 하위로 분리
- 외부 연동은 `api` 하위로 분리
- 운영/인프라성 폴더(`logs`, `firestore`, `vertex-ai`)는 루트에서 독립 관리
- Google Chat 카드 액션(`CARD_CLICKED`)은 도메인 액션 핸들러로 라우팅

## 폴더 구조
```text
all-meet/
  main.py
  domains/
    daily_chat/
    weekly_meeting/
    schedule_management/
    expert_finder/
  api/
    confluence/
    drive/
    calendar/
    gmail/
  logs/
  firestore/
    team_config.py
  vertex-ai/
```

## 폴더명 제안 근거

Python 패키지로 import 하므로 디렉터리명은 `snake_case` 를 씁니다.

- `daily_chat`: 일상 대화 기능
- `weekly_meeting`: 주간 회의/팀/인원 등록 기능
- `schedule_management`: 캘린더 예약/일정 관리 기능
- `expert_finder`: 사내 전문가 찾기

## 주간업무보고 세팅 MVP

- 진입: 사용자가 주간업무보고/세팅 관련 메시지를 보내면 `weekly_meeting` 도메인으로 라우팅
- 카드 플로우: 팀 설정 -> 폴더 구조 설정 -> 템플릿 링크 입력 -> 완료 카드 반환
- 카드 빌더: `domains/weekly_meeting/cards.py`에서 cardsV2 생성 로직 분리
- 카드 액션: `main.py`의 `CARD_CLICKED` 이벤트에서 `wm_*` 함수명을 `handle_weekly_meeting_action`으로 위임
- 저장소: `firestore/team_config.py`가 팀 단위 설정(`config/{team_id}`) 저장/조회 담당

### 팀 설정 문서 모델 (`config/{team_id}`)

- `team_name`
- `team_members` (배열)
- `space_id` (예: `m0g1TiAAAAE`)
- `folder_schema` (JSON 배열)
- `template_page_url`
- `template_page_id`
- `setup_completed` (bool)
- `user_context` (map: `name`, `email`, `department`)
- `created_at` (timestamp)
- `updated_at` (timestamp)

### 저장 규칙

- 신규 생성: `created_at` + `updated_at` 동시 저장
- 업데이트: `updated_at`만 갱신, `created_at` 유지
- `user_context.department` 누락 시 `"미지정"`으로 저장
