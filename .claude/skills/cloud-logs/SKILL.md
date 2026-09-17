---
name: cloud-logs
description: AllMeet 에이전트(Cloud Run/Scheduler) 로그를 gcloud로 직접 조회·분석한다. 사용자가 로그를 붙여넣지 않아도 됨. 트리거 — "로그 확인해", "스케줄러 왜 안 돌았어", "알림 왜 안 갔어", "배포 후 에러 확인", 오류 시각/insertId/trace 언급 시.
---

# AllMeet Cloud 로그 조회

사용자가 콘솔에서 로그를 복사해 붙여넣게 하지 말 것. 이 스킬로 직접 조회한다.

## 고정 환경

- GCP 프로젝트: `ai-agent-test-482706`
- Cloud Run 서비스: `all-meet-agent` (region `asia-northeast3`)
- Cloud Scheduler 잡: `weekly-page-all-teams` 외 — 목록은 `gcloud scheduler jobs list --location=asia-northeast3`로 확인

## 기본 조회 절차

1. **시간 범위를 먼저 정한다.** 사용자가 "오늘 아침 8시 스케줄러"처럼 말하면 KST → UTC 변환(-9h) 후 앞뒤 5분 버퍼를 둔다. 시각 언급이 없으면 최근 1시간.
2. **서비스 로그 조회** (기본형):
   ```bash
   gcloud logging read '
     resource.type="cloud_run_revision"
     resource.labels.service_name="all-meet-agent"
     timestamp>="2026-01-01T00:00:00Z" timestamp<="2026-01-01T01:00:00Z"
   ' --project=ai-agent-test-482706 --order=asc --limit=200 \
     --format='value(timestamp,severity,textPayload,jsonPayload.message)'
   ```
3. **에러만 먼저**: 같은 쿼리에 `severity>=ERROR`를 추가해 1차 스캔 → 에러가 있으면 그 `trace` 값으로 재조회해 요청 전체 흐름을 본다:
   ```
   trace="projects/ai-agent-test-482706/traces/<trace_id>"
   ```
4. **스케줄러 실행 여부 확인**:
   ```
   resource.type="cloud_scheduler_job"
   resource.labels.job_id="<잡이름>"
   resource.labels.location="asia-northeast3"
   ```
   status가 실패면 대상 URL 호출 시각으로 2번 쿼리를 다시 실행해 서버측 원인을 찾는다.
5. **특정 엔드포인트 추적**: `httpRequest.requestUrl=~"<경로>"` 필터 사용 (사용자가 "주간보고 초안", "할일 알림" 등 기능명으로 말하면 소스에서 해당 라우트 경로를 먼저 찾은 뒤 필터링).

## 분석 규칙

- 로그를 그대로 나열하지 말고: **① 실행됐는가 ② 어디까지 성공했는가 ③ 실패 지점의 메시지 ④ 해당 소스 위치(file:line)** 순으로 요약한다.
- `textPayload`의 앱 자체 로그 프리픽스(`[PC2]`, `[MES2]`, `weekly_report`, `drive` 등)는 기능 모듈 태그다 — 소스에서 같은 프리픽스로 grep하면 로그 발생 지점을 바로 찾을 수 있다.
- 인증 관련 이슈(OAuth, revoked, token)는 사용자 이메일 단위로 필터: `jsonPayload.message=~"<email>"`.
- 결과가 0건이면 시간 범위·리전·리비전을 의심하고, `gcloud run revisions list --service=all-meet-agent --region=asia-northeast3`로 최근 배포 시각과 대조한다.

## 배포 직후 확인 요청이면

1. 최신 리비전 준비 상태 확인 → 2. 배포 시각 이후 `severity>=WARNING` 스캔 → 3. 문제 없으면 "리비전 X, 에러 0건"으로 짧게 보고.
