from __future__ import annotations

import argparse

THIS_WEEK = [
    {"title": "제조사업2센터 주간회의", "date": "2026-02-23", "start": "10:00", "end": "11:00"},
    {"title": "PC2팀 주간회의", "date": "2026-02-26", "start": "09:00", "end": "10:00"},
    {"title": "MES팀 주간회의", "date": "2026-02-26", "start": "11:00", "end": "12:00"},
    {"title": "제조ERP2팀 주간회의", "date": "2026-02-26", "start": "14:00", "end": "15:30"},
]

NEXT_WEEK = [
    {"title": "제조사업2센터 주간회의", "date": "2026-03-02", "start": "10:00", "end": "11:00"},
    {"title": "PC2팀 주간회의", "date": "2026-03-05", "start": "09:00", "end": "10:00"},
    {"title": "MES팀 주간회의", "date": "2026-03-05", "start": "11:00", "end": "12:00"},
    {"title": "제조ERP2팀 주간회의", "date": "2026-03-05", "start": "14:00", "end": "15:30"},
]


def _print_schedule(title: str, rows: list[dict[str, str]]) -> None:
    print(f"{title} 주간회의 일정 조회 중 ...")
    for i, row in enumerate(rows, start=1):
        print(f"  {i}. {row['title']}")
        print(f"     일자: {row['date']}  {row['start']} ~ {row['end']}")
    print("완료.")


def main() -> None:
    parser = argparse.ArgumentParser(description="주간회의 일정 조회 스크립트")
    parser.add_argument("--next", action="store_true", help="다음 주 일정을 출력")
    args = parser.parse_args()
    if args.next:
        _print_schedule("다음", NEXT_WEEK)
    else:
        _print_schedule("이번", THIS_WEEK)


if __name__ == "__main__":
    main()
