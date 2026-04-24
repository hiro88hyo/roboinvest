from __future__ import annotations

from datetime import UTC, datetime

import pytest
from gateway import order_builder
from trade_contracts.enums import Action, OrderType, Side, SignalSource, TradeMode


def test_build_buy_order(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, signal_source=SignalSource.CONSENSUS)
    stamp = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    order = order_builder.build(
        signal=signal,
        quantity=400,
        trade_mode=TradeMode.LIVE,
        created_at=stamp,
    )
    assert order.unified_signal_id == signal.signal_id
    assert order.symbol == signal.symbol
    assert order.side is Side.BUY
    assert order.quantity == 400
    assert order.order_type is OrderType.MARKET
    assert order.trade_mode is TradeMode.LIVE
    assert order.signal_source is SignalSource.CONSENSUS
    assert order.created_at == stamp


def test_build_sell_order(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.SELL, signal_source=SignalSource.RULE)
    order = order_builder.build(
        signal=signal,
        quantity=200,
        trade_mode=TradeMode.PAPER,
    )
    assert order.side is Side.SELL
    assert order.trade_mode is TradeMode.PAPER
    assert order.signal_source is SignalSource.RULE


def test_build_hold_raises(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.HOLD)
    with pytest.raises(ValueError):
        order_builder.build(
            signal=signal,
            quantity=100,
            trade_mode=TradeMode.LIVE,
        )


def test_build_zero_quantity_raises(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    with pytest.raises(ValueError):
        order_builder.build(
            signal=signal,
            quantity=0,
            trade_mode=TradeMode.LIVE,
        )


def test_build_defaults_created_at_to_now(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    before = datetime.now(UTC)
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.LIVE,
    )
    after = datetime.now(UTC)
    assert before <= order.created_at <= after


def test_fresh_order_id(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    a = order_builder.build(signal=signal, quantity=100, trade_mode=TradeMode.LIVE)
    b = order_builder.build(signal=signal, quantity=100, trade_mode=TradeMode.LIVE)
    assert a.order_id != b.order_id
