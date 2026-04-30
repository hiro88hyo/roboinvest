"""closeout (oms-live) の単体テスト。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from oms_live._testing import make_live_position
from oms_live.closeout import build_closeout_orders
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode


def test_build_closeout_orders_empty_returns_empty_list() -> None:
    assert (
        build_closeout_orders(
            positions=[],
            created_at=datetime.now(UTC),
        )
        == []
    )


def test_build_closeout_orders_generates_market_sell_per_position() -> None:
    positions = [
        make_live_position(symbol="7203", quantity=100, entry_price=Decimal("1000")),
        make_live_position(symbol="9984", quantity=200, entry_price=Decimal("8000")),
    ]
    created_at = datetime(2026, 4, 29, 14, 50, tzinfo=UTC)
    orders = build_closeout_orders(positions=positions, created_at=created_at)
    assert len(orders) == 2
    assert [o.symbol for o in orders] == ["7203", "9984"]
    for order in orders:
        assert order.side is Side.SELL
        assert order.order_type is OrderType.MARKET
        assert order.trade_mode is TradeMode.LIVE
        assert order.signal_source is SignalSource.CONSENSUS
        assert order.unified_signal_id is None
        assert order.created_at == created_at
    assert orders[0].quantity == 100
    assert orders[1].quantity == 200


def test_build_closeout_orders_preserves_input_order() -> None:
    positions = [
        make_live_position(symbol="A", quantity=10),
        make_live_position(symbol="B", quantity=20),
        make_live_position(symbol="C", quantity=30),
    ]
    orders = build_closeout_orders(
        positions=positions,
        created_at=datetime.now(UTC),
    )
    assert [o.symbol for o in orders] == ["A", "B", "C"]
