from __future__ import annotations

from datetime import date

from oms_paper.calendar import is_tse_business_day, nth_tse_business_day_after


def test_is_tse_business_day_skips_holiday() -> None:
    assert not is_tse_business_day(date(2026, 4, 29))


def test_nth_tse_business_day_after_skips_weekend_and_holiday() -> None:
    assert nth_tse_business_day_after(date(2026, 4, 24), 3) == date(2026, 4, 30)


def test_nth_tse_business_day_after_none_when_no_max_hold() -> None:
    assert nth_tse_business_day_after(date(2026, 4, 24), None) is None
