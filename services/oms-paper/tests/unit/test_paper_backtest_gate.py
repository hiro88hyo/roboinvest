from __future__ import annotations

from decimal import Decimal

from oms_paper.backtest.gate import BacktestGateConfig, backtest_gate_failures


def _report(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "total_net_pnl": "1000",
        "profit_factor": "1.5",
        "max_drawdown": "500",
        "execution_quality_count": 10,
        "average_fill_ratio": "0.99",
        "partial_fill_count": 0,
        "no_fill_count": 0,
        "no_fill_rate": "0",
        "limit_no_fill_count": 0,
        "average_spread_bps": "12",
        "max_spread_bps": "40",
        "average_spread_ticks": "1",
        "max_spread_ticks": "2",
    }
    payload.update(overrides)
    return payload


def test_backtest_gate_passes_default_quality_thresholds() -> None:
    assert backtest_gate_failures(_report()) == []


def test_backtest_gate_fails_profit_factor_when_required() -> None:
    failures = backtest_gate_failures(
        _report(profit_factor="1.1"),
        config=BacktestGateConfig(min_profit_factor=Decimal("1.2")),
    )
    assert failures == ["profit_factor 1.1 < 1.2"]


def test_backtest_gate_fails_execution_quality_metrics() -> None:
    failures = backtest_gate_failures(
        _report(
            average_fill_ratio="0.8",
            partial_fill_count=2,
            no_fill_rate="0.2",
            average_spread_bps="50",
            max_spread_bps="120",
            average_spread_ticks="3",
            max_spread_ticks="8",
        )
    )
    assert failures == [
        "average_fill_ratio 0.8 < 0.95",
        "partial_fill_rate 0.2 > 0.05",
        "no_fill_rate 0.2 > 0.05",
        "average_spread_bps 50 > 30",
        "max_spread_bps 120 > 100",
        "average_spread_ticks 3 > 2",
        "max_spread_ticks 8 > 5",
    ]
