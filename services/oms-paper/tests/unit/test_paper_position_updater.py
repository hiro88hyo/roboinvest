from __future__ import annotations

from datetime import date
from decimal import Decimal

from oms_paper._testing import DEFAULT_TS, make_order_request, make_paper_position
from oms_paper.models import FillResult
from oms_paper.position_updater import apply_fill, build_fill_record
from trade_contracts.enums import Side, TradingStyle


def test_buy_no_existing_creates_new_position() -> None:
    order = make_order_request(side=Side.BUY, quantity=200)
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1000"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=None,
        holding_type=TradingStyle.DAY,
        stop_loss_price=Decimal("980"),
        executed_at=DEFAULT_TS,
    )
    assert upd.error is None
    assert upd.delete is False
    assert upd.position is not None
    assert upd.position.symbol == order.symbol
    assert upd.position.quantity == 200
    assert upd.position.entry_price == Decimal("1000")
    assert upd.position.holding_type is TradingStyle.DAY
    assert upd.position.stop_loss_price == Decimal("980")


def test_buy_averages_into_existing_position() -> None:
    existing = make_paper_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.BUY, quantity=300)
    fill = FillResult(filled_quantity=300, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    # (1000*100 + 1100*300)/400 = 430_000/400 = 1075
    assert upd.error is None
    assert upd.position is not None
    assert upd.position.quantity == 400
    assert upd.position.entry_price == Decimal("1075")


def test_buy_partial_fill_uses_filled_quantity() -> None:
    order = make_order_request(side=Side.BUY, quantity=500)
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1000"), reason="partial")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=None,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.position is not None
    assert upd.position.quantity == 200


def test_buy_relative_stop_is_anchored_to_actual_partial_fill_price() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=500,
        stop_loss_pct=Decimal("0.10"),
    )
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1003"), reason="partial")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=None,
        holding_type=TradingStyle.SWING,
        stop_loss_pct=order.stop_loss_pct,
        executed_at=DEFAULT_TS,
    )

    assert upd.position is not None
    assert upd.position.quantity == 200
    assert upd.position.entry_price == Decimal("1003")
    assert upd.position.stop_loss_price == Decimal("902.70")


def test_buy_into_existing_position_preserves_order_management_metadata() -> None:
    scheduled_exit = date(2026, 5, 15)
    existing = make_paper_position(
        quantity=100,
        entry_price=Decimal("1000"),
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("900"),
        target_price=Decimal("1200"),
        max_hold_days=10,
        scheduled_exit_date=scheduled_exit,
        trailing_stop_pct=Decimal("0.03"),
    )
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        holding_type=TradingStyle.DAY,
        stop_loss_pct=Decimal("0.20"),
        max_hold_days=2,
    )
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        stop_loss_pct=order.stop_loss_pct,
        max_hold_days=order.max_hold_days,
        executed_at=DEFAULT_TS,
    )

    assert upd.position is not None
    assert upd.position.quantity == 200
    assert upd.position.entry_price == Decimal("1050")
    assert upd.position.holding_type is TradingStyle.SWING
    assert upd.position.stop_loss_price == Decimal("900")
    assert upd.position.target_price == Decimal("1200")
    assert upd.position.max_hold_days == 10
    assert upd.position.scheduled_exit_date == scheduled_exit
    assert upd.position.trailing_stop_pct == Decimal("0.03")


def test_sell_full_close_marks_delete() -> None:
    existing = make_paper_position(quantity=200, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.SELL, quantity=200)
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.delete is True
    assert upd.position is None
    assert upd.error is None


def test_sell_partial_reduces_quantity() -> None:
    existing = make_paper_position(quantity=300, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.SELL, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.delete is False
    assert upd.position is not None
    assert upd.position.quantity == 200
    assert upd.position.entry_price == Decimal("1000")  # 既存の取得単価を維持


def test_sell_without_position_returns_error() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=None,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.error == "no_position_for_sell"
    assert upd.delete is False
    assert upd.position is None


def test_oversell_keeps_existing_with_error() -> None:
    existing = make_paper_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.SELL, quantity=200)
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1100"), reason="filled")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.error == "oversell"
    assert upd.position == existing
    assert upd.delete is False


def test_no_fill_returns_existing() -> None:
    existing = make_paper_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=0, fill_price=None, reason="empty_book")
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.error == "no_fill"
    assert upd.position == existing
    assert upd.delete is False


def test_build_fill_record_returns_none_for_no_fill() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=0, fill_price=None, reason="empty_book")
    rec = build_fill_record(order=order, fill=fill, executed_at=DEFAULT_TS)
    assert rec is None


def test_build_fill_record_carries_signal_metadata() -> None:
    order = make_order_request(side=Side.BUY, quantity=200)
    fill = FillResult(filled_quantity=200, fill_price=Decimal("1000"), reason="filled")
    rec = build_fill_record(order=order, fill=fill, executed_at=DEFAULT_TS)
    assert rec is not None
    assert rec.symbol == order.symbol
    assert rec.side is Side.BUY
    assert rec.quantity == 200
    assert rec.price == Decimal("1000")
    assert rec.signal_source == order.signal_source
    assert rec.unified_signal_id == order.unified_signal_id
    assert rec.executed_at == DEFAULT_TS


def test_buy_average_round_half_up() -> None:
    existing = make_paper_position(quantity=100, entry_price=Decimal("1000"))
    order = make_order_request(side=Side.BUY, quantity=100)
    fill = FillResult(filled_quantity=100, fill_price=Decimal("1001"), reason="filled")
    # (1000*100 + 1001*100)/200 = 1000.5 -> ROUND_HALF_UP -> 1001
    upd = apply_fill(
        order=order,
        fill=fill,
        existing=existing,
        holding_type=TradingStyle.DAY,
        executed_at=DEFAULT_TS,
    )
    assert upd.position is not None
    assert upd.position.entry_price == Decimal("1001")
