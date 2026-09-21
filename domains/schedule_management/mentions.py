"""자유 발화의 멘션 추출과 부정(제외) 스코프 — 순수 모듈.

사람(이름·닉네임·이메일)·팀(이름·ID·"우리팀")·회의실·지역·집합표현("팀원들")·제외동사("빼고")를
위치(스팬)와 함께 찾고, 제외동사 앞의 나열 체인을 부정 처리한 뒤 참석자/회의실/지역을 합성한다.
Firestore 등 외부 의존이 없어 규칙 기반 파서(conversation.py)와 카드 입력란(handler.py)이 같이 쓴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from domains.schedule_management.gunsan_rooms import GUNSAN_ROOM_CATALOG
from domains.schedule_management.seoul_rooms import SEOUL_ROOM_CATALOG

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# 라틴 토큰(팀ID·영문 닉네임·회의실 영문명)은 단어 경계 안에서만 — "PC20", "erp2.lead", "mes2kim"에
# "PC2"/"ERP2"/"MES2"가 들어 있어도 팀으로 보지 않는다.
_LB = r"(?<![A-Za-z0-9._@-])"
_RB = r"(?![A-Za-z0-9._@-])"
_LATIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# 토큰 바로 뒤에 붙은 호칭·조사 — 긴 것 먼저(이랑 > 이).
_SUFFIX_RE = re.compile(
    r"(?:씨|님)?(?:이랑|으로|에서|하고|그리고|랑|와|과|및|은|는|이|가|을|를|도|만|로|에)*"
)
_LOCATIVE_TAILS = ("으로", "에서", "로", "에")

_COLLECTIVE_RE = re.compile(r"팀원들|멤버들|전원|팀\s?전체|전체(?!적)|모두|다\s?같이")
_EXCL_VERB_RE = re.compile(r"빼(?:고|줘요?|주고|주세요|주시고)|제외(?:하고|해줘요?|해서|해주세요)?|말고")
_OUR_TEAM_RE = re.compile(r"우리\s?팀|저희\s?팀|내\s?팀")

# 두 멘션 사이에 이것만 있으면 같은 나열로 본다(연결어 랑/하고/와 등은 앞 멘션의 suffix 에 이미 붙어 있다).
_GAP_RE = re.compile(r"^\s*(?:,|、|/|및|그리고)?\s*$")

_CATEGORY = {
    "member": "people",
    "email": "people",
    "team": "people",
    "bulk": "people",
    "room": "room",
    "region": "region",
}

_REGION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"서울"), "seoul"),
    (re.compile(r"군산"), "gunsan"),
    (re.compile(r"(?<!\d)[67]\s*층"), "seoul"),
    (re.compile(r"(?<!\d)3\s*층"), "gunsan"),
)


@dataclass
class Mention:
    kind: str  # email | member | team | bulk | room | region | verb
    start: int
    end: int
    suffix_end: int
    value: str
    negated: bool = False

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


def _norm_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).upper()


def _attach_suffix(text: str, end: int) -> int:
    m = _SUFFIX_RE.match(text, end)
    return m.end() if m else end


def _mask_emails(text: str) -> tuple[str, list[Mention]]:
    """이메일을 같은 길이의 공백으로 바꿔 오프셋을 유지한 채 다른 규칙이 그 안을 보지 못하게 한다."""
    mentions: list[Mention] = []
    out = list(text)
    for m in EMAIL_RE.finditer(text):
        mentions.append(
            Mention("email", m.start(), m.end(), _attach_suffix(text, m.end()), m.group(0))
        )
        for i in range(m.start(), m.end()):
            out[i] = " "
    return "".join(out), mentions


def teams_from_members(members: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for m in members:
        tid = str(m.get("team_id") or "").strip()
        if tid and tid not in seen:
            seen[tid] = str(m.get("team_name") or "").strip() or tid
    return [{"id": tid, "name": name} for tid, name in seen.items()]


def team_members_by_id(team_id: str, members: list[dict[str, Any]]) -> list[dict[str, str]]:
    """team_id 소속 멤버(이메일 있는 사람만) — 참석자 항목 형태로."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        if str(m.get("team_id") or "").strip() != team_id:
            continue
        email = str(m.get("email") or "").strip()
        if not email or email in seen:
            continue
        found.append({"name": str(m.get("name") or "").strip(), "email": email})
        seen.add(email)
    return found


