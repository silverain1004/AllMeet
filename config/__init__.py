"""앱 전역 설정 (환경 변수 기준). Cloud Run에서는 GCLOUD_PROJECT 등이 자동 주입됩니다."""

from config.settings import LOCATION, PROJECT_ID

__all__ = ["PROJECT_ID", "LOCATION"]
