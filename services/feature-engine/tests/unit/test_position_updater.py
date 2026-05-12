from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from feature_engine.clients.supabase import PositionSnapshot, SupabaseReader, SupabaseWriter
from feature_engine.streaming.position_updater import apply_tick, update_positions_for_tick
from trade_contracts.enums import TradeType
from trade_contracts.market import TickData

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _tick(symbol: str = "7203", price: Decimal = Decimal("2600")) -> TickData:
    return TickData(
        symbol=symbol,
        timestamp=datetime(2026, 1, 20, 13, 0, tzinfo=UTC),
        price=price,
        volume=100,
    )


def test_apply_tick_returns_empty_when_no_positions() -> None:
    assert apply_tick(_tick(), []) == []


def test_apply_tick_filters_other_symbols() -> None:
    positions = [
        PositionSnapshot(
            symbol="9984",
            trade_type=TradeType.LIVE,
            quantity=100,
            entry_price=Decimal("8000"),
        )
    ]
    assert apply_tick(_tick("7203"), positions) == []


def test_apply_tick_skips_zero_quantity() -> None:
    positions = [
        PositionSnapshot(
            symbol="7203", trade_type=TradeType.LIVE, quantity=0, entry_price=Decimal("2500")
        )
    ]
    assert apply_tick(_tick("7203"), positions) == []


def test_apply_tick_computes_pnl_for_both_trade_types() -> None:
    positions = [
        PositionSnapshot(
            symbol="7203",
            trade_type=TradeType.LIVE,
            quantity=100,
            entry_price=Decimal("2500"),
        ),
        PositionSnapshot(
            symbol="7203",
            trade_type=TradeType.PAPER,
            quantity=50,
            entry_price=Decimal("2400"),
        ),
    ]
    updates = apply_tick(_tick("7203", Decimal("2600")), positions)
    assert len(updates) == 2
    by_type = {u.trade_type: u for u in updates}
    assert by_type["live"].current_price == Decimal("2600")
    assert by_type["live"].unrealized_pnl == Decimal("10000")  # (2600-2500)*100
    assert by_type["paper"].unrealized_pnl == Decimal("10000")  # (2600-2400)*50


def test_apply_tick_handles_loss() -> None:
    positions = [
        PositionSnapshot(
            symbol="7203",
            trade_type=TradeType.LIVE,
            quantity=100,
            entry_price=Decimal("2800"),
        )
    ]
    updates = apply_tick(_tick("7203", Decimal("2600")), positions)
    assert updates[0].unrealized_pnl == Decimal("-20000")


def _split_handler(captured: list[httpx.Request], positions: list[dict[str, object]]) -> Handler:
    async def _impl(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET" and request.url.path == "/rest/v1/positions":
            headers = {"Content-Range": f"0-{max(len(positions) - 1, 0)}/{len(positions)}"}
            return httpx.Response(200, content=json.dumps(positions), headers=headers)
        if request.method == "PATCH" and request.url.path == "/rest/v1/positions":
            return httpx.Response(204)
        return httpx.Response(500)

    return _impl


async def test_update_positions_for_tick_patches_rows() -> None:
    captured: list[httpx.Request] = []
    rows = [
        {
            "symbol": "7203",
            "trade_type": "live",
            "quantity": 100,
            "entry_price": "2500",
        },
        {
            "symbol": "7203",
            "trade_type": "paper",
            "quantity": 50,
            "entry_price": "2400",
        },
    ]
    transport = httpx.MockTransport(_split_handler(captured, rows))
    async with (
        SupabaseReader(
            url="https://example.supabase.co", secret_key="k", transport=transport
        ) as reader,
        SupabaseWriter(
            url="https://example.supabase.co", secret_key="k", transport=transport
        ) as writer,
    ):
        n = await update_positions_for_tick(
            _tick("7203", Decimal("2600")), reader=reader, writer=writer
        )
    assert n == 2
    patches = [r for r in captured if r.method == "PATCH"]
    assert len(patches) == 2
    trade_types = {r.url.params["trade_type"].removeprefix("eq.") for r in patches}
    assert trade_types == {"live", "paper"}
    for req in patches:
        body = json.loads(req.content.decode())
        assert body["current_price"] == "2600"
        assert "symbol" not in body
        assert "quantity" not in body


async def test_update_positions_for_tick_skips_when_no_positions() -> None:
    captured: list[httpx.Request] = []
    transport = httpx.MockTransport(_split_handler(captured, []))
    async with (
        SupabaseReader(
            url="https://example.supabase.co", secret_key="k", transport=transport
        ) as reader,
        SupabaseWriter(
            url="https://example.supabase.co", secret_key="k", transport=transport
        ) as writer,
    ):
        n = await update_positions_for_tick(_tick("7203"), reader=reader, writer=writer)
    assert n == 0
    assert all(r.method == "GET" for r in captured)
