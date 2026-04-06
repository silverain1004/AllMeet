# all-meet 아키텍처 가이드

## 원칙
- 루트 `main.py`는 HTTP·의도 분류(`UserIntent`, 키워드 매칭)·도메인 호출만 담당하는 진입점으로 사용
- 도메인별 로직은 `domains` 하위로 분리
- 외부 연동은 `api` 하위로 분리
- 운영/인프라성 폴더(`logs`, `firestore`, `vertex-ai`)는 루트에서 독립 관리

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
  vertex-ai/
```

## 폴더명 제안 근거

Python 패키지로 import 하므로 디렉터리명은 `snake_case` 를 씁니다.

- `daily_chat`: 일상 대화 기능
- `weekly_meeting`: 주간 회의/팀/인원 등록 기능
- `schedule_management`: 캘린더 예약/일정 관리 기능
- `expert_finder`: 사내 전문가 찾기
