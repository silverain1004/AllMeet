"""상태(날짜) 형식 status 매크로 색상 판정 (ERP2) + 기존 % 폴백 회귀."""
from __future__ import annotations

import re

from domains.weekly_meeting.page_html import (
    apply_bold_adjacent_status_chips,
    apply_status_chip_colors,
)


def _macro(title: str, colour: str | None = None) -> str:
    colour_part = f'<ac:parameter ac:name="colour">{colour}</ac:parameter>' if colour else ""
    return (
        '<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>{colour_part}'
        "</ac:structured-macro>"
    )


def _colour_of(html: str) -> str | None:
    import re

    m = re.search(r'<ac:parameter ac:name="colour">([^<]*)</ac:parameter>', html)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 상태(날짜) 형식 — 상태 텍스트 기준
# ---------------------------------------------------------------------------

def test_label_done_maps_green():
    assert _colour_of(apply_status_chip_colors(_macro("완료(8/6)"))) == "Green"


def test_label_done_scheduled_maps_green():
    assert _colour_of(apply_status_chip_colors(_macro("완료예정(8/13)"))) == "Green"


def test_label_in_progress_maps_yellow():
    assert _colour_of(apply_status_chip_colors(_macro("진행중(8/9~8/13)"))) == "Yellow"


def test_label_waiting_maps_grey():
    assert _colour_of(apply_status_chip_colors(_macro("대기(~8/13)"))) == "Grey"


def test_label_on_hold_maps_grey():
    assert _colour_of(apply_status_chip_colors(_macro("보류(8/13~)"))) == "Grey"


def test_label_whitespace_variants_still_match():
    assert _colour_of(apply_status_chip_colors(_macro(" 완료 예정 (8/13)"))) == "Green"
    assert _colour_of(apply_status_chip_colors(_macro("진행 중(8/6~8/13)"))) == "Yellow"
    assert _colour_of(apply_status_chip_colors(_macro("완료 (8/6)"))) == "Green"


def test_label_date_with_scheduled_day_marker_e():
    assert _colour_of(apply_status_chip_colors(_macro("완료예정(8/E)"))) == "Green"
    assert _colour_of(apply_status_chip_colors(_macro("진행중(8/E~8/13)"))) == "Yellow"
    assert _colour_of(apply_status_chip_colors(_macro("대기(8/6~8/E)"))) == "Grey"


def test_label_date_with_dash_separator():
    # 실데이터에 '~' 뿐 아니라 '-' 구분자도 쓰임 (예: '7/27-8/24', '08/04-8/14')
    assert _colour_of(apply_status_chip_colors(_macro("진행중(7/27-8/24)"))) == "Yellow"
    assert _colour_of(apply_status_chip_colors(_macro("진행중(08/04-8/14)"))) == "Yellow"


def test_label_date_placeholder_dash_only():
    # 날짜 미정 상태로 '-' 만 들어간 경우도 실데이터에 존재
    assert _colour_of(apply_status_chip_colors(_macro("대기(-)"))) == "Grey"


def test_progress_rate_chip_is_not_touched():
    # '진척률(60%)' 은 상태(날짜) 5종 라벨이 아닌 별개의 %기반 칩 — 건드리지 않음
    html = _macro("진척률(60%)", colour="Blue")
    assert apply_status_chip_colors(html) == html


def test_label_updates_existing_colour_param():
    html = _macro("완료(8/6)", colour="Grey")
    assert _colour_of(apply_status_chip_colors(html)) == "Green"


def test_label_unrecognized_text_leaves_colour_unchanged():
    html = _macro("알수없음(8/6)", colour="Grey")
    assert apply_status_chip_colors(html) == html


def test_label_recognized_but_date_shape_invalid_leaves_unchanged():
    html = _macro("완료(어제)", colour="Yellow")
    assert apply_status_chip_colors(html) == html


# ---------------------------------------------------------------------------
# 괄호 형식이 아니면 기존 % 로직으로 폴백 (회귀)
# ---------------------------------------------------------------------------

def test_percent_fallback_zero_is_grey():
    assert _colour_of(apply_status_chip_colors(_macro("0%"))) == "Grey"


def test_percent_fallback_partial_is_yellow():
    assert _colour_of(apply_status_chip_colors(_macro("50%"))) == "Yellow"


def test_percent_fallback_hundred_is_green():
    assert _colour_of(apply_status_chip_colors(_macro("100%"))) == "Green"


def test_percent_fallback_empty_is_grey():
    assert _colour_of(apply_status_chip_colors(_macro(""))) == "Grey"


def test_no_status_macro_returns_unchanged():
    html = "<p>일반 텍스트</p>"
    assert apply_status_chip_colors(html) == html


