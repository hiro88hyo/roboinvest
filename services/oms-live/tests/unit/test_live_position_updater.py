"""position_updater (oms-live) の単体テスト。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from oms_live._testing import make_live_position, make_order_request
from oms_live.models import FillResult, LiveFillRecord
from oms_live.position_updater import (
    apply_fill,
    build_fill_record,
    realized_pnl,
)
from trade_contracts.enums import Side, TradingStyle

EXEC_AT = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)


def _fill(qty: int = 100, price: str = "1000", reason: str = "filled") -> FillResult:
    return FillResult(filled_quantity=qty, fill_price=Decimal(price), reason=reason)


def test_realized_pnl_long_profit() -> None:
    assert realized_pnl(
        entry_price=Decimal("1000"), fill_price=Decimal("1100"), quantity=100
    ) == Decimal("10000")


def test_realized_pnl_long_loss() -> None:
    assert realized_pnl(
        entry_price=Decimal("1000"), fill_price=Decimal("950"), quantity=100
    ) == Decimal("-5000")


def test_build_fill_record_returns_live_record() -> None:
    order = make_order_request(side=Side.BUY, quantity=200)
    rec = build_fill_record(
        order=order,
        fill=_fill(qty=200, price="1234.5"),
        executed_at=EXEC_AT,
    )
    assert isinstance(rec, LiveFillRecord)
    assert rec.symbol == order.symbol
    assert rec.side is Side.BUY
    assert rec.quantity == 200
    assert rec.price == Decimal("1234.5")
    assert rec.unified_signal_id == order.unified_signal_id
    assert rec.executed_at == EXEC_AT
    # 冪等性キーが carry されること
    assert rec.order_id == order.order_id


def test_build_fill_record_returns_none_on_no_fill() -> None:
    order = make_order_request()
    rec = build_fill_record(
        order=order,
        fill=FillResult(filled_quantity=0, fill_price=None, reason="cancelled"),
        executed_at=EXEC_AT,
    )
    assert rec is None


def test_apply_fill_buy_creates_new_position() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    update = apply_fill(
        order=order,
        fill=_fill(qty=100, price="1000"),
        existing=None,
        holding_type=TradingStyle.DAY,
        stop_loss_price=Decimal("950"),
        executed_at=EXEC_AT,
    )
    assert update.error is None
    assert update.delete is False
    assert update.realized_pnl is None
    assert update.position is not None
    assert update.position.symbol == order.symbol
    assert update.position.quantity == 100
    assert update.position.entry_price == Decimal("1000")
    assert update.position.holding_type is TradingStyle.DAY
    assert update.position.stop_loss_price == Decimal("950")


def test_apply_fill_buy_adds_to_existing_with_weighted_average() -> None:
    existing = make_live_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.BUY, quantity=200)
    update = apply_fill(
        order=order,
        fill=_fill(qty=200, price="1100"),
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.position is not None
    assert update.position.quantity == 300
    # (1000*100 + 1100*200) / 300 = 320000/300 = 1066.66... -> ROUND_HALF_UP -> 1067
    assert update.position.entry_price == Decimal("1067")
    assert update.realized_pnl is None


def test_apply_fill_sell_full_close_returns_delete_and_realized_pnl() -> None:
    existing = make_live_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.SELL, quantity=100)
    update = apply_fill(
        order=order,
        fill=_fill(qty=100, price="1100"),
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.delete is True
    assert update.position is None
    assert update.realized_pnl == Decimal("10000")
    assert update.error is None


def test_apply_fill_sell_partial_keeps_position_with_pnl() -> None:
    existing = make_live_position(quantity=300, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.SELL, quantity=100)
    update = apply_fill(
        order=order,
        fill=_fill(qty=100, price="950"),
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.delete is False
    assert update.position is not None
    assert update.position.quantity == 200
    assert update.position.entry_price == Decimal("1000")  # entry は不変
    assert update.realized_pnl == Decimal("-5000")


def test_apply_fill_sell_without_position_returns_error() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    update = apply_fill(
        order=order,
        fill=_fill(qty=100, price="1100"),
        existing=None,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.error == "no_position_for_sell"
    assert update.delete is False
    assert update.realized_pnl is None


def test_apply_fill_oversell_returns_error_and_keeps_existing() -> None:
    existing = make_live_position(quantity=100)
    order = make_order_request(side=Side.SELL, quantity=200)
    update = apply_fill(
        order=order,
        fill=_fill(qty=200, price="1100"),
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.error == "oversell"
    assert update.position == existing
    assert update.realized_pnl is None


def test_apply_fill_no_fill_returns_existing_with_error() -> None:
    existing = make_live_position()
    order = make_order_request(side=Side.BUY, quantity=100)
    update = apply_fill(
        order=order,
        fill=FillResult(filled_quantity=0, fill_price=None, reason="cancelled"),
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=EXEC_AT,
    )
    assert update.error == "no_fill"
    assert update.position == existing
    assert update.realized_pnl is None
