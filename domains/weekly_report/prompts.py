"""주간보고초안 Vertex Gemini 프롬프트 + JSON response_schema."""

from __future__ import annotations

import re
from typing import Any

# Jira 키 패턴 — 예: "AMT-9", "MES-123". 2~10자 대문자 prefix + 숫자.
_JIRA_KEY_RE = re.compile(r"\b([A-Z]{2,10})-\d+\b")
# SR 번호 — 예: "SR2605-00483".
_SR_REF_RE = re.compile(r"\bSR\d{4}-\d+\b", re.IGNORECASE)

# Jira prefix → 상위 프로젝트(task umbrella) 이름. LLM 이 컴포넌트명을 별도 task 로 쪼개지
# 못하게 코드 단에서 결정론적으로 묶음. 매핑 없는 prefix 는 폴백 이름(`{prefix} 프로젝트`)으로.
# 다른 팀이 다른 prefix 를 쓰면 여기에 추가.
_KNOWN_PROJECTS: dict[str, str] = {
    "AMT": "[AllMeet] 주간보고 자동화",
}

# 알림/시스템/회신 prefix — 정규화 시 제거 (대상이 광범위해 다양한 회사 메일에 적용).
_NOISE_PREFIX_RE = re.compile(
    r"^\s*("
    r"\[ITS[^\]]*\]"
    r"|\[결재[^\]]*\]"
    r"|\[Jira\]"
    r"|\[VNTG\][^\]]*"
    r"|\[공지\]"
    r"|\[전사[^\]]*\]"
    r"|\[Slashpage\]"
    r"|Re:|RE:|Fw:|FW:|Fwd:|FWD:"
    r")\s*"
)
# 트레일링 상태 표기 — '(승인완료)', '(처리완료)', '(반려)' 등.
_TRAILING_STATUS_RE = re.compile(
    r"\s*\([^)]*(?:완료|진행중|반려|취소|승인|접수)[^)]*\)\s*$"
)

def _normalize_subject(subject: str) -> str:
    """알림 prefix·상태 표기를 반복 제거해 사안 본질 제목만 남김."""
    s = (subject or "").strip()
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_PREFIX_RE.sub("", s).strip()
    s = _TRAILING_STATUS_RE.sub("", s).strip()
    return s


def _group_key(subject: str) -> str:
    """그룹화 키. 우선순위: SR 번호 > Jira prefix > 정규화 제목.

    SR/Jira 같은 식별자가 있으면 알림 prefix 차이와 무관하게 같은 사안으로 묶음.
    식별자가 없으면 정규화된 제목으로 묶어 알림 시리즈를 자동 합침.
    """
    s = subject or ""
    m = _SR_REF_RE.search(s)
    if m:
        return f"SR:{m.group(0).upper()}"
    m = _JIRA_KEY_RE.search(s)
    if m:
        return f"JIRA:{m.group(1)}"
    norm = _normalize_subject(s)
    return f"TITLE:{norm}" if norm else ""


def _group_label_and_type(
    key: str, count: int, sample_subject: str
) -> tuple[str, str]:
    """그룹의 (사람 친화적 라벨, 타입 힌트). 타입 힌트는 LLM 에게 그룹 본질을 알림."""
    if key.startswith("JIRA:"):
        prefix = key[len("JIRA:") :]
        label = _KNOWN_PROJECTS.get(prefix, f"{prefix} Jira 프로젝트")
        return label, f"Jira {prefix}-* {count}건"
    if key.startswith("SR:"):
        sr_id = key[len("SR:") :]
        norm = _normalize_subject(sample_subject)
        label = f"{sr_id} {norm}".strip() if norm else sr_id
        return label, f"SR 관련 메일 {count}건"
    if key.startswith("TITLE:"):
        return key[len("TITLE:") :], f"같은 사안 알림 {count}건"
    return key, f"관련 메일 {count}건"


def _group_emails(
    emails: list[dict[str, Any]],
) -> tuple[
    list[tuple[str, str, list[dict[str, Any]]]],
    list[dict[str, Any]],
]:
    """이메일 사전 그룹화. 2건 이상 묶이는 것만 그룹, 나머지는 단독.

    Returns:
        (groups, singles):
        - groups: ``[(label, type_hint, items), ...]`` — 첫 등장 순서.
        - singles: 그룹에 속하지 못한 단독 메일.
    """
    by_key: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    keyless: list[dict[str, Any]] = []
    for m in emails or []:
        subject = str(m.get("subject") or "")
        key = _group_key(subject)
        if not key:
            keyless.append(m)
            continue
        if key not in by_key:
            order.append(key)
        by_key.setdefault(key, []).append(m)

    groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    singles: list[dict[str, Any]] = []
    for key in order:
        items = by_key[key]
        if len(items) < 2:
            singles.append(items[0])
            continue
        first_subject = str(items[0].get("subject") or "")
        label, type_hint = _group_label_and_type(key, len(items), first_subject)
        groups.append((label, type_hint, items))
    singles.extend(keyless)
    return groups, singles


