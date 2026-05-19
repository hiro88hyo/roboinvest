from __future__ import annotations

from datetime import date

from universe_scanner.__main__ import JST, _parse_date


def test_parse_date_explicit_value() -> None:
    assert _parse_date("2026-05-19") == date(2026, 5, 19)


def test_default_target_date_uses_jst_today(monkeypatch) -> None:
    class _FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz == JST

            class _Now:
                @staticmethod
                def date() -> date:
                    return date(2026, 5, 19)

            return _Now()

    monkeypatch.setattr("universe_scanner.__main__.datetime", _FixedDateTime)

    assert _parse_date(None) == date(2026, 5, 19)
