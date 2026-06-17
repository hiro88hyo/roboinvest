from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class BacktestGateConfig:
    min_execution_quality_count: int = 1
    min_total_net_pnl: Decimal = Decimal("0")
    min_profit_factor: Decimal | None = None
    max_drawdown: Decimal | None = None
    min_average_fill_ratio: Decimal = Decimal("0.95")
    max_partial_fill_rate: Decimal = Decimal("0.05")
    max_no_fill_rate: Decimal = Decimal("0.05")
    max_average_spread_bps: Decimal = Decimal("30")
    max_spread_bps: Decimal = Decimal("100")
    max_average_spread_ticks: Decimal = Decimal("2")
    max_spread_ticks: Decimal = Decimal("5")


DEFAULT_BACKTEST_GATE_CONFIG = BacktestGateConfig()


def check_backtest_report(
    payload: dict[str, Any],
    *,
    config: BacktestGateConfig = DEFAULT_BACKTEST_GATE_CONFIG,
    report_path: str,
) -> dict[str, Any]:
    failures = backtest_gate_failures(payload, config=config)
    return {
        "status": "FAIL" if failures else "PASS",
        "report": report_path,
        "failures": failures,
        "metrics": {
            key: payload.get(key)
            for key in (
                "total_net_pnl",
                "profit_factor",
                "max_drawdown",
                "execution_quality_count",
                "average_fill_ratio",
                "partial_fill_count",
                "no_fill_count",
                "no_fill_rate",
                "limit_no_fill_count",
                "average_spread_bps",
                "max_spread_bps",
                "average_spread_ticks",
                "max_spread_ticks",
                "average_order_book_imbalance",
            )
        },
    }


def backtest_gate_failures(
    payload: dict[str, Any],
    *,
    config: BacktestGateConfig = DEFAULT_BACKTEST_GATE_CONFIG,
) -> list[str]:
    failures: list[str] = []

    quality_count = int(payload.get("execution_quality_count") or 0)
    if quality_count < config.min_execution_quality_count:
        failures.append(
            f"execution_quality_count {quality_count} < {config.min_execution_quality_count}"
        )

    total_net = _as_decimal(payload, "total_net_pnl") or Decimal("0")
    if total_net < config.min_total_net_pnl:
        failures.append(f"total_net_pnl {total_net} < {config.min_total_net_pnl}")

    profit_factor = _as_decimal(payload, "profit_factor")
    if config.min_profit_factor is not None:
        if profit_factor is None:
            failures.append(f"profit_factor null < {config.min_profit_factor}")
        elif profit_factor < config.min_profit_factor:
            failures.append(f"profit_factor {profit_factor} < {config.min_profit_factor}")

    max_drawdown = _as_decimal(payload, "max_drawdown") or Decimal("0")
    if config.max_drawdown is not None and max_drawdown > config.max_drawdown:
        failures.append(f"max_drawdown {max_drawdown} > {config.max_drawdown}")

    avg_fill_ratio = _as_decimal(payload, "average_fill_ratio") or Decimal("0")
    if avg_fill_ratio < config.min_average_fill_ratio:
        failures.append(f"average_fill_ratio {avg_fill_ratio} < {config.min_average_fill_ratio}")

    partial_count = Decimal(int(payload.get("partial_fill_count") or 0))
    partial_rate = Decimal("0") if quality_count == 0 else partial_count / Decimal(quality_count)
    if partial_rate > config.max_partial_fill_rate:
        failures.append(f"partial_fill_rate {partial_rate} > {config.max_partial_fill_rate}")

    no_fill_rate = _as_decimal(payload, "no_fill_rate") or Decimal("0")
    if no_fill_rate > config.max_no_fill_rate:
        failures.append(f"no_fill_rate {no_fill_rate} > {config.max_no_fill_rate}")

    avg_spread = _as_decimal(payload, "average_spread_bps")
    if avg_spread is None:
        failures.append("average_spread_bps is null")
    elif avg_spread > config.max_average_spread_bps:
        failures.append(f"average_spread_bps {avg_spread} > {config.max_average_spread_bps}")

    max_spread = _as_decimal(payload, "max_spread_bps")
    if max_spread is None:
        failures.append("max_spread_bps is null")
    elif max_spread > config.max_spread_bps:
        failures.append(f"max_spread_bps {max_spread} > {config.max_spread_bps}")

    avg_spread_ticks = _as_decimal(payload, "average_spread_ticks")
    if avg_spread_ticks is None:
        failures.append("average_spread_ticks is null")
    elif avg_spread_ticks > config.max_average_spread_ticks:
        failures.append(
            f"average_spread_ticks {avg_spread_ticks} > {config.max_average_spread_ticks}"
        )

    max_spread_ticks = _as_decimal(payload, "max_spread_ticks")
    if max_spread_ticks is None:
        failures.append("max_spread_ticks is null")
    elif max_spread_ticks > config.max_spread_ticks:
        failures.append(f"max_spread_ticks {max_spread_ticks} > {config.max_spread_ticks}")

    return failures


def _as_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    return Decimal(str(value))
