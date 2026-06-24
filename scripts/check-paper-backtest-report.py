#!/usr/bin/env python3
"""Check OMS Paper backtest_report.json against execution-quality gates."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from oms_paper.backtest.gate import BacktestGateConfig, check_backtest_report


def _decimal(raw: str) -> Decimal:
    return Decimal(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check-paper-backtest-report.py",
        description="OMS Paper backtest_report.json の PnL / execution quality gate を判定する。",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-execution-quality-count", type=int, default=1)
    parser.add_argument("--min-total-net-pnl", type=_decimal, default=Decimal("0"))
    parser.add_argument("--min-profit-factor", type=_decimal, default=None)
    parser.add_argument("--max-drawdown", type=_decimal, default=None)
    parser.add_argument("--min-average-fill-ratio", type=_decimal, default=Decimal("0.95"))
    parser.add_argument("--max-partial-fill-rate", type=_decimal, default=Decimal("0.05"))
    parser.add_argument("--max-no-fill-rate", type=_decimal, default=Decimal("0.05"))
    parser.add_argument("--max-average-spread-bps", type=_decimal, default=Decimal("30"))
    parser.add_argument("--max-spread-bps", type=_decimal, default=Decimal("100"))
    parser.add_argument("--max-average-spread-ticks", type=_decimal, default=Decimal("2"))
    parser.add_argument("--max-spread-ticks", type=_decimal, default=Decimal("5"))
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print(f"NG  report must be a JSON object: {args.report}", flush=True)
        return 2

    config = BacktestGateConfig(
        min_execution_quality_count=args.min_execution_quality_count,
        min_total_net_pnl=args.min_total_net_pnl,
        min_profit_factor=args.min_profit_factor,
        max_drawdown=args.max_drawdown,
        min_average_fill_ratio=args.min_average_fill_ratio,
        max_partial_fill_rate=args.max_partial_fill_rate,
        max_no_fill_rate=args.max_no_fill_rate,
        max_average_spread_bps=args.max_average_spread_bps,
        max_spread_bps=args.max_spread_bps,
        max_average_spread_ticks=args.max_average_spread_ticks,
        max_spread_ticks=args.max_spread_ticks,
    )
    result = check_backtest_report(payload, config=config, report_path=str(args.report))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    print(f"{result['status']}  {args.report}", flush=True)
    for failure in result["failures"]:
        print(f"- {failure}", flush=True)
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