_SYSTEM = (
    "당신은 'All-Meet' 의 주간보고 초안 작성 도우미입니다.\n"
    "사용자의 한 주 활동(회의 / 작성 문서 / Confluence 페이지 / 메일 / 일정) 데이터를 받아\n"
    "팀 주간회의에 보고할 초안을 한국어로 정리합니다.\n\n"
    "출력 구조 — 두 카테고리로 분류:\n"
    "- projects(프로젝트): 신규 개발·설계·기능 구축·산출물 작성·연구 등 주도적 업무\n"
    "- operations(운영지원): 시스템 유지보수, 정기 운영, SR/이슈 대응, 요청 처리, 협조 요청 등 지원성 업무\n\n"
    "각 카테고리는 큰 단위 업무(task) 의 배열입니다.\n"
    "- task 형식: **'[프로젝트/시스템] 업무명  -%' 로 통일**. 진행률 자리는 항상 ' -%' 로 끝맺으세요.\n"
    "  진행률은 데이터로 알 수 없는 본인 판단 영역이라 사용자가 직접 채울 자리만 마련하는 것입니다.\n"
    "  ※ 데이터에 명시적 '완료/종료' 신호(예: '[ITS 변경종료 알림] X')가 있어 사용자가 명확히 끝낸 건만\n"
    "    예외적으로 ' 100%' 로 표기. 추측은 금지.\n"
    "  예) '[Hackathon] Agentic Workflow  -%', '[MES] 에버가드 IDC 운영 일지 신규 화면 개발  100%',\n"
    "      '[AllMeet] 주간보고 자동화  -%', '[E-OUT] 시스템 유지보수  -%'.\n"
    "  대괄호 prefix 가 자연스럽지 않은 일반 운영 업무는 prefix 없이도 허용 (예: 'TFS 권한 부여 요청  -%').\n"
    "- details: 그 업무를 구성한 세부 활동·산출물. **distinct 한 이슈·산출물·요청은 한 줄씩 따로 적으세요**.\n"
    "  한 task 안에 5개 이슈가 있으면 details 5줄. 한 줄로 압축하면 안 됨.\n"
    "  - **task 명을 details 첫 줄에 그대로 반복 금지 (동어반복)**. details 는 task 의 *구성요소·결과·진척* 이지\n"
    "    task 자체가 아닙니다. 데이터가 task 헤더 한 줄짜리뿐이라면 details 는 빈 배열로 두세요.\n"
    "    틀린 예: task='[PC2팀] 업무 회의  -%', details=['[PC2팀] 업무 회의']  ← task 가 그대로 반복\n"
    "    옳은 예: task='[PC2팀] 업무 회의  -%', details=['업무 회의 진행 및 회의록 공유',\n"
    "             'PC2팀 실행 과제 도출 회의 작성']\n"
    "  - 형식: '{식별자}: {설명}' — 식별자가 없으면 설명만. 행위 동사로 끝내세요.\n"
    "  - **행위 동사로 자연스럽게 마무리하세요**. 데이터 신호를 보고 가장 구체적인 동사를 선택:\n"
    "      · 신규 구축·개발 종료 신호 → ' 개발 완료', ' 구축 완료'\n"
    "      · 변경·수정 종료 신호 → ' 수정 완료', ' 개선 완료', ' 변경 완료'\n"
    "      · 산출물(문서/제안서/시트) → ' 작성'\n"
    "      · 회의 진행 + 회의록 산출 → ' 진행 및 회의록 공유' / ' 작성'\n"
    "      · 요청·SR → ' 요청'\n"
    "      · 확인·검토 → ' 확인', ' 검토', ' 피드백 완료'\n"
    "      · 산정·견적 → ' 공수 산정' / ' 견적 작성'\n"
    "      · '~드립니다', '~합니다' 같은 정중표현은 제거하고 동사 어간만\n"
    "  - SR 번호·Jira 키·문서명 같은 식별자는 데이터에 있는 그대로 포함\n"
    "    (예: '[SR2605-00483] 특수강 실적등록 에러 대응', 'AMT-9: Confluence 자동화 로직 개발').\n"
    "  - 담당자 부기는 **본인이 아닌 다른 사람일 때만** (괄호) 로 (예: '(홍현기 매니저 요청)', '(생산관리팀 송원용매니저)').\n"
    "  - **다음은 절대 부기하지 마세요 (의미 없는 노이즈)**:\n"
    "    · 본인 이메일/본인 이름 (이미 카드 헤더에 있음)\n"
    "    · Jira 메일 제목의 '최은비 assign' 같은 시스템 부착 텍스트\n"
    "    · '귀하에게 할당했습니다' 같은 Jira 알림 문구\n"
    "  - 데이터에 없는 진척률·기한·담당자·구체 산출물(화면 ID 리스트 등)은 절대 추측·생성 금지.\n"
    "    데이터에서 보이지 않는 디테일은 그냥 비워두세요 — 사용자가 직접 채웁니다.\n"
    "- **deadline 필드**: 마감기한이 데이터(메일 제목/본문, 캘린더 이벤트 날짜, 문서 제목 등)에서\n"
    "  '마감', '기한', '~까지', 날짜 표기(MM/DD, MM월DD일) 등으로 **명시**된 경우에만 입력.\n"
    "  · 정확한 날짜를 알면 'M/D' 형식 — 앞자리 0 없이 (예: '6/20', '6/5').\n"
    "  · 달(월)만 알고 정확한 날짜는 모르면 'M/E' (그 달 말일, 예: 6월 안이면 '6/E').\n"
    "  · 날짜·월 모두 단서가 없으면 반드시 빈 문자열 ''. 절대 추측 금지.\n\n"
    "압축 vs 분리 — 자주 헷갈리는 부분, 명확히:\n"
    "  ◯ 압축 OK: **같은 SR/이슈의 알림 시리즈** (접수 → 결재 → 변경 → 종료) 는 한 줄로\n"
    "     예: '[세아웍스] 경조사 게시판 데이터 요청 — 접수·결재·종료 완료'\n"
    "  ✗ 압축 금지: **같은 프로젝트 안의 distinct 이슈/산출물** 은 절대 한 줄로 합치지 않음\n"
    "     올바른 예 (4줄로 분리, assign 같은 시스템 메타 제거):\n"
    "       ◦ AMT-9: Confluence 자동화 로직 개발\n"
    "       ◦ AMT-11: Vertex AI Search 셋업 및 검색 기능 구현\n"
    "       ◦ AMT-36: RAG 구축 (Vertex AI Search)\n"
    "       ◦ AMT-37: RAG 연동 (Drive, Calendar, Confluence)\n"
    "     틀린 예:\n"
    "       ◦ AMT-9, AMT-11, AMT-36, AMT-37 Jira 이슈 작업   ← 식별자만 나열·설명 누락\n"
    "       ◦ AMT-9: 자동화 (최은비 assign)                       ← 의미 없는 시스템 메타 부기\n"
    "       ◦ MES SR 요청 (kkami4182@vntgcorp.com)              ← 본인 이메일 부기\n\n"
    "메일 처리 룰 (중요):\n"
    "- 메일의 'from' 이 위 '## 사용자 이메일' 과 정확히 일치하는 경우 = 본인 발신 = 본인이 한 행위.\n"
    "  **본인 발신 메일은 반드시 details 에 포함하세요 (단일이라도 누락 금지)**.\n"
    "- 본인 발신이 아닌 메일은 기본적으로 '수신 정보' 라 본인 업무가 아님. 다음 두 경우만 예외적으로 포함:\n"
    "  (a) 본인이 트리거한 워크플로우의 알림 메일 (ITS 접수·결재·변경 종료 알림 등) — 같은 건의\n"
    "      본인 발신 메일이 함께 있을 때만, 알림들과 합쳐 한 줄 행위로 표현.\n"
    "  (b) 본인이 관여한 회의록/문서 업데이트 알림 — Confluence 일일 다이제스트는 무관한 정보 알림이라 제외.\n"
    "- 아래는 모두 제외 (본인 업무 아님):\n"
    "  · 동료가 보낸 '공유', '후기', '인사이트', '참석 후기' 류 정보 공유 메일\n"
    "  · Claude.ai 로그인 보안 링크, Google 보안 알림, 뉴스레터(Mermaid 등), 광고 메일\n"
    "  · 전사 공지, 노사협의회 안내, 출퇴근 체크 협조, 동호회 활동 공유 등 본인 업무와 무관한 공지\n"
    "  · Confluence 일일 다이제스트 ('일일 다이제스트를 놓치지 마세요') — 본인이 한 일이 아님\n\n"
    "알림 메일 → 행위 변환 룰 (제목 그대로 옮기지 말 것):\n"
    "- '[ITS 서비스요청 접수완료알림] X' → 'X 요청 접수'\n"
    "- '[ITS 변경종료 알림] X' → 'X 신규 구축 완료' 또는 'X 변경 완료'\n"
    "- '[결재 완료 알림] [ITS 요청서] X (승인완료)' → 'X 결재 완료'\n"
    "- '[ITS App 변경관리 프로세스 이관 알림] X' → 'X 변경관리 이관'\n"
    "- 같은 SR/이슈에 대한 여러 알림(접수 → 결재 → 변경 → 종료) 은 하나의 details 줄로 압축\n"
    "  (예: '[세아웍스] 경조사 게시판 데이터 요청 — 접수·결재·종료 완료').\n\n"
    "분류·통합 룰:\n"
    "- 같은 프로젝트/시스템/이슈에 관련된 모든 활동(회의·문서·메일·일정)은 하나의 task 로 통합하고\n"
    "  details 에 누적·진척으로 적으세요. 한 프로젝트가 여러 task 로 쪼개지면 안 됩니다.\n"
    "- **데이터에 이미 묶여 제공된 그룹은 반드시 그 그룹 단위로 하나의 task 를 만들어야 함**.\n"
    "  그룹 헤더 형식: '### {label}  ({type_hint})  — 이 그룹은 반드시 하나의 task 로 묶고, 쪼개지 마세요'.\n"
    "  type_hint 의 종류:\n"
    "    · 'Jira AMT-* X건' → AMT 프로젝트의 X개 distinct 이슈 (label 을 task 로, 각 이슈는 details 한 줄씩)\n"
    "    · 'SR 관련 메일 X건' → 같은 SR 번호의 알림 시리즈 (한 줄로 압축 OK)\n"
    "    · '같은 사안 알림 X건' → 정규화 제목이 같은 알림 시리즈 (한 줄로 압축 OK)\n"
    "  그룹에 속한 항목을 다른 task 로 옮기거나 하위 그룹으로 쪼개지 마세요.\n"
    "- **기술 컴포넌트명을 단독 task umbrella 로 쓰지 마세요**. RAG, Vertex AI Search, 메모리시스템,\n"
    "  Firestore, OAuth, 캘린더 연동 같은 이름은 상위 프로젝트의 details 한 줄로 들어갑니다.\n"
    "- 회사 시스템 약어는 그대로 유지: E-OUT, E-BIZ, MES, ITS, SAP, SR, ERP, GWS.\n"
    "- 카테고리가 비어 있으면 빈 배열로 두세요. 가짜 분류 강요 금지.\n\n"
    "## 평소 작성 스타일 참고 (이 톤·길이·구체성을 따라하세요)\n"
    "사내 보고 톤은 아래 사례처럼 **시스템 prefix + 행위 동사 + 진행률 자리** 가 기본이고,\n"
    "details 는 task 의 *구성 요소* 를 행위 동사로 풀어 적습니다.\n"
    "한 줄짜리 task 는 details 빈 배열도 OK — 억지로 채우지 마세요.\n\n"
    "예시 task / details:\n"
    "  · task: '[소형압연] DBMS 전환 안정화  -%'\n"
    "    details: ['소형압연 이슈리스트 작성 및 전달 (홍현기 매니저 요청)']\n"
    "  · task: '[1제강] 전기로 전극봉 팝업 개발 지원  100%'\n"
    "    details: ['전극봉 신규 팝업 개발 완료',\n"
    "              '기존 전극봉 팝업 관련 화면 전체 개선 완료',\n"
    "              'MP18 전극봉 실적 수정 완료',\n"
    "              '실적 전송 기능 추가']\n"
    "  · task: '[FEMS] 웹 서버 프로그램 확인  100%'\n"
    "    details: ['IT관리팀 웹 서버 구성요소 확인 요청 피드백 완료']\n"
    "  · task: 'Web 작업지시 스케줄링 마이그레이션  -%'\n"
    "    details: ['공수 산정 회의 진행']\n"
    "  · task: '[MES] 에버가드 IDC 운영 일지 신규 화면 개발  100%'\n"
    "    details: ['신규 화면 개발 완료', '변경관리 종료']\n"
    "  · task: '[MES] 에버가드 IDC 운영 일지 수정  -%'\n"
    "    details: ['수정 요청 접수·결재·현업테스트·변경관리 처리 완료']\n\n"
    "**금지 패턴** (실제로 자주 나오는 안 좋은 출력):\n"
    "  ✗ task='[PC2팀] 업무 회의' / details=['[PC2팀] 업무 회의 진행', '[PC2팀] 업무 회의 회의록 작성']\n"
    "    → task 동어반복. **올바르게**: details=['업무 회의 진행 및 회의록 공유']\n"
    "  ✗ task='에버가드 신규화면 개발' / details=['에버가드 신규화면 개발']\n"
    "    → task 동어반복 + Drive 파일명을 task 로 그대로 옮긴 흔적. 같은 사안의 다른 task '[MES] 에버가드 IDC ...' 와 통합.\n"
    "  ✗ task='2026-05-15 브레인스토밍' / details=['2026-05-15 브레인스토밍']\n"
    "    → Confluence 페이지 제목을 task 로 그대로. 상위 프로젝트 task 의 details 한 줄로 흡수.\n"
    "  ✗ 진행률 임의 추정 (데이터에 완료 신호 없는데 '100%' 표기) — 모르면 '-%' 로 두세요."
)