def _team_of_sender(sender_email: str, members: list[dict[str, Any]]) -> str:
    target = (sender_email or "").strip().lower()
    if not target:
        return ""
    for m in members:
        if str(m.get("email") or "").strip().lower() == target:
            return str(m.get("team_id") or "").strip()
    return ""


def _find_all(haystack: str, needle: str) -> list[int]:
    out: list[int] = []
    if not needle:
        return out
    pos = haystack.find(needle)
    while pos != -1:
        out.append(pos)
        pos = haystack.find(needle, pos + 1)
    return out


def _claim(
    candidates: list[tuple[int, int, str]],
    claimed: list[Mention],
    kind: str,
    text: str,
) -> list[Mention]:
    """긴 스팬 먼저 채택하고, 이미 채택된 스팬과 겹치는 후보는 버린다."""
    out: list[Mention] = []
    for start, end, value in sorted(candidates, key=lambda c: (-(c[1] - c[0]), c[0])):
        if any(m.overlaps(start, end) for m in claimed) or any(m.overlaps(start, end) for m in out):
            continue
        out.append(Mention(kind, start, end, _attach_suffix(text, end), value))
    return out


def _member_candidates(masked: str, members: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    cands: list[tuple[int, int, str]] = []
    for m in members:
        email = str(m.get("email") or "").strip()
        if not email:
            continue
        names = [str(m.get("name") or "").strip()] + [
            str(n).strip() for n in (m.get("nickname") or []) if str(n).strip()
        ]
        for n in names:
            if len(n) < 2:
                continue
            if _LATIN_TOKEN_RE.match(n):
                for hit in re.finditer(_LB + re.escape(n) + _RB, masked, re.IGNORECASE):
                    cands.append((hit.start(), hit.end(), email))
            else:
                for pos in _find_all(masked, n):
                    cands.append((pos, pos + len(n), email))
    return cands


def find_team_mentions(text: str, teams: list[dict[str, str]]) -> list[Mention]:
    """등록 팀의 이름(공백 제거형 포함)과 ID(단어 경계, 뒤에 '팀' 허용)를 위치와 함께 모두 찾는다."""
    masked, _ = _mask_emails(text)
    cands: list[tuple[int, int, str]] = []
    for team in teams or []:
        if not isinstance(team, dict):
            continue
        tid = _norm_id(team.get("id") or "")
        if not tid:
            continue
        name = str(team.get("name") or "").strip()
        variants = {name, name.replace(" ", "")} - {""}
        for v in variants:
            for pos in _find_all(masked, v):
                cands.append((pos, pos + len(v), tid))
        for hit in re.finditer(_LB + re.escape(tid) + _RB + r"(?:\s*팀)?", masked, re.IGNORECASE):
            cands.append((hit.start(), hit.end(), tid))
    return sorted(_claim(cands, [], "team", text), key=lambda m: m.start)


_ROOM_OFFICE: dict[str, str] = {}
_ROOM_TERMS: list[tuple[str, str]] = []  # (표기, display_name), 긴 것 먼저
for _entry in list(GUNSAN_ROOM_CATALOG) + list(SEOUL_ROOM_CATALOG):
    _display = str(_entry.get("display_name") or "").strip()
    if not _display:
        continue
    _ROOM_OFFICE[_display] = str(_entry.get("office") or "gunsan").strip()
    _ROOM_TERMS.append((_display, _display))
    for _alias in _entry.get("aliases") or []:
        _alias_s = str(_alias).strip()
        if _alias_s:
            _ROOM_TERMS.append((_alias_s, _display))
_ROOM_TERMS.sort(key=lambda t: len(t[0]), reverse=True)


def _room_candidates(masked: str) -> list[tuple[int, int, str]]:
    cands: list[tuple[int, int, str]] = []
    for term, display in _ROOM_TERMS:
        if _LATIN_TOKEN_RE.match(term):
            for hit in re.finditer(_LB + re.escape(term) + _RB, masked, re.IGNORECASE):
                cands.append((hit.start(), hit.end(), display))
        else:
            for pos in _find_all(masked, term):
                cands.append((pos, pos + len(term), display))
    return cands


def find_mentions(
    text: str,
    members: list[dict[str, Any]],
    *,
    sender_email: str = "",
) -> list[Mention]:
    masked, mentions = _mask_emails(text)
    claimed: list[Mention] = list(mentions)

    members_found = _claim(_member_candidates(masked, members), claimed, "member", text)
    claimed += members_found

    teams = teams_from_members(members)
    team_cands: list[tuple[int, int, str]] = []
    for tm in find_team_mentions(masked, teams):
        team_cands.append((tm.start, tm.end, tm.value))
    own_team = _team_of_sender(sender_email, members)
    if own_team:
        for hit in _OUR_TEAM_RE.finditer(masked):
            team_cands.append((hit.start(), hit.end(), own_team))
    teams_found = _claim(team_cands, claimed, "team", text)
    claimed += teams_found

    rooms_found = _claim(_room_candidates(masked), claimed, "room", text)
    claimed += rooms_found

    # 지역어는 회의실 표기("서울룸") 안에 있으면 지역이 아니다. 팀 스팬과는 겹쳐도 된다.
    regions: list[Mention] = []
    for pattern, code in _REGION_PATTERNS:
        for hit in pattern.finditer(masked):
            if any(r.overlaps(hit.start(), hit.end()) for r in rooms_found):
                continue
            regions.append(Mention("region", hit.start(), hit.end(), _attach_suffix(text, hit.end()), code))

    bulks: list[Mention] = []
    for hit in _COLLECTIVE_RE.finditer(masked):
        bulks.append(Mention("bulk", hit.start(), hit.end(), _attach_suffix(text, hit.end()), hit.group(0)))
    # "멤버들 김민수"처럼 바로 뒤에 사람 이름이 오면 집합 표현이 아니라 호칭이다.
    bulks = [
        b
        for b in bulks
        if not any(
            m.start >= b.suffix_end and _GAP_RE.match(text[b.suffix_end : m.start]) and text[b.suffix_end : m.start].strip() == ""
            for m in members_found
        )
    ]

    verbs = [
        Mention("verb", hit.start(), hit.end(), hit.end(), hit.group(0))
        for hit in _EXCL_VERB_RE.finditer(masked)
    ]

    out = mentions + members_found + teams_found + rooms_found + regions + bulks + verbs
    out.sort(key=lambda m: (m.start, m.end))
    return out


def _has_locative(text: str, m: Mention) -> bool:
    return text[m.end : m.suffix_end].endswith(_LOCATIVE_TAILS)


def _preceding_team_chain(text: str, anchor: Mention, entities: list[Mention]) -> list[Mention]:
    """anchor(집합표현) 바로 앞에 나열된 팀 멘션들 — 간격 규칙으로 이어진 만큼."""
    chain: list[Mention] = []
    next_start = anchor.start
    for p in reversed([e for e in entities if e.start < anchor.start and e is not anchor]):
        gap = text[p.suffix_end : next_start] if p.suffix_end < next_start else ""
        if not _GAP_RE.match(gap):
            break
        if p.kind != "team":
            break
        chain.append(p)
        next_start = p.start
    return chain


def apply_negation(text: str, mentions: list[Mention]) -> None:
    """제외동사 앞의 나열 체인을 부정 처리한다.

    체인 규칙: (1) 앞 멘션과의 간격이 공백/쉼표/및/그리고만이어야 한다 (2) 사람·회의실·지역 카테고리는
    섞이지 않는다 (3) 처소격(로/에서)이 붙은 멘션은 동사 직전일 때만 들어간다
    (4) 부정된 집합표현 앞의 팀도 함께 부정한다("PC2팀 팀원들 빼고").
    """
    entities = sorted([m for m in mentions if m.kind in _CATEGORY], key=lambda m: m.start)
    for verb in [m for m in mentions if m.kind == "verb"]:
        chain: list[Mention] = []
        category: str | None = None
        next_start = verb.start
        for p in reversed([e for e in entities if e.suffix_end <= verb.start]):
            gap = text[p.suffix_end : next_start]
            if not _GAP_RE.match(gap):
                break
            cat = _CATEGORY[p.kind]
            if category is None:
                category = cat
            elif cat != category:
                break
            if _has_locative(text, p) and chain:
                break
            if p.kind in ('bulk', 'team') and chain and re.search(r'[,、]', gap):
                # 'PC2팀 팀원들, minsu@x.com은 빼고' — 쉼표 다음의 사람 제외는 앞 절의 집합/팀까지
                # 삼키지 않는다. 사람-사람 나열(김민수, 이지은 빼고)은 쉼표를 건너 이어진다.
                break
            chain.append(p)
            next_start = p.start
        for p in chain:
            p.negated = True
            if p.kind == "bulk":
                for t in _preceding_team_chain(text, p, entities):
                    t.negated = True


def resolve_attendees(
    text: str,
    mentions: list[Mention],
    members: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """본문 순서로 참석자 합성. 이메일은 멤버 여부와 무관하게 추가/제외된다."""
    entities = sorted([m for m in mentions if m.kind in _CATEGORY], key=lambda m: m.start)
    by_email = {
        str(m.get("email") or "").strip(): str(m.get("name") or "").strip()
        for m in members
        if str(m.get("email") or "").strip()
    }
    live_teams = {e.value for e in entities if e.kind == "team" and not e.negated}
    negated_emails = {e.value for e in entities if e.kind in ("member", "email") and e.negated}

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(email: str, name: str) -> None:
        if email and email not in seen and email not in negated_emails:
            out.append({"name": name, "email": email})
            seen.add(email)

    for e in entities:
        if e.negated:
            continue
        if e.kind in ("member", "email"):
            _add(e.value, by_email.get(e.value, ""))
        elif e.kind == "bulk":
            chain = [t for t in _preceding_team_chain(text, e, entities) if not t.negated]
            team_ids = [t.value for t in chain]
            if not team_ids and len(live_teams) == 1:
                team_ids = list(live_teams)
            for tid in dict.fromkeys(team_ids):
                for person in team_members_by_id(tid, members):
                    _add(person["email"], person["name"])
    return out


def resolve_room_and_region(text: str, mentions: list[Mention]) -> tuple[str, str, str]:
    """(room_name_keyword, room_office, region). 처소격이 붙은 멘션 우선, 아니면 마지막 멘션."""
    rooms = [m for m in mentions if m.kind == "room" and not m.negated]
    room: Mention | None = None
    for r in rooms:
        if _has_locative(text, r):
            room = r
    if room is None and rooms:
        room = rooms[-1]
    room_name = room.value if room else ""
    room_office = _ROOM_OFFICE.get(room_name, "") if room_name else ""

    regions = [m for m in mentions if m.kind == "region" and not m.negated]
    best: tuple[int, int] | None = None
    picked = ""
    for idx, r in enumerate(regions):
        suffix = text[r.end : r.suffix_end]
        if re.match(r"\s*(?:의\s*)?회의실", text[r.suffix_end :]):
            score = 3
        elif suffix.endswith(("로", "으로")):
            score = 2
        elif suffix.endswith(("에", "에서")):
            score = 1
        else:
            score = 0
        if best is None or (score, idx) >= best:
            best = (score, idx)
            picked = r.value
    if best is not None and best[0] == 0 and len({r.value for r in regions}) >= 2:
        picked = ""
    return room_name, room_office, (picked or room_office)


def resolve_team_id_from_text(text: str, teams: list[dict[str, str]]) -> str | None:
    """입력란용 단일 결과 — 가장 긴 팀 멘션. 언급이 없으면 None(발신자 팀 폴백 없음)."""
    found = find_team_mentions(text or "", teams)
    if not found:
        return None
    longest = max(found, key=lambda m: (m.end - m.start, -m.start))
    return longest.value


def exact_member_matches(query: str, members: list[dict[str, Any]]) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in members:
        email = str(m.get("email") or "").strip()
        if not email or email in seen:
            continue
        name = str(m.get("name") or "").strip()
        nicks = {str(n).strip().lower() for n in (m.get("nickname") or []) if str(n).strip()}
        if q == name or q.lower() in nicks or q.lower() == email.lower():
            out.append({"name": name, "email": email})
            seen.add(email)
    return out
