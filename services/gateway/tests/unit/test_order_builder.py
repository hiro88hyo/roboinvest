from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from gateway import order_builder
from trade_contracts.enums import (
    Action,
    OrderType,
    RoutingIntent,
    Side,
    SignalSource,
    TradeMode,
    TradingStyle,
)


def test_build_buy_order(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(
        action=Action.BUY,
        signal_source=SignalSource.CONSENSUS,
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("2450"),
        target_price=Decimal("2600"),
        trailing_stop_pct=Decimal("0.02"),
        max_hold_days=5,
        scheduled_exit_date=date(2026, 5, 1),
    )
    stamp = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    order = order_builder.build(
        signal=signal,
        quantity=400,
        trade_mode=TradeMode.LIVE,
        entry_price=Decimal("2500"),
        created_at=stamp,
    )
    assert order.unified_signal_id == signal.signal_id
    assert order.symbol == signal.symbol
    assert order.side is Side.BUY
    assert order.quantity == 400
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == Decimal("2500")
    assert order.trade_mode is TradeMode.LIVE
    assert order.signal_source is SignalSource.CONSENSUS
    assert order.holding_type is TradingStyle.SWING
    assert order.stop_loss_price == Decimal("2450")
    assert order.stop_loss_pct is None
    assert order.target_price == Decimal("2600")
    assert order.trailing_stop_pct == Decimal("0.02")
    assert order.max_hold_days == 5
    assert order.scheduled_exit_date == date(2026, 5, 1)
    assert order.created_at == stamp


def test_build_buy_order_fills_default_stop_loss(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=None)
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.LIVE,
        entry_price=Decimal("1719.6"),
        default_stop_loss_spread_pct=Decimal("0.02"),
    )
    assert order.stop_loss_price == Decimal("1685.208")


def test_build_buy_order_carries_relative_stop_without_synthesizing_absolute(  # type: ignore[no-untyped-def]
    unified_signal_factory,
) -> None:
    signal = unified_signal_factory(
        action=Action.BUY,
        holding_type=TradingStyle.SWING,
        stop_loss_pct=Decimal("0.10"),
        max_hold_days=20,
    )
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.PAPER,
        entry_price=Decimal("1719.6"),
        default_stop_loss_spread_pct=Decimal("0.02"),
    )
    assert order.holding_type is TradingStyle.SWING
    assert order.stop_loss_price is None
    assert order.stop_loss_pct == Decimal("0.10")
    assert order.max_hold_days == 20


def test_build_preserves_event_identity_and_paper_only_intent(  # type: ignore[no-untyped-def]
    unified_signal_factory,
) -> None:
    signal = unified_signal_factory(
        action=Action.BUY,
        routing_intent=RoutingIntent.PAPER_ONLY,
        strategy_key="event-cluster-v1",
        candidate_id="cluster-1",
    )

    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.PAPER,
        entry_price=Decimal("1000"),
    )

    assert order.routing_intent is RoutingIntent.PAPER_ONLY
    assert order.strategy_key == "event-cluster-v1"
    assert order.candidate_id == "cluster-1"


def test_build_buy_order_applies_limit_offset_ticks(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.PAPER,
        entry_price=Decimal("2500"),
        buy_limit_offset_ticks=3,
    )
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == Decimal("2503")


def test_build_buy_order_limit_offset_uses_price_band_tick(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.PAPER,
        entry_price=Decimal("4000"),
        buy_limit_offset_ticks=3,
    )
    assert order.limit_price == Decimal("4015")


def test_build_buy_order_keeps_explicit_stop_loss(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("1650"))
    order = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.LIVE,
        entry_price=Decimal("1719.6"),
        default_stop_loss_spread_pct=Decimal("0.02"),
    )
    assert order.stop_loss_price == Decimal("1650")


def test_build_sell_order(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.SELL, signal_source=SignalSource.RULE)
    order = order_builder.build(
        signal=signal,
        quantity=200,
        trade_mode=TradeMode.PAPER,
        entry_price=Decimal("1719.6"),
        default_stop_loss_spread_pct=Decimal("0.02"),
    )
    assert order.side is Side.SELL
    assert order.order_type is OrderType.MARKET
    assert order.limit_price is None
    assert order.trade_mode is TradeMode.PAPER
    assert order.signal_source is SignalSource.RULE
    assert order.stop_loss_price is None


def test_build_hold_raises(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.HOLD)
    with pytest.raises(ValueError):
        order_builder.build(
            signal=signal,
            quantity=100,
            trade_mode=TradeMode.LIVE,
        )


def test_build_buy_without_entry_price_raises(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    with pytest.raises(ValueError, match="entry_price"):
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
        entry_price=Decimal("1000"),
    )
    after = datetime.now(UTC)
    assert before <= order.created_at <= after


def test_order_id_is_deterministic_for_redelivery(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    a = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.LIVE,
        entry_price=Decimal("1000"),
    )
    b = order_builder.build(
        signal=signal,
        quantity=100,
        trade_mode=TradeMode.LIVE,
        entry_price=Decimal("1000"),
    )
    assert a.order_id == b.order_id
