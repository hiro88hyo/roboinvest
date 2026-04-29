"""scheduler (oms-live) の単体テスト。oms-paper の同名テストと同等。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from oms_live.scheduler import (
    JST,
    parse_hhmm,
    run_closeout_scheduler,
    seconds_until_next_fire,
)
from oms_live.streaming.runner import CloseoutStats, StreamRunner


def test_parse_hhmm_valid() -> None:
    assert parse_hhmm("14:50") == (14, 50)
    assert parse_hhmm("00:00") == (0, 0)
    assert parse_hhmm(" 09:01 ") == (9, 1)


def test_parse_hhmm_invalid_format() -> None:
    with pytest.raises(ValueError):
        parse_hhmm("14")
    with pytest.raises(ValueError):
        parse_hhmm("14:60")
    with pytest.raises(ValueError):
        parse_hhmm("24:00")


def test_seconds_until_next_fire_today_future() -> None:
    now = datetime(2026, 4, 29, 14, 0, tzinfo=JST)
    assert seconds_until_next_fire(now, hour=14, minute=50) == pytest.approx(3000.0)


def test_seconds_until_next_fire_already_passed_rolls_to_tomorrow() -> None:
    now = datetime(2026, 4, 29, 15, 0, tzinfo=JST)
    assert seconds_until_next_fire(now, hour=14, minute=50) == pytest.approx(85800.0)


def test_seconds_until_next_fire_at_target_returns_zero() -> None:
    now = datetime(2026, 4, 29, 14, 50, tzinfo=JST)
    assert seconds_until_next_fire(now, hour=14, minute=50) == pytest.approx(0.0)


def test_seconds_until_next_fire_requires_tz_aware() -> None:
    with pytest.raises(ValueError):
        seconds_until_next_fire(datetime(2026, 4, 29, 14, 0), hour=14, minute=50)


def test_seconds_until_next_fire_normalizes_other_tz_to_jst() -> None:
    utc = ZoneInfo("UTC")
    now = datetime(2026, 4, 29, 6, 0, tzinfo=utc)
    assert seconds_until_next_fire(now, hour=14, minute=50) == pytest.approx(85800.0)


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run_closeout(self) -> CloseoutStats:
        self.calls += 1
        return CloseoutStats(
            triggered=True,
            skipped_reason=None,
            positions_seen=self.calls,
            closed=self.calls,
            no_fills=0,
            write_errors=0,
        )


async def test_run_closeout_scheduler_finite_iterations_invokes_callback() -> None:
    fake = _FakeRunner()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    def _clock() -> datetime:
        return datetime(2026, 4, 29, 14, 49, tzinfo=JST)

    results = await run_closeout_scheduler(
        cast(StreamRunner, fake),
        clock=_clock,
        sleep=_sleep,
        iterations=2,
    )
    assert fake.calls == 2
    assert len(results) == 2
    assert sleeps[0] == pytest.approx(60.0)
    assert sleeps[1] >= 86000.0


async def test_run_closeout_scheduler_can_run_zero_iterations() -> None:
    fake = _FakeRunner()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    def _clock() -> datetime:
        return datetime(2026, 4, 29, 14, 49, tzinfo=JST)

    await run_closeout_scheduler(
        cast(StreamRunner, fake),
        clock=_clock,
        sleep=_sleep,
        iterations=0,
    )
    assert fake.calls == 0
    assert sleeps == []


async def test_run_closeout_scheduler_first_fire_immediate_when_target_in_past() -> None:
    fake = _FakeRunner()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    def _clock() -> datetime:
        return datetime(2026, 4, 29, 15, 0, tzinfo=JST)

    await run_closeout_scheduler(
        cast(StreamRunner, fake),
        clock=_clock,
        sleep=_sleep,
        iterations=1,
    )
    assert fake.calls == 1
    expected = (
        datetime(2026, 4, 30, 14, 50, tzinfo=JST) - datetime(2026, 4, 29, 15, 0, tzinfo=JST)
    ).total_seconds()
    assert sleeps[0] == pytest.approx(expected)


def test_seconds_until_next_fire_jst_has_no_dst() -> None:
    base = datetime(2026, 3, 8, 14, 0, tzinfo=JST)
    later = base + timedelta(days=1)
    assert seconds_until_next_fire(base, hour=14, minute=0) == pytest.approx(0.0)
    assert seconds_until_next_fire(later, hour=14, minute=0) == pytest.approx(0.0)
