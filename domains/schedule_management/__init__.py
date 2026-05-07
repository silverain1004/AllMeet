"""캘린더·일정 관리 (추후 구현).

캘린더 예약·일정 관리 기능을 이 패키지에 구현합니다. (UserIntent.SCHEDULE_MANAGEMENT)
"""

from domains.schedule_management.handler import handle_schedule_management

__all__ = ["handle_schedule_management"]
