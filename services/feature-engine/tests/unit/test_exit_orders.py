from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from feature_engine.clients.supabase import PositionSnapshot
from feature_engine.streaming.exit_orders import (
    ExitOrderMonitor,
    build_exit_order,
    topic_for_exit_order,
)
from trade_contracts.enums import Side, TradeMode, TradeType, TradingStyle
from trade_contracts.market import TickData


def _tick(symbol: str = "7203", price: Decimal = Decimal("1000")) -> TickData:
    return TickData(
        symbol=symbol,
        timestamp=datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
        price=price,
        volume=100,
    )


def _position(
    *,
    trade_type: TradeType = TradeType.LIVE,
    holding_type: TradingStyle = TradingStyle.DAY,
    current_price: Decimal = Decimal("1000"),
    entry_price: Decimal = Decimal("1000"),
    opened_at: datetime = datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
    target_price: Decimal | None = None,
    stop_loss_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
) -> PositionSnapshot:
    return PositionSnapshot(
        symbol="7203",
        trade_type=trade_type,
        quantity=100,
        entry_price=entry_price,
        current_price=current_price,
        opened_at=opened_at,
        holding_type=holding_type,
        target_price=target_price,
        stop_loss_price=stop_loss_price,
        trailing_stop_pct=trailing_stop_pct,
    )


def test_collect_triggers_stop_loss() -> None:
    monitor = ExitOrderMonitor()
    triggers = monitor.collect_triggers(
        tick=_tick(price=Decimal("970")),
        positions=[_position(stop_loss_price=Decimal("980"))],
    )
    assert len(triggers) == 1
    assert triggers[0].reason == "stop_loss"
    assert triggers[0].threshold == Decimal("980")


def test_collect_triggers_target_price() -> None:
    monitor = ExitOrderMonitor()
    triggers = monitor.collect_triggers(
        tick=_tick(price=Decimal("1050")),
        positions=[_position(target_price=Decimal("1040"))],
    )
    assert len(triggers) == 1
    assert triggers[0].reason == "target_price"
    assert triggers[0].threshold == Decimal("1040")


def test_collect_triggers_trailing_stop_after_peak() -> None:
    monitor = ExitOrderMonitor()
    pos = _position(
        entry_price=Decimal("1000"),
        current_price=Decimal("1000"),
        trailing_stop_pct=Decimal("0.05"),
    )
    assert monitor.collect_triggers(tick=_tick(price=Decimal("1100")), positions=[pos]) == []
    triggers = monitor.collect_triggers(tick=_tick(price=Decimal("1040")), positions=[pos])
    assert len(triggers) == 1
    assert triggers[0].reason == "trailing_stop"
    assert triggers[0].threshold == Decimal("1045.00")


def test_collect_triggers_max_hold_minutes() -> None:
    monitor = ExitOrderMonitor()
    triggers = monitor.collect_triggers(
        tick=_tick(price=Decimal("1005")),
        positions=[
            _position(
                opened_at=datetime(2026, 6, 1, 23, 14, 59, tzinfo=UTC),
                stop_loss_price=Decimal("980"),
            )
        ],
        max_hold_minutes=45,
    )
    assert len(triggers) == 1
    assert triggers[0].reason == "max_hold_minutes"
    assert triggers[0].threshold == Decimal("45")


def test_collect_triggers_max_hold_ignores_younger_position() -> None:
    monitor = ExitOrderMonitor()
    triggers = monitor.collect_triggers(
        tick=_tick(price=Decimal("1005")),
        positions=[_position(opened_at=datetime(2026, 6, 1, 23, 30, 1, tzinfo=UTC))],
        max_hold_minutes=45,
    )
    assert triggers == []


def test_collect_triggers_max_hold_ignores_swing_position() -> None:
    monitor = ExitOrderMonitor()
    triggers = monitor.collect_triggers(
        tick=_tick(price=Decimal("1005")),
        positions=[
            _position(
                holding_type=TradingStyle.SWING,
                opened_at=datetime(2026, 6, 1, 23, 0, 0, tzinfo=UTC),
            )
        ],
        max_hold_minutes=45,
    )
    assert triggers == []


def test_collect_triggers_deduplicates_while_condition_remains_true() -> None:
    monitor = ExitOrderMonitor()
    pos = _position(stop_loss_price=Decimal("980"))
    assert len(monitor.collect_triggers(tick=_tick(price=Decimal("970")), positions=[pos])) == 1
    assert monitor.collect_triggers(tick=_tick(price=Decimal("960")), positions=[pos]) == []
    assert monitor.collect_triggers(tick=_tick(price=Decimal("990")), positions=[pos]) == []
    assert len(monitor.collect_triggers(tick=_tick(price=Decimal("970")), positions=[pos])) == 1


def test_build_exit_order_and_topic_for_live() -> None:
    monitor = ExitOrderMonitor()
    trigger = monitor.collect_triggers(
        tick=_tick(price=Decimal("1050")),
        positions=[_position(target_price=Decimal("1040"))],
    )[0]
    order = build_exit_order(trigger, created_at=datetime(2026, 6, 2, 1, 0, tzinfo=UTC))
    assert order.symbol == "7203"
    assert order.side is Side.SELL
    assert order.quantity == 100
    assert order.trade_mode is TradeMode.LIVE
    assert order.unified_signal_id is None
    assert topic_for_exit_order(trigger, live_topic="live-orders", paper_topic="paper-orders") == (
        "live-orders"
    )
