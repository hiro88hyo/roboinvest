"""SupabaseClient (oms-live) の単体テスト。"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from oms_live._testing import make_live_fill_record, make_live_position
from oms_live.clients.supabase import SupabaseClient, SupabaseError
from trade_contracts.enums import TradingStyle
from trade_contracts.risk import KillSwitchState

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _build_client(handler: Handler) -> SupabaseClient:
    return SupabaseClient(
        url="https://example.supabase.co",
        secret_key="k",
        transport=httpx.MockTransport(handler),
    )


def _system_status_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 1,
        "is_trading_allowed": True,
        "trade_mode": "live",
        "trading_style": "day",
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
        "daily_loss_limit": "10000",
        "weekly_loss_limit": "30000",
        "monthly_loss_limit": "100000",
        "updated_at": "2026-04-29T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _position_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "7203",
        "side": "LONG",
        "quantity": 100,
        "entry_price": "1000",
        "holding_type": "day",
        "target_price": None,
        "stop_loss_price": None,
        "max_hold_days": None,
        "trailing_stop_pct": None,
        "opened_at": "2026-04-29T09:00:00+00:00",
    }
    row.update(overrides)
    return row


# --- system_status -----------------------------------------------------------


async def test_read_system_status_parses_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_system_status_row()])

    async with _build_client(_handler) as client:
        state = await client.read_system_status()

    assert isinstance(state, KillSwitchState)
    assert state.trading_style is TradingStyle.DAY
    assert captured[0].url.path == "/rest/v1/system_status"
    assert captured[0].url.params.get("id") == "eq.1"


async def test_read_system_status_raises_when_missing() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="not found"):
            await client.read_system_status()


# --- add_realized_pnl --------------------------------------------------------


async def test_add_realized_pnl_reads_then_patches_three_columns() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    _system_status_row(
                        daily_pnl="100",
                        weekly_pnl="500",
                        monthly_pnl="2000",
                    )
                ],
            )
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.add_realized_pnl(Decimal("250"))

    assert len(captured) == 2
    patch = captured[1]
    assert patch.method == "PATCH"
    assert patch.url.path == "/rest/v1/system_status"
    assert patch.url.params.get("id") == "eq.1"
    body = json.loads(patch.content.decode())
    assert body == {
        "daily_pnl": "350",
        "weekly_pnl": "750",
        "monthly_pnl": "2250",
    }


async def test_add_realized_pnl_handles_negative_amount() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    _system_status_row(
                        daily_pnl="100",
                        weekly_pnl="500",
                        monthly_pnl="2000",
                    )
                ],
            )
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.add_realized_pnl(Decimal("-300"))

    body = json.loads(captured[1].content.decode())
    assert body == {
        "daily_pnl": "-200",
        "weekly_pnl": "200",
        "monthly_pnl": "1700",
    }


async def test_add_realized_pnl_skips_when_zero() -> None:
    called = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200, json=[_system_status_row()])

    async with _build_client(_handler) as client:
        await client.add_realized_pnl(Decimal("0"))
    assert called == 0


async def test_set_trading_allowed_patches_singleton() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.set_trading_allowed(False)

    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.path == "/rest/v1/system_status"
    assert req.url.params.get("id") == "eq.1"
    assert json.loads(req.content.decode()) == {"is_trading_allowed": False}


# --- read_live_position ------------------------------------------------------


async def test_read_live_position_returns_none_when_no_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        pos = await client.read_live_position(symbol="7203")

    assert pos is None
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("trade_type") == "eq.live"


async def test_read_live_position_parses_full_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(quantity=300, entry_price="1234.5")])

    async with _build_client(_handler) as client:
        pos = await client.read_live_position(symbol="7203")

    assert pos is not None
    assert pos.quantity == 300
    assert pos.entry_price == Decimal("1234.5")
    assert pos.holding_type is TradingStyle.DAY


async def test_read_live_position_rejects_non_long_side() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(side="SHORT")])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="non-LONG"):
            await client.read_live_position(symbol="7203")


# --- list_live_positions -----------------------------------------------------


async def test_list_live_positions_returns_all() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                _position_row(symbol="7203", quantity=100),
                _position_row(symbol="9984", quantity=200, entry_price="8000"),
            ],
        )

    async with _build_client(_handler) as client:
        rows = await client.list_live_positions()

    assert [p.symbol for p in rows] == ["7203", "9984"]
    assert captured[0].url.params.get("trade_type") == "eq.live"
    assert "symbol" not in captured[0].url.params


# --- insert_live_position ----------------------------------------------------


async def test_insert_live_position_posts_full_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    pos = make_live_position(
        symbol="7203",
        quantity=100,
        entry_price=Decimal("1000"),
        target_price=Decimal("1200"),
        stop_loss_price=Decimal("950"),
        holding_type=TradingStyle.SWING,
        max_hold_days=5,
        trailing_stop_pct=Decimal("0.03"),
    )

    async with _build_client(_handler) as client:
        await client.insert_live_position(pos)

    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/positions"
    body = json.loads(req.content.decode())
    row = body[0]
    assert row["symbol"] == "7203"
    assert row["trade_type"] == "live"
    assert row["side"] == "LONG"
    assert row["entry_price"] == "1000"
    assert row["current_price"] == "1000"
    assert row["unrealized_pnl"] == "0"
    assert row["holding_type"] == "swing"
    assert row["max_hold_days"] == 5
    assert row["trailing_stop_pct"] == "0.03"


async def test_insert_live_position_omits_optional_fields() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.insert_live_position(make_live_position())

    row = json.loads(captured[0].content.decode())[0]
    for key in ("target_price", "stop_loss_price", "max_hold_days", "trailing_stop_pct"):
        assert key not in row


# --- update / delete --------------------------------------------------------


async def test_update_live_position_quantity_patches_only_quantity_and_entry() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.update_live_position_quantity(symbol="7203", quantity=300, entry_price="1050")

    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.params.get("trade_type") == "eq.live"
    body = json.loads(req.content.decode())
    assert body == {"quantity": 300, "entry_price": "1050"}


async def test_delete_live_position_uses_filter() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.delete_live_position(symbol="7203")

    req = captured[0]
    assert req.method == "DELETE"
    assert req.url.params.get("trade_type") == "eq.live"


# --- insert_trade_live -------------------------------------------------------


async def test_insert_trade_live_posts_serialized_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    rec = make_live_fill_record(quantity=100, price=Decimal("1234.5"))
    async with _build_client(_handler) as client:
        await client.insert_trade_live(rec)

    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/trades_live"
    body = json.loads(req.content.decode())
    row = body[0]
    assert row["trade_id"] == str(rec.trade_id)
    assert row["symbol"] == "7203"
    assert row["side"] == "BUY"
    assert row["quantity"] == 100
    assert row["price"] == "1234.5"
    assert row["unified_signal_id"] == str(rec.unified_signal_id)
    assert row["order_id"] is None


async def test_insert_trade_live_serializes_order_id_when_present() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    order_id = uuid4()
    rec = make_live_fill_record(order_id=order_id)
    async with _build_client(_handler) as client:
        await client.insert_trade_live(rec)

    body = json.loads(captured[0].content.decode())
    assert body[0]["order_id"] == str(order_id)


async def test_insert_trade_live_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="insert failed"):
            await client.insert_trade_live(make_live_fill_record())


# --- live_trade_exists_for_order_id -----------------------------------------


async def test_live_trade_exists_returns_true_when_row_present() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"order_id": "11111111-1111-1111-1111-111111111111"}])

    order_id = UUID("11111111-1111-1111-1111-111111111111")
    async with _build_client(_handler) as client:
        exists = await client.live_trade_exists_for_order_id(order_id)

    assert exists is True
    req = captured[0]
    assert req.method == "GET"
    assert req.url.path == "/rest/v1/trades_live"
    assert req.url.params.get("order_id") == f"eq.{order_id}"
    assert req.url.params.get("select") == "order_id"
    assert req.url.params.get("limit") == "1"


async def test_live_trade_exists_returns_false_when_no_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        exists = await client.live_trade_exists_for_order_id(uuid4())

    assert exists is False


async def test_live_trade_exists_raises_on_5xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="transient"):
            await client.live_trade_exists_for_order_id(uuid4())


async def test_delete_live_position_raises_on_5xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="transient"):
            await client.delete_live_position(symbol="7203")
