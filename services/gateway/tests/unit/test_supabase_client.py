from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from gateway.clients.supabase import SupabaseClient, SupabaseError
from trade_contracts.enums import TradeMode

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
        "updated_at": "2026-04-23T00:00:00+00:00",
    }
    row.update(overrides)
    return row


async def test_read_system_status_parses_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_system_status_row()])

    async with _build_client(_handler) as client:
        state = await client.read_system_status()

    assert state.is_trading_allowed is True
    assert state.trade_mode is TradeMode.LIVE
    assert captured[0].url.path == "/rest/v1/system_status"
    assert captured[0].url.params.get("id") == "eq.1"
    assert captured[0].url.params.get("limit") == "1"


async def test_read_system_status_raises_when_missing() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="not found"):
            await client.read_system_status()


async def test_read_system_status_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError):
            await client.read_system_status()


async def test_read_system_status_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "is_trading_allowed": True}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid"):
            await client.read_system_status()


async def test_check_kill_switch_posts_rpc_and_parses_decision() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    **_system_status_row(is_trading_allowed=False, daily_pnl="-10000"),
                    "passed": False,
                    "reason": "daily_loss_limit",
                    "disabled": True,
                }
            ],
        )

    async with _build_client(_handler) as client:
        decision = await client.check_kill_switch()

    assert decision.passed is False
    assert decision.reason == "daily_loss_limit"
    assert decision.disabled is True
    assert decision.state.is_trading_allowed is False
    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/rest/v1/rpc/gateway_check_kill_switch"
    assert json.loads(captured[0].content.decode()) == {}


async def test_check_kill_switch_accepts_object_payload() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **_system_status_row(),
                "passed": True,
                "reason": None,
                "disabled": False,
            },
        )

    async with _build_client(_handler) as client:
        decision = await client.check_kill_switch()

    assert decision.passed is True
    assert decision.reason is None
    assert decision.disabled is False


async def test_check_kill_switch_raises_on_invalid_payload() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="returned no rows"):
            await client.check_kill_switch()


async def test_reserve_order_risk_posts_rpc_and_parses_decision() -> None:
    captured: list[httpx.Request] = []
    order_id = uuid4()

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "passed": True,
                    "reason": None,
                    "reserved": True,
                    "active_risk_before": "1000",
                    "active_risk_after": "3000",
                    "daily_pnl": "-100",
                    "daily_loss_limit": "10000",
                    "weekly_pnl": "0",
                    "weekly_loss_limit": "30000",
                    "monthly_pnl": "0",
                    "monthly_loss_limit": "100000",
                }
            ],
        )

    async with _build_client(_handler) as client:
        decision = await client.reserve_order_risk(
            order_id=order_id,
            trade_mode=TradeMode.LIVE,
            trading_date=datetime(2026, 4, 20).date(),
            symbol="7203",
            side="BUY",
            risk_amount=Decimal("2000"),
            notional_amount=Decimal("500000"),
        )

    assert decision.passed is True
    assert decision.reserved is True
    assert decision.reason is None
    assert decision.active_risk_before == Decimal("1000")
    assert decision.active_risk_after == Decimal("3000")
    req = captured[0]
    assert req.method == "POST"
    assert req.url.path == "/rest/v1/rpc/gateway_check_and_reserve_risk"
    assert json.loads(req.content.decode()) == {
        "p_order_id": str(order_id),
        "p_trade_mode": "live",
        "p_trading_date": "2026-04-20",
        "p_symbol": "7203",
        "p_side": "BUY",
        "p_risk_amount": "2000",
        "p_notional_amount": "500000",
    }


async def test_reserve_order_risk_raises_on_invalid_payload() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="returned no rows"):
            await client.reserve_order_risk(
                order_id=uuid4(),
                trade_mode=TradeMode.LIVE,
                trading_date=datetime(2026, 4, 20).date(),
                symbol="7203",
                side="BUY",
                risk_amount=Decimal("2000"),
                notional_amount=Decimal("500000"),
            )


