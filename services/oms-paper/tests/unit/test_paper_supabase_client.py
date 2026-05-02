from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from oms_paper._testing import make_paper_position
from oms_paper.clients.supabase import SupabaseClient, SupabaseError
from oms_paper.models import FillResult, PaperFillRecord
from oms_paper.position_updater import build_fill_record
from trade_contracts.enums import Side, TradingStyle
from trade_contracts.risk import KillSwitchState

from oms_paper._testing import make_order_request  # isort: skip

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
        "trade_mode": "paper",
        "trading_style": "day",
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
        "daily_loss_limit": "10000",
        "weekly_loss_limit": "30000",
        "monthly_loss_limit": "100000",
        "updated_at": "2026-04-25T00:00:00+00:00",
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
        "opened_at": "2026-04-25T09:00:00+00:00",
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


async def test_read_system_status_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "is_trading_allowed": True}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid"):
            await client.read_system_status()


# --- read_paper_position -----------------------------------------------------


async def test_read_paper_position_returns_none_when_no_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        pos = await client.read_paper_position(symbol="7203")

    assert pos is None
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("trade_type") == "eq.paper"


async def test_read_paper_position_parses_full_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(quantity=300, entry_price="1234.5")])

    async with _build_client(_handler) as client:
        pos = await client.read_paper_position(symbol="7203")

    assert pos is not None
    assert pos.quantity == 300
    assert pos.entry_price == Decimal("1234.5")
    assert pos.holding_type is TradingStyle.DAY


async def test_read_paper_position_rejects_non_long_side() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(side="SHORT")])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="non-LONG"):
            await client.read_paper_position(symbol="7203")


async def test_read_paper_position_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_position_row(quantity=-10)])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid paper position"):
            await client.read_paper_position(symbol="7203")


# --- list_paper_positions ----------------------------------------------------


async def test_list_paper_positions_returns_all() -> None:
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
        rows = await client.list_paper_positions()

    assert [p.symbol for p in rows] == ["7203", "9984"]
    assert captured[0].url.params.get("trade_type") == "eq.paper"
    assert "symbol" not in captured[0].url.params  # no symbol filter


async def test_list_paper_positions_returns_empty() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        rows = await client.list_paper_positions()
    assert rows == []


async def test_list_paper_positions_raises_on_non_list() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="unexpected positions list"):
            await client.list_paper_positions()


# --- insert_paper_position ---------------------------------------------------


async def test_insert_paper_position_posts_full_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    pos = make_paper_position(
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
        await client.insert_paper_position(pos)

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/positions"
    body = json.loads(req.content.decode())
    assert isinstance(body, list)
    row = body[0]
    assert row["symbol"] == "7203"
    assert row["trade_type"] == "paper"
    assert row["side"] == "LONG"
    assert row["quantity"] == 100
    assert row["entry_price"] == "1000"
    assert row["current_price"] == "1000"  # entry_price で初期化
    assert row["unrealized_pnl"] == "0"
    assert row["holding_type"] == "swing"
    assert row["target_price"] == "1200"
    assert row["stop_loss_price"] == "950"
    assert row["max_hold_days"] == 5
    assert row["trailing_stop_pct"] == "0.03"
    assert req.headers.get("Prefer") == "return=minimal"


async def test_insert_paper_position_omits_optional_fields() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    pos = make_paper_position()  # 全 optional は None
    async with _build_client(_handler) as client:
        await client.insert_paper_position(pos)
    row = json.loads(captured[0].content.decode())[0]
    for key in ("target_price", "stop_loss_price", "max_hold_days", "trailing_stop_pct"):
        assert key not in row


async def test_insert_paper_position_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="conflict")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="insert failed"):
            await client.insert_paper_position(make_paper_position())


# --- update_paper_position_quantity ------------------------------------------


async def test_update_paper_position_quantity_patches_only_quantity_and_entry() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.update_paper_position_quantity(symbol="7203", quantity=300, entry_price="1050")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.path == "/rest/v1/positions"
    assert req.url.params.get("symbol") == "eq.7203"
    assert req.url.params.get("trade_type") == "eq.paper"
    body = json.loads(req.content.decode())
    assert body == {"quantity": 300, "entry_price": "1050"}
    assert "current_price" not in body
    assert "unrealized_pnl" not in body


# --- update_paper_position_stop_loss -----------------------------------------


async def test_update_paper_position_stop_loss_patches_only_stop_loss() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.update_paper_position_stop_loss(symbol="7203", stop_loss_price="1078")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.path == "/rest/v1/positions"
    assert req.url.params.get("symbol") == "eq.7203"
    assert req.url.params.get("trade_type") == "eq.paper"
    body = json.loads(req.content.decode())
    assert body == {"stop_loss_price": "1078"}
    # quantity / entry_price / current_price は触らない
    assert "quantity" not in body
    assert "entry_price" not in body
    assert "current_price" not in body


async def test_update_paper_position_stop_loss_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="row not found")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="update_stop_loss failed"):
            await client.update_paper_position_stop_loss(symbol="missing", stop_loss_price="1000")


async def test_update_paper_position_stop_loss_retries_on_5xx() -> None:
    calls = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="boom")
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.update_paper_position_stop_loss(symbol="7203", stop_loss_price="1078")

    assert calls == 3  # 5xx 2 回 → 3 回目で成功


# --- delete_paper_position ---------------------------------------------------


async def test_delete_paper_position_uses_filter() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    async with _build_client(_handler) as client:
        await client.delete_paper_position(symbol="7203")

    req = captured[0]
    assert req.method == "DELETE"
    assert req.url.path == "/rest/v1/positions"
    assert req.url.params.get("symbol") == "eq.7203"
    assert req.url.params.get("trade_type") == "eq.paper"


async def test_delete_paper_position_raises_on_5xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="transient"):
            await client.delete_paper_position(symbol="7203")


# --- insert_trade_paper ------------------------------------------------------


def _build_fill_record() -> PaperFillRecord:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1000"), reason="filled")
    rec = build_fill_record(
        order=order,
        fill=fill,
        executed_at=datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
    )
    assert rec is not None
    return rec


async def test_insert_trade_paper_posts_serialized_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    rec = _build_fill_record()
    async with _build_client(_handler) as client:
        await client.insert_trade_paper(rec)

    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/trades_paper"
    body = json.loads(req.content.decode())
    row = body[0]
    assert row["trade_id"] == str(rec.trade_id)
    assert row["symbol"] == "7203"
    assert row["side"] == "BUY"
    assert row["quantity"] == 100
    assert row["price"] == "1000"
    assert row["signal_source"] == rec.signal_source.value
    assert row["unified_signal_id"] == str(rec.unified_signal_id)
    assert row["executed_at"] == rec.executed_at.isoformat()
    assert req.headers.get("Prefer") == "return=minimal"


async def test_insert_trade_paper_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="insert failed"):
            await client.insert_trade_paper(_build_fill_record())
