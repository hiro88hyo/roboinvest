from __future__ import annotations

from datetime import date, timedelta

import jpholiday


def is_tse_business_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if jpholiday.is_holiday(d):
        return False
    if d.month == 12 and d.day == 31:
        return False
    return not (d.month == 1 and d.day <= 3)


def nth_tse_business_day_after(opened_date: date, sessions: int | None) -> date | None:
    if sessions is None:
        return None
    current = opened_date
    remaining = sessions
    while remaining > 0:
        current += timedelta(days=1)
        if is_tse_business_day(current):
            remaining -= 1
    return current