async def test_release_risk_reservation_posts_rpc() -> None:
    captured: list[httpx.Request] = []
    order_id = uuid4()

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[{"released": True, "order_id": str(order_id), "status": "released"}],
        )

    async with _build_client(_handler) as client:
        released = await client.release_risk_reservation(
            order_id=order_id,
            reason="publish_failed",
        )

    assert released is True
    assert captured[0].url.path == "/rest/v1/rpc/gateway_release_risk_reservation"
    assert json.loads(captured[0].content.decode()) == {
        "p_order_id": str(order_id),
        "p_reason": "publish_failed",
    }


async def test_read_long_quantity_returns_zero_when_no_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        qty = await client.read_long_quantity(symbol="7203", trade_mode=TradeMode.LIVE)

    assert qty == 0


async def test_read_long_quantity_returns_quantity_and_filters() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"quantity": 300, "side": "LONG"}])

    async with _build_client(_handler) as client:
        qty = await client.read_long_quantity(symbol="7203", trade_mode=TradeMode.PAPER)

    assert qty == 300
    assert captured[0].url.path == "/rest/v1/positions"
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("trade_type") == "eq.paper"
    assert captured[0].url.params.get("limit") == "1"


async def test_read_long_quantity_rejects_non_long_side() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"quantity": 100, "side": "SHORT"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="non-LONG"):
            await client.read_long_quantity(symbol="7203", trade_mode=TradeMode.LIVE)


async def test_read_long_quantity_raises_on_invalid_payload() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"side": "LONG"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid position"):
            await client.read_long_quantity(symbol="7203", trade_mode=TradeMode.LIVE)


async def test_read_latest_price_returns_decimal() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"current_price": "2531.5"}])

    async with _build_client(_handler) as client:
        price = await client.read_latest_price(symbol="7203")

    assert price is not None
    assert str(price) == "2531.5"
    assert captured[0].url.path == "/rest/v1/positions"
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("order") == "opened_at.desc"
    assert captured[0].url.params.get("limit") == "1"


async def test_read_latest_price_returns_none_when_no_position() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        assert await client.read_latest_price(symbol="7203") is None


async def test_read_latest_price_returns_none_when_null_price() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"current_price": None}])

    async with _build_client(_handler) as client:
        assert await client.read_latest_price(symbol="7203") is None


async def test_read_latest_price_raises_on_invalid_value() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"current_price": "not-a-number"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid current_price"):
            await client.read_latest_price(symbol="7203")


async def test_read_latest_daily_close_returns_decimal() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"close": "2305.5"}])

    async with _build_client(_handler) as client:
        price = await client.read_latest_daily_close(symbol="7203")

    assert price is not None
    assert str(price) == "2305.5"
    assert captured[0].url.path == "/rest/v1/daily_ohlcv"
    assert captured[0].url.params.get("symbol") == "eq.7203"
    assert captured[0].url.params.get("order") == "date.desc"
    assert captured[0].url.params.get("limit") == "1"
    assert captured[0].url.params.get("select") == "close"


async def test_read_latest_daily_close_returns_none_when_empty() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        assert await client.read_latest_daily_close(symbol="7203") is None


async def test_read_latest_daily_close_returns_none_when_null_close() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"close": None}])

    async with _build_client(_handler) as client:
        assert await client.read_latest_daily_close(symbol="7203") is None


async def test_read_latest_daily_close_raises_on_invalid_value() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"close": "not-a-number"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid daily_ohlcv close"):
            await client.read_latest_daily_close(symbol="7203")


