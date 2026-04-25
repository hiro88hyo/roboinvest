"""``clients.supabase.SupabaseWatchlistReader`` の単体テスト。"""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
from feeder.clients.supabase import (
    PollingWatchlistFeed,
    SupabaseError,
    SupabaseWatchlistReader,
    WatchlistRow,
)


def _build_reader(handler: httpx.MockTransport) -> SupabaseWatchlistReader:
    return SupabaseWatchlistReader(
        url="http://localhost:54321",
        secret_key="dummy-key",
        transport=handler,
    )


async def test_fetch_watchlist_returns_rows_for_date() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"symbol": "7203", "valid_date": "2026-04-25"},
                {"symbol": "9984", "valid_date": "2026-04-25"},
            ],
        )

    reader = _build_reader(httpx.MockTransport(handler))
    async with reader:
        rows = await reader.fetch_watchlist(valid_date=date(2026, 4, 25))

    assert rows == [
        WatchlistRow(symbol="7203", valid_date=date(2026, 4, 25)),
        WatchlistRow(symbol="9984", valid_date=date(2026, 4, 25)),
    ]
    assert seen[0].url.params["valid_date"] == "eq.2026-04-25"
    assert seen[0].url.params["order"] == "symbol.asc"
    assert seen[0].headers.get("apikey") == "dummy-key"


async def test_fetch_watchlist_returns_empty_when_no_rows() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    reader = _build_reader(httpx.MockTransport(handler))
    async with reader:
        rows = await reader.fetch_watchlist(valid_date=date(2026, 4, 25))

    assert rows == []


async def test_fetch_watchlist_raises_for_4xx() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad request"})

    reader = _build_reader(httpx.MockTransport(handler))
    async with reader:
        with pytest.raises(SupabaseError) as exc:
            await reader.fetch_watchlist(valid_date=date(2026, 4, 25))
    assert "400" in str(exc.value)


async def test_fetch_watchlist_retries_on_5xx_then_succeeds() -> None:
    counter = {"hits": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        counter["hits"] += 1
        if counter["hits"] < 2:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=[{"symbol": "7203", "valid_date": "2026-04-25"}])

    reader = _build_reader(httpx.MockTransport(handler))
    async with reader:
        rows = await reader.fetch_watchlist(valid_date=date(2026, 4, 25))

    assert counter["hits"] == 2
    assert rows == [WatchlistRow(symbol="7203", valid_date=date(2026, 4, 25))]


async def test_fetch_watchlist_raises_on_non_list_payload() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": "not a list"})

    reader = _build_reader(httpx.MockTransport(handler))
    async with reader:
        with pytest.raises(SupabaseError):
            await reader.fetch_watchlist(valid_date=date(2026, 4, 25))


async def test_polling_watchlist_feed_yields_each_poll_and_sleeps() -> None:
    captured_dates: list[date] = []
    poll_count = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        poll_count["hits"] += 1
        captured_dates.append(date.fromisoformat(request.url.params["valid_date"][3:]))
        return httpx.Response(
            200, json=[{"symbol": f"720{poll_count['hits']}", "valid_date": "2026-04-25"}]
        )

    reader = _build_reader(httpx.MockTransport(handler))
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with reader:
        feed = PollingWatchlistFeed(
            reader=reader,
            poll_interval_seconds=5.0,
            date_provider=lambda: date(2026, 4, 25),
            sleep=fake_sleep,
        )
        agen = feed.subscribe()
        first = await agen.__anext__()
        second = await agen.__anext__()
        await agen.aclose()

    assert [r.symbol for r in first] == ["7201"]
    assert [r.symbol for r in second] == ["7202"]
    # 1 回目の yield 後に 1 回 sleep してから 2 回目を取りに行く
    assert sleeps == [5.0]
    assert captured_dates == [date(2026, 4, 25), date(2026, 4, 25)]


async def test_polling_watchlist_feed_uses_dynamic_date_each_call() -> None:
    poll_count = {"hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        poll_count["hits"] += 1
        return httpx.Response(200, json=[])

    reader = _build_reader(httpx.MockTransport(handler))

    dates_to_return = iter([date(2026, 4, 25), date(2026, 4, 26)])

    async def no_sleep(_: float) -> None:
        return None

    async with reader:
        feed = PollingWatchlistFeed(
            reader=reader,
            poll_interval_seconds=0.0,
            date_provider=lambda: next(dates_to_return),
            sleep=no_sleep,
        )
        agen = feed.subscribe()
        await agen.__anext__()
        await agen.__anext__()
        await agen.aclose()

    assert poll_count["hits"] == 2


async def test_polling_watchlist_feed_propagates_supabase_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "boom"})

    reader = _build_reader(httpx.MockTransport(handler))

    async with reader:
        feed = PollingWatchlistFeed(
            reader=reader,
            poll_interval_seconds=0.0,
            date_provider=lambda: date(2026, 4, 25),
            sleep=asyncio.sleep,
        )
        agen = feed.subscribe()
        with pytest.raises(SupabaseError):
            await agen.__anext__()
        await agen.aclose()
