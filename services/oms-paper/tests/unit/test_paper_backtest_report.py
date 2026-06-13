from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from oms_paper._testing import DEFAULT_TS
from oms_paper.backtest.report import (
    ClosedTrade,
    ExecutionQualityRecord,
    build_backtest_report,
)
from trade_contracts.enums import Side


def _closed_trade(*, gross_pnl: Decimal, executed_offset_seconds: int = 0) -> ClosedTrade:
    quantity = 100
    entry_price = Decimal("1000")
    exit_price = Decimal("1000") + (gross_pnl / Decimal(quantity))
    entry_notional = entry_price * quantity
    exit_notional = exit_price * quantity
    commission = (entry_notional + exit_notional) * Decimal("0.00099")
    return ClosedTrade(
        symbol="7203",
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        gross_pnl=gross_pnl,
        commission=commission,
        net_pnl_before_tax=gross_pnl - commission,
        executed_at=DEFAULT_TS + timedelta(seconds=executed_offset_seconds),
    )


def test_build_backtest_report_includes_costs_tax_and_core_metrics() -> None:
    report = build_backtest_report(
        [
            _closed_trade(gross_pnl=Decimal("10000"), executed_offset_seconds=1),
            _closed_trade(gross_pnl=Decimal("-5000"), executed_offset_seconds=2),
        ]
    )

    assert report.closed_trade_count == 2
    assert report.total_gross_pnl == Decimal("5000")
    assert report.total_commission == Decimal("400.95000")
    assert report.total_slippage == Decimal("202.5000")
    assert report.tax == Decimal("893.1591325000")
    assert report.total_net_pnl == Decimal("3503.3908675000")
    assert report.win_rate == Decimal("0.5")
    assert report.profit_factor == Decimal("1.831019459224466265322130969")
    assert report.max_drawdown == Decimal("5290.55000")
    assert report.expectancy == Decimal("1751.6954337500")
    assert report.sharpe_ratio is not None


def test_build_backtest_report_handles_no_closed_trades() -> None:
    report = build_backtest_report([])

    assert report.closed_trade_count == 0
    assert report.total_net_pnl == Decimal("0")
    assert report.win_rate == Decimal("0")
    assert report.profit_factor is None
    assert report.max_drawdown == Decimal("0")
    assert report.sharpe_ratio is None
    assert report.expectancy == Decimal("0")


def test_build_backtest_report_includes_execution_quality_metrics() -> None:
    report = build_backtest_report(
        [],
        [
            ExecutionQualityRecord(
                symbol="7203",
                side=Side.BUY,
                requested_quantity=100,
                filled_quantity=100,
                fill_ratio=Decimal("1"),
                reason="filled",
                order_created_at=DEFAULT_TS,
                book_timestamp=DEFAULT_TS,
                best_bid=Decimal("999"),
                best_ask=Decimal("1000"),
                spread_bps=Decimal("10.0"),
                opposite_depth_quantity=200,
                same_side_depth_quantity=300,
                order_book_imbalance=Decimal("0.2"),
            ),
            ExecutionQualityRecord(
                symbol="7203",
                side=Side.SELL,
                requested_quantity=100,
                filled_quantity=50,
                fill_ratio=Decimal("0.5"),
                reason="partial",
                order_created_at=DEFAULT_TS,
                book_timestamp=DEFAULT_TS,
                best_bid=Decimal("998"),
                best_ask=Decimal("1000"),
                spread_bps=Decimal("20.0"),
                opposite_depth_quantity=100,
                same_side_depth_quantity=400,
                order_book_imbalance=Decimal("-0.6"),
            ),
        ],
    )

    assert report.execution_quality_count == 2
    assert report.average_spread_bps == Decimal("15.0")
    assert report.max_spread_bps == Decimal("20.0")
    assert report.average_fill_ratio == Decimal("0.75")
    assert report.partial_fill_count == 1
    assert report.buy_order_count == 1
    assert report.sell_order_count == 1
    assert report.average_opposite_depth_quantity == Decimal("150")
    assert report.average_order_book_imbalance == Decimal("-0.2")