def _tag_counts_balanced(html: str) -> bool:
    return len(re.findall(r"<strong>", html)) == len(re.findall(r"</strong\s*>", html, re.IGNORECASE))


def _macro_titles(html: str) -> list[str]:
    out = []
    for m in re.finditer(
        r'<ac:structured-macro ac:name="status"><ac:parameter ac:name="title">([^<]*)</ac:parameter>'
        r'<ac:parameter ac:name="colour">([^<]*)</ac:parameter></ac:structured-macro>',
        html,
    ):
        out.append((m.group(1), m.group(2)))
    return out


# ---------------------------------------------------------------------------
# 볼드 제목 옆 일반 텍스트 → 실제 Status 칩 변환 (실 데이터 형태 기반)
# ---------------------------------------------------------------------------

def test_bold_adjacent_pattern_a_whole_annotation_inside_strong():
    # 실데이터: '(06/24~07/03-&gt;08/11, 완료 예정)' 전체가 <strong> 안, 제목 자체도
    # '제조원가분석(견적용)' 처럼 무관한 괄호를 포함
    html = (
        '<p><strong>[SR2605-01307][경영기획본부] &lsquo;제조원가분석(견적용)&rsquo; '
        '출력값 일부 변경 요청의 건'
        '<span style="color: rgb(76,154,255);">(06/24~07/03-&gt;08/11, 완료 예정)</span>'
        '</strong></p>'
    )
    out = apply_bold_adjacent_status_chips(html)
    assert _tag_counts_balanced(out)
    titles = _macro_titles(out)
    assert titles == [("완료예정(06/24~07/03-&gt;08/11)", "Green")]
    # 제목 안의 무관한 괄호 '(견적용)'은 그대로 남아있어야 함
    assert "제조원가분석(견적용)" in out


def test_bold_adjacent_pattern_b_split_across_strong_boundary():
    # 실데이터: 상태가 </strong> 안쪽에서 끝나고, 콤마+날짜는 </strong> 밖의 별도 span
    html = (
        '<p><strong>SAP 시스템 AI Description 자동 생성'
        '<span style="color: rgb(76,154,255);">(완료 예정</span></strong>, '
        '<span style="color: rgb(76,154,255);">8/13)</span></p>'
    )
    out = apply_bold_adjacent_status_chips(html)
    assert _tag_counts_balanced(out)
    titles = _macro_titles(out)
    assert titles == [("완료예정(8/13)", "Green")]


def test_bold_adjacent_not_converted_when_not_bold():
    # 볼드가 아닌 곳에 있는 '(완료, 8/6)' 는 건드리지 않음
    html = '<p>참고: 다른 팀 일정<span style="color: rgb(76,154,255);">(완료, 8/6)</span></p>'
    assert apply_bold_adjacent_status_chips(html) == html


def test_bold_adjacent_bare_jinhaeng_yejeong_not_recognized():
    # '진행 예정' 은 5개 키워드(대기/보류/진행중/완료/완료예정)에 없어 보수적으로 미변환
    html = (
        '<p><strong>수선비 현황 리포트 개발의 건'
        '<span style="color: rgb(76,154,255);">(08/10~08/31, 진행 예정)</span>'
        '</strong></p>'
    )
    assert apply_bold_adjacent_status_chips(html) == html


def test_bold_adjacent_bare_yejeong_alone_not_recognized():
    # 단독 '예정' 도 5개 키워드에 없어 미변환
    html = (
        '<p><strong>소형정정 X번들 이관 검토'
        '<span style="color: rgb(7,71,166);">(07/30, 예정)</span>'
        '</strong></p>'
    )
    assert apply_bold_adjacent_status_chips(html) == html


def test_bold_adjacent_pattern_c_date_also_wrapped_in_own_strong():
    # 실데이터: 상태 부분과 날짜 부분이 각각 별도의 <strong>으로 감싸여 있음
    # (콤마 앞뒤로 <strong> 이 한번씩 닫히고 다시 열림) — 태그 균형이 깨지면 안 됨
    html = (
        '<p><strong>AI Description 자동 생성'
        '<span style="color: rgb(76,154,255);">(완료 예정</span></strong>, '
        '<strong><span style="color: rgb(76,154,255);">8/13)</span></strong></p>'
    )
    out = apply_bold_adjacent_status_chips(html)
    assert _tag_counts_balanced(out)
    assert _macro_titles(out) == [("완료예정(8/13)", "Green")]


def test_bold_adjacent_bare_label_no_date():
    html = '<p><strong>테스트 배포 완료<span style="color: rgb(76,154,255);">(완료)</span></strong></p>'
    out = apply_bold_adjacent_status_chips(html)
    assert _macro_titles(out) == [("완료", "Green")]