async def test_read_latest_daily_liquidity_returns_snapshot() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[{"close": "803", "volume": 12600, "turnover": "10117800"}])

    async with _build_client(_handler) as client:
        snapshot = await client.read_latest_daily_liquidity(symbol="4346")

    assert snapshot is not None
    assert snapshot.close == Decimal("803")
    assert snapshot.volume == 12600
    assert snapshot.turnover == Decimal("10117800")
    assert captured[0].url.path == "/rest/v1/daily_ohlcv"
    assert captured[0].url.params.get("select") == "close,volume,turnover"
    assert captured[0].url.params.get("symbol") == "eq.4346"


async def test_read_latest_daily_liquidity_returns_none_when_empty() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        assert await client.read_latest_daily_liquidity(symbol="4346") is None


async def test_read_latest_daily_liquidity_raises_on_invalid_row() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"close": "803", "volume": "bad", "turnover": "1"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid daily_ohlcv liquidity row"):
            await client.read_latest_daily_liquidity(symbol="4346")


async def test_read_live_capital_in_use_sums_current_and_entry_prices() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"quantity": 100, "current_price": "2500.5", "entry_price": "2400"},
                {"quantity": 200, "current_price": None, "entry_price": "1100"},
                {"quantity": 0, "current_price": "9999", "entry_price": "9999"},
            ],
        )

    async with _build_client(_handler) as client:
        total = await client.read_live_capital_in_use()

    assert str(total) == "470050.0"


async def test_read_live_capital_in_use_raises_on_invalid_quantity() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"quantity": "oops", "current_price": "2500"}])

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError, match="invalid position quantity"):
            await client.read_live_capital_in_use()


async def test_read_market_regime_returns_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "valid_date": "2026-06-11",
                    "regime": "RISK_OFF",
                    "confidence": "0.86",
                    "buy_enabled": False,
                    "position_size_multiplier": "0.25",
                    "source": "universe_scanner",
                    "rationale": ["weak breadth"],
                    "metrics": {"down_ratio": 0.82},
                    "created_at": "2026-06-11T00:00:00+00:00",
                }
            ],
        )

    async with _build_client(_handler) as client:
        row = await client.read_market_regime(valid_date=datetime(2026, 6, 11).date())

    assert row is not None
    assert row.regime == "RISK_OFF"
    assert str(row.confidence) == "0.86"
    assert row.buy_enabled is False
    assert str(row.position_size_multiplier) == "0.25"
    assert row.rationale == ["weak breadth"]
    assert row.metrics == {"down_ratio": 0.82}
    assert captured[0].url.path == "/rest/v1/market_regime"
    assert captured[0].url.params.get("valid_date") == "eq.2026-06-11"


async def test_read_market_regime_returns_none_when_missing() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _build_client(_handler) as client:
        row = await client.read_market_regime(valid_date=datetime(2026, 6, 11).date())

    assert row is None


async def test_read_market_regime_missing_table_is_noop() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "42P01",
                "message": 'relation "public.market_regime" does not exist',
            },
        )

    async with _build_client(_handler) as client:
        row = await client.read_market_regime(valid_date=datetime(2026, 6, 11).date())

    assert row is None


async def test_read_latest_daily_close_retries_on_5xx() -> None:
    calls: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=[{"close": "2310"}])

    async with _build_client(_handler) as client:
        price = await client.read_latest_daily_close(symbol="7203")

    assert price is not None
    assert str(price) == "2310"
    assert len(calls) == 2


async def test_disable_trading_patches_row() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, content=b"")

    frozen = datetime(2026, 4, 23, 15, 30, tzinfo=UTC)

    async with _build_client(_handler) as client:
        await client.disable_trading(now=frozen)

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "PATCH"
    assert req.url.path == "/rest/v1/system_status"
    assert req.url.params.get("id") == "eq.1"
    body = json.loads(req.content.decode())
    assert body == {
        "is_trading_allowed": False,
        "updated_at": "2026-04-23T15:30:00+00:00",
    }
    assert req.headers.get("Prefer") == "return=minimal"


async def test_disable_trading_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    async with _build_client(_handler) as client:
        with pytest.raises(SupabaseError):
            await client.disable_trading()
