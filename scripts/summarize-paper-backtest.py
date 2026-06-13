#!/usr/bin/env python3
"""Generate a Markdown summary from OMS Paper backtest and gate reports."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="summarize-paper-backtest.py",
        description=(
            "OMS Paper backtest_report.json / gate_report.json から Markdown summary を作る。"
        ),
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gate", type=Path, default=None)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--date", dest="target_date", default=None)
    parser.add_argument("--title", default="Paper Archive Backtest Summary")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return "n/a" if value is None else str(value)


def _render_summary(
    *,
    title: str,
    report_path: Path,
    report: dict[str, Any],
    gate_path: Path | None,
    gate: dict[str, Any] | None,
    metadata_path: Path | None,
    metadata: dict[str, Any] | None,
    target_date: str | None,
) -> str:
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    status = "n/a" if gate is None else str(gate.get("status", "n/a"))

    lines = [
        f"# {title}",
        "",
        f"- generated_at: `{generated_at}`",
        f"- target_date: `{target_date or 'n/a'}`",
        f"- backtest_report: `{report_path}`",
        f"- gate_report: `{gate_path or 'n/a'}`",
        f"- metadata: `{metadata_path or 'n/a'}`",
        f"- gate_status: `{status}`",
        "",
    ]
    if metadata is not None:
        lines.extend(
            [
                "## Archive Inputs",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| order_count | {_value(metadata, 'order_count')} |",
                f"| book_count | {_value(metadata, 'book_count')} |",
                f"| fill_count | {_value(metadata, 'fill_count')} |",
                f"| no_fill_count | {_value(metadata, 'no_fill_count')} |",
                "",
            ]
        )

    lines.extend(
        [
            "## Core Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| total_net_pnl | {_value(report, 'total_net_pnl')} |",
            f"| total_gross_pnl | {_value(report, 'total_gross_pnl')} |",
            f"| total_commission | {_value(report, 'total_commission')} |",
            f"| total_slippage | {_value(report, 'total_slippage')} |",
            f"| tax | {_value(report, 'tax')} |",
            f"| closed_trade_count | {_value(report, 'closed_trade_count')} |",
            f"| win_rate | {_value(report, 'win_rate')} |",
            f"| profit_factor | {_value(report, 'profit_factor')} |",
            f"| max_drawdown | {_value(report, 'max_drawdown')} |",
            f"| sharpe_ratio | {_value(report, 'sharpe_ratio')} |",
            f"| expectancy | {_value(report, 'expectancy')} |",
            "",
            "## Execution Quality",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| execution_quality_count | {_value(report, 'execution_quality_count')} |",
            f"| average_fill_ratio | {_value(report, 'average_fill_ratio')} |",
            f"| partial_fill_count | {_value(report, 'partial_fill_count')} |",
            f"| buy_order_count | {_value(report, 'buy_order_count')} |",
            f"| sell_order_count | {_value(report, 'sell_order_count')} |",
            f"| average_spread_bps | {_value(report, 'average_spread_bps')} |",
            f"| max_spread_bps | {_value(report, 'max_spread_bps')} |",
            "| average_opposite_depth_quantity | "
            f"{_value(report, 'average_opposite_depth_quantity')} |",
            f"| average_order_book_imbalance | {_value(report, 'average_order_book_imbalance')} |",
            "",
        ]
    )

    if gate is not None:
        failures = gate.get("failures")
        if isinstance(failures, list) and failures:
            lines.extend(["## Gate Failures", ""])
            lines.extend(f"- {failure}" for failure in failures)
            lines.append("")
        else:
            lines.extend(["## Gate Failures", "", "- none", ""])

    return "\n".join(lines)


def main() -> int:
    args = _build_parser().parse_args()
    report = _load_json_object(args.report)
    gate = _load_json_object(args.gate) if args.gate is not None else None
    metadata = _load_json_object(args.metadata) if args.metadata is not None else None
    markdown = _render_summary(
        title=args.title,
        report_path=args.report,
        report=report,
        gate_path=args.gate,
        gate=gate,
        metadata_path=args.metadata,
        metadata=metadata,
        target_date=args.target_date,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown + "\n", encoding="utf-8")
    print(f"wrote paper backtest summary: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
