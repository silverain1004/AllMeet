"""주간 회의·팀·인원 등록 (추후 구현).

주간 회의·팀·인원 등록 기능을 이 패키지에 구현합니다. (UserIntent.WEEKLY_MEETING)
"""

from domains.weekly_meeting.handler import handle_weekly_meeting, handle_weekly_meeting_action

__all__ = ["handle_weekly_meeting", "handle_weekly_meeting_action"]
