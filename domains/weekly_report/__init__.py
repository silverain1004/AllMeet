"""주간보고초안 도메인 — 사용자 한 주 활동 합성 및 Vertex Gemini 기반 초안 생성.

채팅 트리거: ``주간보고초안`` 또는 ``A함수호출`` (테스트 임시).
즉시 "분석 중" 응답 + 백그라운드 thread 에서 데이터 수집·분석 후 Chat REST 로 push.
"""

from domains.weekly_report.draft import handle_weekly_report_draft

__all__ = ["handle_weekly_report_draft"]
