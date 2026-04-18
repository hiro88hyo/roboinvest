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


def is_first_business_day_of_week(d: date) -> bool:
    """`d` が当該週 (月-日) で最初の営業日なら True。"""
    if not is_tse_business_day(d):
        return False
    for offset in range(1, d.weekday() + 1):
        if is_tse_business_day(d - timedelta(days=offset)):
            return False
    return True


def is_first_business_day_of_month(d: date) -> bool:
    """`d` が当月で最初の営業日なら True。"""
    if not is_tse_business_day(d):
        return False
    return all(not is_tse_business_day(d.replace(day=day)) for day in range(1, d.day))
