from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import date

import httpx
import pytest
from feature_engine.clients.supabase import SupabaseError, SupabaseReader, _parse_total

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _handler(rows_by_path: dict[str, list[dict[str, object]]]) -> Handler:
    async def _impl(request: httpx.Request) -> httpx.Response:
        rows = rows_by_path.get(request.url.path, [])
        range_header = request.headers.get("range", "0-999")
        start_s, end_s = range_header.split("-")
        start, end = int(start_s), int(end_s)
        page = rows[start : end + 1]
        total = len(rows)
        headers = {"Content-Range": f"{start}-{start + len(page) - 1}/{total}"}
        return httpx.Response(200, content=json.dumps(page), headers=headers)

    return _impl


async def test_fetch_watchlist_returns_symbols() -> None:
    transport = httpx.MockTransport(
        _handler(
            {
                "/rest/v1/watchlist": [
                    {"symbol": "7203"},
                    {"symbol": "9984"},
                ]
            }
        )
    )
    async with SupabaseReader(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as reader:
        result = await reader.fetch_watchlist(date(2026, 1, 20))
    assert result == ["7203", "9984"]


async def test_fetch_daily_ohlcv_returns_dataframe() -> None:
    transport = httpx.MockTransport(
        _handler(
            {
                "/rest/v1/daily_ohlcv": [
                    {
                        "symbol": "7203",
                        "date": "2026-01-19",
                        "open": 2500.0,
                        "high": 2520.0,
                        "low": 2480.0,
                        "close": 2510.0,
                        "volume": 1_000_000,
                        "turnover": 2_500_000_000.0,
                    },
                    {
                        "symbol": "7203",
                        "date": "2026-01-20",
                        "open": 2510.0,
                        "high": 2540.0,
                        "low": 2500.0,
                        "close": 2530.0,
                        "volume": 1_200_000,
                        "turnover": 3_000_000_000.0,
                    },
                ]
            }
        )
    )
    async with SupabaseReader(
        url="https://example.supabase.co", secret_key="k", transport=transport
    ) as reader:
        df = await reader.fetch_daily_ohlcv(
            ["7203"], start=date(2026, 1, 19), end=date(2026, 1, 20)
        )
    assert df.height == 2
    assert df.get_column("close").to_list() == [2510.0, 2530.0]
    assert str(df.schema["date"]) == "Date"


async def test_fetch_daily_ohlcv_empty_symbols_skips_request() -> None:
    called = 0

    async def _fail(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(500)

    async with SupabaseReader(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_fail),
    ) as reader:
        df = await reader.fetch_daily_ohlcv([], start=date(2026, 1, 1), end=date(2026, 1, 2))
    assert df.is_empty()
    assert called == 0


async def test_fetch_raises_on_4xx() -> None:
    async def _forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    async with SupabaseReader(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(_forbidden),
    ) as reader:
        with pytest.raises(SupabaseError):
            await reader.fetch_watchlist(date(2026, 1, 20))


def test_parse_total_handles_variants() -> None:
    assert _parse_total("0-9/42") == 42
    assert _parse_total("0-9/*") is None
    assert _parse_total(None) is None
    assert _parse_total("garbage") is None
    assert _parse_total("0-9/notanumber") is None
