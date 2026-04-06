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
