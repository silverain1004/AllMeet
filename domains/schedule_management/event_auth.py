"""기존 일정 수정·삭제 권한 — 누구의 토큰으로 쓸지 결정한다.

Google 캘린더는 주최자만 시간·회의실(참석자 목록)을 바꿀 수 있다. 사용자 결정에 따라
  - 주최자 본인 → 본인 토큰
  - 주최자가 아니면: 그 일정의 참석자이거나 주최자와 같은 팀원일 때만 허용하고,
    주최자가 AllMeet 에 캘린더를 연결해 뒀으면 주최자 토큰으로 대신 실행한다.
    주최자가 미연결이면 요청자 토큰으로 시도한다('참석자 수정 허용' 일정이면 성공).
대신 실행하는 쓰기는 반드시 백업(event_backup)을 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domains.schedule_management.oauth_calendar import get_user_access_token, is_oauth_linked


@dataclass
class WriteAuth:
    ok: bool
    token: str = ""
    acting_as: str = ""  # 실제로 캘린더에 쓰는 계정
    via: str = ""  # self | organizer | guest
    error: str = ""  # not_permitted | requester_not_linked

    @property
    def on_behalf(self) -> bool:
        return self.via == "organizer"

    def notice(self, organizer_email: str) -> str:
        if self.via == "organizer":
            return f"주최자({organizer_email})의 연결된 캘린더 권한으로 대신 실행해요. 실행 전 원본을 백업해요."
        if self.via == "guest":
            return (
                f"주최자({organizer_email})가 AllMeet 캘린더를 연결하지 않아 내 권한으로 시도해요. "
                "주최자가 '참석자 일정 수정'을 막아 두었으면 실패할 수 있어요."
            )
        return "내가 주최한 일정이라 내 권한으로 실행해요."


def _teams_by_email(members: list[dict[str, Any]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in members:
        email = str(m.get("email") or "").strip().lower()
        tid = str(m.get("team_id") or "").strip()
        if email and tid:
            out.setdefault(email, set()).add(tid)
    return out


def is_permitted(
    *,
    requester_email: str,
    organizer_email: str,
    attendee_emails: list[str],
    members: list[dict[str, Any]],
) -> bool:
    req = (requester_email or "").strip().lower()
    org = (organizer_email or "").strip().lower()
    if not req:
        return False
    if req == org or req in {a.strip().lower() for a in attendee_emails}:
        return True
    teams = _teams_by_email(members)
    return bool(teams.get(req, set()) & teams.get(org, set()))


def resolve_write_auth(
    *,
    requester_email: str,
    organizer_email: str,
    organizer_self: bool,
    attendee_emails: list[str],
    members: list[dict[str, Any]],
) -> WriteAuth:
    req = (requester_email or "").strip()
    org = (organizer_email or "").strip()
    if organizer_self or (req and req.lower() == org.lower()):
        token = get_user_access_token(req) if req else None
        if not token:
            return WriteAuth(ok=False, error="requester_not_linked")
        return WriteAuth(ok=True, token=token, acting_as=req, via="self")
    if not is_permitted(
        requester_email=req, organizer_email=org, attendee_emails=attendee_emails, members=members
    ):
        return WriteAuth(ok=False, error="not_permitted")
    if org and is_oauth_linked(org):
        token = get_user_access_token(org)
        if token:
            return WriteAuth(ok=True, token=token, acting_as=org, via="organizer")
    token = get_user_access_token(req) if req else None
    if not token:
        return WriteAuth(ok=False, error="requester_not_linked")
    return WriteAuth(ok=True, token=token, acting_as=req, via="guest")


def auth_error_message(auth: WriteAuth, organizer_email: str) -> str:
    if auth.error == "not_permitted":
        return (
            f"이 일정의 주최자({organizer_email})와 같은 팀이거나 참석자여야 수정할 수 있어요."
        )
    return "내 Google 캘린더를 먼저 연결해 주세요."
