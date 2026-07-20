"""expert_finder 라이브 버그 수정 — 동일인 중복·팀 오표시·키워드 추출."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 동일인 병합 (rank 2·3 에 같은 사람이 뜨던 문제)
# ---------------------------------------------------------------------------


def test_merge_same_person_combines_duplicate_names():
    from domains.expert_finder.scoring import _merge_same_person

    entries = [
        {"email": "a@x.com", "name": "송원용", "score": 10.0, "hits_by_source": {"confluence": 2}, "evidence": []},
        {"email": "b@x.com", "name": "송원용", "score": 6.0, "hits_by_source": {"gmail": 1}, "evidence": []},
        {"email": "c@x.com", "name": "김철수", "score": 4.0, "hits_by_source": {"drive": 1}, "evidence": []},
    ]
    out = _merge_same_person(entries)
    names = [e["name"] for e in out]
    assert names.count("송원용") == 1  # 중복 병합
    merged = next(e for e in out if e["name"] == "송원용")
    assert merged["score"] == 16.0  # 점수 합산
    assert merged["hits_by_source"] == {"confluence": 2, "gmail": 1}
    assert len(out) == 2


def test_merge_same_person_keeps_unnamed_separate():
    from domains.expert_finder.scoring import _merge_same_person

    entries = [
        {"email": "a@x.com", "name": "", "score": 3.0, "hits_by_source": {}, "evidence": []},
        {"email": "b@x.com", "name": "", "score": 2.0, "hits_by_source": {}, "evidence": []},
    ]
    out = _merge_same_person(entries)
    assert len(out) == 2  # 이름 없으면 이메일 단위 유지


# ---------------------------------------------------------------------------
# displayName → email 정확 매칭 (loose substring 오매칭 방지)
# ---------------------------------------------------------------------------


def test_lookup_email_exact_match_only():
    from domains.expert_finder.public_sources import lookup_email_by_display_name

    pool = [
        {"name": "송원용", "nickname": [], "email": "wy@mes2.com", "team_name": "MES2"},
        {"name": "송원", "nickname": [], "email": "other@x.com", "team_name": "PC2"},
    ]
    # 정확 일치만 — '송원'(부분일치)으로 '송원용' 이메일을 잘못 반환하지 않음
    assert lookup_email_by_display_name("송원용", pool) == "wy@mes2.com"
    assert lookup_email_by_display_name("송원", pool) == "other@x.com"
    # 풀에 없는 이름은 None (과거엔 substring 으로 아무나 매칭됐음)
    assert lookup_email_by_display_name("원", pool) is None


# ---------------------------------------------------------------------------
# 키워드 추출 — '생산관리 누가 제일 잘해?' 류
# ---------------------------------------------------------------------------


def test_keyword_extract_who_is_best_pattern():
    from domains.expert_finder.keyword_extract import extract_keyword

    assert extract_keyword("생산관리 누가 제일 잘해?") == "생산관리"
    assert extract_keyword("생산관리 누가 제일 잘해") == "생산관리"
    # 기존 패턴 회귀 확인
    assert extract_keyword("Kafka 전문가 추천해줘") == "Kafka"
    assert extract_keyword("열처리 담당자 누구야?") == "열처리"
