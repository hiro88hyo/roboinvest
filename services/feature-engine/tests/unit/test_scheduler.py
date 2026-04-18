from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import datetime
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
