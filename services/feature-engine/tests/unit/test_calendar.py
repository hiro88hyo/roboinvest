from __future__ import annotations

from datetime import date

from feature_engine.calendar import (
    is_first_business_day_of_month,
    is_first_business_day_of_week,
    is_tse_business_day,
)


def test_is_tse_business_day_weekend_is_false() -> None:
    # 2026-01-17 は土曜日
    assert not is_tse_business_day(date(2026, 1, 17))
    # 2026-01-18 は日曜日
    assert not is_tse_business_day(date(2026, 1, 18))


def test_is_tse_business_day_year_end_year_start() -> None:
    assert not is_tse_business_day(date(2026, 12, 31))
    assert not is_tse_business_day(date(2026, 1, 1))
    assert not is_tse_business_day(date(2026, 1, 2))
    assert not is_tse_business_day(date(2026, 1, 3))


def test_is_tse_business_day_holiday_is_false() -> None:
    # 2026-01-01 は元日 (祝日 & 年始休)
    assert not is_tse_business_day(date(2026, 1, 1))
    # 2026-01-12 は成人の日 (月曜)
    assert not is_tse_business_day(date(2026, 1, 12))


def test_is_tse_business_day_normal_weekday_is_true() -> None:
    # 2026-01-05 月曜 (平日・非祝日)
    assert is_tse_business_day(date(2026, 1, 5))


def test_is_first_business_day_of_week_monday_after_holiday() -> None:
    # 2026-01-05 は月曜 (1-1..1-3 は休み、1-4 は日曜)
    assert is_first_business_day_of_week(date(2026, 1, 5))
    # 2026-01-06 火曜は前日が営業日なので False
    assert not is_first_business_day_of_week(date(2026, 1, 6))


def test_is_first_business_day_of_week_when_monday_is_holiday() -> None:
    # 2026-01-12 月曜は成人の日。週初営業日は 1-13 火曜
    assert not is_first_business_day_of_week(date(2026, 1, 12))
    assert is_first_business_day_of_week(date(2026, 1, 13))


def test_is_first_business_day_of_month_skips_year_start() -> None:
    # 1/1-1/3 休 + 1/4-1/5 は 日・月。月初営業日は 2026-01-05 (月)
    assert is_first_business_day_of_month(date(2026, 1, 5))
    assert not is_first_business_day_of_month(date(2026, 1, 6))


def test_is_first_business_day_of_month_normal() -> None:
    # 2026-02 は 2/2 月曜が月初営業日 (2/1 は日曜)
    assert is_first_business_day_of_month(date(2026, 2, 2))
    assert not is_first_business_day_of_month(date(2026, 2, 1))
