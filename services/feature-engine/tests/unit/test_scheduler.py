from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from feature_engine.clients.supabase import SupabaseWriter
from feature_engine.scheduler import (
    JST,
    ResetDecision,
    ResetKind,
    apply_pnl_resets,
    compute_resets,
    run_pnl_reset_scheduler,
    seconds_until_next_fire,
)

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def test_compute_resets_skips_weekend() -> None:
    saturday = datetime(2026, 1, 17, 9, 0, tzinfo=JST)
    assert compute_resets(saturday).kinds == frozenset()


def test_compute_resets_requires_tzaware() -> None:
    with pytest.raises(ValueError):
        compute_resets(datetime(2026, 1, 5, 9, 0))


def test_compute_resets_regular_weekday_daily_only() -> None:
    # 2026-01-06 火曜 (週初ではない・月初ではない)
    d = datetime(2026, 1, 6, 9, 0, tzinfo=JST)
    assert compute_resets(d).kinds == frozenset({ResetKind.DAILY})


def test_compute_resets_monday_adds_weekly() -> None:
    # 2026-01-19 月曜 (非祝日・月初でない)
    d = datetime(2026, 1, 19, 9, 0, tzinfo=JST)
    assert compute_resets(d).kinds == frozenset({ResetKind.DAILY, ResetKind.WEEKLY})


def test_compute_resets_first_business_day_of_month() -> None:
    # 2026-01-05 月曜は月初営業日 かつ 週初
    d = datetime(2026, 1, 5, 9, 0, tzinfo=JST)
    assert compute_resets(d).kinds == frozenset(
        {ResetKind.DAILY, ResetKind.WEEKLY, ResetKind.MONTHLY}
    )


def test_compute_resets_accepts_non_jst_timezone() -> None:
    # UTC 0:00 2026-01-06 は JST 2026-01-06 09:00 火曜
    utc = ZoneInfo("UTC")
    d = datetime(2026, 1, 6, 0, 0, tzinfo=utc).astimezone(JST)
    assert compute_resets(d).kinds == frozenset({ResetKind.DAILY})


def _capture_handler(captured: list[httpx.Request]) -> Handler:
    async def _impl(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, headers={"Content-Type": "application/json"})

    return _impl


async def test_apply_pnl_resets_no_kinds_is_noop() -> None:
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await apply_pnl_resets(writer, ResetDecision(kinds=frozenset()))
    assert captured == []


async def test_apply_pnl_resets_daily_only_sets_single_column() -> None:
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await apply_pnl_resets(writer, ResetDecision(kinds=frozenset({ResetKind.DAILY})))
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.path == "/rest/v1/system_status"
    assert req.url.params["id"] == "eq.1"
    body = json.loads(req.content.decode())
    assert body["daily_pnl"] == 0
    assert "weekly_pnl" not in body
    assert "monthly_pnl" not in body
    assert "updated_at" in body


async def test_apply_pnl_resets_all_kinds_sets_all_columns() -> None:
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await apply_pnl_resets(
            writer,
            ResetDecision(kinds=frozenset({ResetKind.DAILY, ResetKind.WEEKLY, ResetKind.MONTHLY})),
        )
    body = json.loads(captured[0].content.decode())
    assert body["daily_pnl"] == 0
    assert body["weekly_pnl"] == 0
    assert body["monthly_pnl"] == 0
    assert "is_trading_allowed" not in body


def test_seconds_until_next_fire_requires_tzaware() -> None:
    with pytest.raises(ValueError):
        seconds_until_next_fire(datetime(2026, 4, 20, 5, 0))


def test_seconds_until_next_fire_before_target_same_day() -> None:
    now = datetime(2026, 4, 20, 5, 0, tzinfo=JST)
    # 05:00 → 09:00 は 4 時間
    assert seconds_until_next_fire(now) == 4 * 3600


def test_seconds_until_next_fire_after_target_rolls_to_next_day() -> None:
    now = datetime(2026, 4, 20, 10, 0, tzinfo=JST)
    # 10:00 → 翌 09:00 は 23 時間
    assert seconds_until_next_fire(now) == 23 * 3600


def test_seconds_until_next_fire_exactly_at_target_returns_zero() -> None:
    now = datetime(2026, 4, 20, 9, 0, tzinfo=JST)
    assert seconds_until_next_fire(now) == 0


def test_seconds_until_next_fire_accepts_utc_input() -> None:
    # UTC 2026-04-19 23:00 == JST 2026-04-20 08:00 → 1h until 09:00 JST
    now = datetime(2026, 4, 19, 23, 0, tzinfo=ZoneInfo("UTC"))
    assert seconds_until_next_fire(now) == 3600


class _FakeClock:
    """テスト用のクロック。`sleep(s)` で時刻が進む。"""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.sleep_calls: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


async def test_scheduler_fires_once_at_next_9am() -> None:
    # 2026-04-20 月曜 05:00 JST → 09:00 までの 4h スリープ後に fire
    clock = _FakeClock(datetime(2026, 4, 20, 5, 0, tzinfo=JST))
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await run_pnl_reset_scheduler(writer, clock=clock, sleep=clock.sleep, iterations=1)
    assert clock.sleep_calls == [4 * 3600]
    # 月曜 09:00 → daily + weekly が発火
    assert len(captured) == 1
    body = json.loads(captured[0].content.decode())
    assert body["daily_pnl"] == 0
    assert body["weekly_pnl"] == 0
    assert "monthly_pnl" not in body


async def test_scheduler_fires_daily_across_iterations() -> None:
    # 月曜 10:00 スタート → 23h スリープで火曜 09:00 fire → 24h スリープで水曜 09:00 fire
    clock = _FakeClock(datetime(2026, 4, 20, 10, 0, tzinfo=JST))
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await run_pnl_reset_scheduler(writer, clock=clock, sleep=clock.sleep, iterations=2)
    assert clock.sleep_calls == [23 * 3600, 24 * 3600]
    # どちらも平日・非週初・非月初 → daily のみ
    assert len(captured) == 2
    for req in captured:
        body = json.loads(req.content.decode())
        assert body["daily_pnl"] == 0
        assert "weekly_pnl" not in body


async def test_scheduler_skips_reset_on_non_business_day() -> None:
    # 土曜 08:00 → 日曜 09:00 fire (非営業日 → no-op)
    clock = _FakeClock(datetime(2026, 4, 18, 8, 0, tzinfo=JST))
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_capture_handler(captured))
    async with SupabaseWriter(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as writer:
        await run_pnl_reset_scheduler(writer, clock=clock, sleep=clock.sleep, iterations=1)
    # 1 回 fire したが decision は空 → Supabase は叩かれない
    assert captured == []
