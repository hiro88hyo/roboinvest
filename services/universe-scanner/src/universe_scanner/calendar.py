from __future__ import annotations

from datetime import date, timedelta

import jpholiday


def is_tse_business_day(d: date) -> bool:
    """東証の営業日判定。土日・祝日・年末年始 (12/31, 1/1-1/3) を除く。"""
    if d.weekday() >= 5:
        return False
    if jpholiday.is_holiday(d):
        return False
    if d.month == 12 and d.day == 31:
        return False
    return not (d.month == 1 and d.day <= 3)


def previous_business_day(d: date) -> date:
    """`d` 直前の営業日を返す (`d` 自身は含まない)。"""
    prev = d - timedelta(days=1)
    while not is_tse_business_day(prev):
        prev -= timedelta(days=1)
    return prev


def business_days_back(d: date, n: int) -> date:
    """`d` を含む直近 `n` 営業日のうち最古の営業日を返す。

    `n=1` は `d` が営業日ならそれ自身、営業日でなければ直前の営業日。
    """
    if n <= 0:
        raise ValueError("n must be >= 1")
    cursor = d
    while not is_tse_business_day(cursor):
        cursor -= timedelta(days=1)
    for _ in range(n - 1):
        cursor = previous_business_day(cursor)
    return cursor
