from datetime import date

from universe_scanner.calendar import (
    business_days_back,
    is_tse_business_day,
    next_business_day,
    previous_business_day,
)


def test_weekday_is_business_day():
    # 2026-04-20 (Mon) は平日かつ祝日でない
    assert is_tse_business_day(date(2026, 4, 20)) is True


def test_saturday_is_not_business_day():
    assert is_tse_business_day(date(2026, 4, 18)) is False


def test_national_holiday_is_not_business_day():
    # 2026-04-29 昭和の日
    assert is_tse_business_day(date(2026, 4, 29)) is False


def test_year_end_year_start_not_business_day():
    for d in [date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]:
        assert is_tse_business_day(d) is False


def test_previous_business_day_skips_weekend():
    # 月曜の前営業日は金曜
    assert previous_business_day(date(2026, 4, 20)) == date(2026, 4, 17)


def test_previous_business_day_skips_holiday():
    # 2026-04-30 木曜。前営業日は 4-28 火曜 (4-29 は祝日)
    assert previous_business_day(date(2026, 4, 30)) == date(2026, 4, 28)


def test_next_business_day_skips_weekend():
    assert next_business_day(date(2026, 1, 16)) == date(2026, 1, 19)


def test_next_business_day_skips_holiday():
    assert next_business_day(date(2026, 4, 28)) == date(2026, 4, 30)


def test_business_days_back_returns_oldest_of_window():
    # 2026-04-20 を含む直近 3 営業日 → 4/16(木), 4/17(金), 4/20(月) のうち最古 = 4/16
    assert business_days_back(date(2026, 4, 20), 3) == date(2026, 4, 16)


def test_business_days_back_rolls_non_business_input():
    # 4/18(土) 起点で 1 営業日 → 直前営業日の 4/17(金)
    assert business_days_back(date(2026, 4, 18), 1) == date(2026, 4, 17)
