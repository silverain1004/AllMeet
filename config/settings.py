"""프로젝트·리전 — 참고 에이전트와 같이 환경 변수로 덮어쓸 수 있게 둡니다."""

import os

# Cloud Run: GOOGLE_CLOUD_PROJECT / GCLOUD_PROJECT, 로컬: PROJECT_ID
PROJECT_ID = (
    os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("GCLOUD_PROJECT")
    or os.environ.get("PROJECT_ID")
    or "ai-agent-test-482706"
)

# Vertex AI 리전 (Gemini 호출 위치)
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"

# Vertex 모델 — daily_chat 과 weekly_report 가 같이 사용 가능. 모델 교체는 환경변수로.
ALLMEET_CHAT_MODEL = os.environ.get("ALLMEET_CHAT_MODEL") or "gemini-2.5-flash"
ALLMEET_DRAFT_MODEL = os.environ.get("ALLMEET_DRAFT_MODEL") or "gemini-2.5-flash"

# Drive — 센터 Shared Drive ID (drive.google.com 의 공유 드라이브 URL 끝의 문자열).
# Phase 1 prerequisite: SA 가 이 Shared Drive 에 멤버로 등록되어 있어야 함.
SHARED_DRIVE_ID = os.environ.get("SHARED_DRIVE_ID") or ""

# OAuth (Phase 2) — 사용자 동의 플로우용. Web application 타입 client.
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID") or ""
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET") or ""
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI") or ""

# 봇 SA 이메일 — Drive Shared Drive 자동 멤버 등록 시 reader 로 추가할 대상.
# Cloud Run 에 attached 된 SA 와 동일해야 함.
BOT_SA_EMAIL = (
    os.environ.get("BOT_SA_EMAIL")
    or "all-meet-test@ai-agent-test-482706.iam.gserviceaccount.com"
)

# 휴가 캘린더 ID — 일정 공유 표에 사용. vacation_calendar_id 미설정 팀의 fallback.
VACATION_CALENDAR_ID = (
    os.environ.get("VACATION_CALENDAR_ID")
    or "c_u19ilp3ptq42lgd7os70ir6vuo@group.calendar.google.com"
)
