from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from oms_paper._testing import make_paper_position
from oms_paper.closeout import build_closeout_orders
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle


def test_empty_positions_returns_empty_list() -> None:
    orders = build_closeout_orders(
        positions=[],
        closeout_signal_id=uuid4(),
        created_at=datetime(2026, 4, 25, 14, 50, tzinfo=UTC),
    )
    assert orders == []


def test_each_position_becomes_market_sell() -> None:
    positions = [
        make_paper_position(symbol="7203", quantity=100, entry_price=Decimal("1000")),
        make_paper_position(symbol="9984", quantity=200, entry_price=Decimal("8000")),
    ]
    sentinel = uuid4()
    ts = datetime(2026, 4, 25, 14, 50, tzinfo=UTC)
    orders = build_closeout_orders(
        positions=positions,
        closeout_signal_id=sentinel,
        created_at=ts,
    )
    assert len(orders) == 2
    for order, pos in zip(orders, positions, strict=True):
        assert order.symbol == pos.symbol
        assert order.quantity == pos.quantity
        assert order.side is Side.SELL
        assert order.order_type is OrderType.MARKET
        assert order.trade_mode is TradeMode.PAPER
        assert order.signal_source is SignalSource.CONSENSUS
        assert order.unified_signal_id == sentinel
        assert order.created_at == ts


def test_preserves_input_order() -> None:
    symbols = ["A", "B", "C", "D"]
    positions = [make_paper_position(symbol=s, quantity=100) for s in symbols]
    orders = build_closeout_orders(
        positions=positions,
        closeout_signal_id=uuid4(),
        created_at=datetime(2026, 4, 25, 14, 50, tzinfo=UTC),
    )
    assert [o.symbol for o in orders] == symbols


def test_swing_position_also_closed_out() -> None:
    pos = make_paper_position(quantity=100, holding_type=TradingStyle.SWING)
    orders = build_closeout_orders(
        positions=[pos],
        closeout_signal_id=uuid4(),
        created_at=datetime(2026, 4, 25, 14, 50, tzinfo=UTC),
    )
    assert len(orders) == 1
    assert orders[0].symbol == pos.symbol