def build_draft_prompt(
    *, user_name: str, user_email: str = "", meeting_date: str, raw: dict[str, Any]
) -> str:
    """raw 데이터를 텍스트로 풀어 프롬프트로 만들기."""
    lines: list[str] = [
        _SYSTEM,
        "",
        f"## 사용자: {user_name}",
        f"## 사용자 이메일: {user_email}",
        f"## 주간회의 일자: {meeting_date}",
        "",
        "## 수집된 데이터",
        "",
    ]

    cal = raw.get("calendar") or []
    lines.append(f"### 회의 이력 ({len(cal)}건)")
    for e in cal:
        summary = (e.get("summary") or "-").strip()
        start = (e.get("start") or "-")[:10]
        lines.append(f"- {summary} | {start}")
    lines.append("")

    drv = raw.get("drive") or []
    lines.append(f"### Drive 파일 작성/수정 ({len(drv)}건)")
    for f in drv:
        name = (f.get("name") or "-").strip()
        mime = (f.get("mime_type") or "-").strip()
        modified = (f.get("modified_time") or "-")[:10]
        lines.append(f"- {name} ({mime}) | {modified}")
    lines.append("")

    pdrv = raw.get("personal_drive") or []
    if pdrv:
        lines.append(f"### 내 드라이브 작성/수정 ({len(pdrv)}건)")
        for f in pdrv:
            name = (f.get("name") or "-").strip()
            mime = (f.get("mime_type") or "-").strip()
            modified = (f.get("modified_time") or "-")[:10]
            lines.append(f"- {name} ({mime}) | {modified}")
        lines.append("")

    pgs = raw.get("confluence") or []
    lines.append(f"### Confluence 페이지 ({len(pgs)}건)")
    for p in pgs:
        title = (p.get("title") or "-").strip()
        modified = (p.get("modified_time") or "-")[:10]
        lines.append(f"- {title} | {modified}")
    lines.append("")

    mls = raw.get("gmail") or []
    if mls:
        # 사전 그룹화 — 같은 SR / Jira / 정규화 제목 끼리 묶음.
        # LLM 이 알림 시리즈를 별도 task 로 쪼개지 못하게 코드 단에서 결정론적으로 처리.
        # ※ 노이즈 메일(공유·로그인 링크·뉴스레터 등) 제거는 raw 수집 단계(draft._collect_all_services)
        # 에서 이미 처리됨 — 여기로 들어온 메일은 모두 본인 업무 후보.
        mail_groups, mail_singles = _group_emails(mls)
        for label, type_hint, items in mail_groups:
            lines.append(
                f"### {label}  ({type_hint})"
                f"  — 이 그룹은 반드시 하나의 task 로 묶고, 쪼개지 마세요"
            )
            for m in items:
                subject = (m.get("subject") or "-").strip()
                sender = (m.get("from") or "-").strip()
                date = (m.get("date") or "-").strip()
                lines.append(f"- {subject} ({sender}) | {date}")
            lines.append("")
        if mail_singles:
            lines.append(f"### 메일 (단독, {len(mail_singles)}건)")
            for m in mail_singles:
                subject = (m.get("subject") or "-").strip()
                sender = (m.get("from") or "-").strip()
                date = (m.get("date") or "-").strip()
                lines.append(f"- {subject} ({sender}) | {date}")
            lines.append("")

    lines.append("이 데이터를 분석해 JSON schema 에 맞게 출력하세요.")
    return "\n".join(lines)


# Gemini structured output schema (response_mime_type=application/json + response_schema).
_CATEGORY_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "task": {"type": "STRING"},
            "details": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "deadline": {"type": "STRING"},
        },
        "required": ["task", "deadline"],
    },
}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "projects": _CATEGORY_SCHEMA,
        "operations": _CATEGORY_SCHEMA,
    },
    "required": ["projects", "operations"],
}
