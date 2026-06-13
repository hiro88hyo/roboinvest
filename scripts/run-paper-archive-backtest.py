#!/usr/bin/env python3
"""Run OMS Paper backtest from archived Gateway orders and feature-engine books."""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from feature_engine.storage.book import enumerate_book_symbols, load_book_partition
from oms_paper.backtest import (
    build_backtest_report,
    iter_order_books,
    iter_order_requests,
    read_positions_json,
    run_backtest,
    write_backtest_report,
    write_jsonl,
    write_positions_json,
)
from oms_paper.backtest.gate import BacktestGateConfig, check_backtest_report
from trade_contracts.enums import TradingStyle
from trade_contracts.market import OrderBookSnapshot


def _decimal(raw: str) -> Decimal:
    return Decimal(raw)


def _parse_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return "n/a" if value is None else str(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-paper-archive-backtest.py",
        description="Archived orders/books から OMS Paper backtest report を生成する。",
    )
    parser.add_argument("--date", dest="target_date", type=date.fromisoformat, required=True)
    parser.add_argument("--trade-mode", default="paper")
    parser.add_argument("--orders-dir", type=Path, default=Path("./data/orders"))
    parser.add_argument("--book-dir", type=Path, default=Path("./data/books"))
    parser.add_argument("--symbols", type=_parse_symbols, default=None)
    parser.add_argument("--positions", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-gate", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--min-execution-quality-count", type=int, default=1)
    parser.add_argument("--min-total-net-pnl", type=_decimal, default=Decimal("0"))
    parser.add_argument("--min-profit-factor", type=_decimal, default=None)
    parser.add_argument("--max-drawdown", type=_decimal, default=None)
    parser.add_argument("--min-average-fill-ratio", type=_decimal, default=Decimal("0.95"))
    parser.add_argument("--max-partial-fill-rate", type=_decimal, default=Decimal("0.05"))
    parser.add_argument("--max-average-spread-bps", type=_decimal, default=Decimal("30"))
    parser.add_argument("--max-spread-bps", type=_decimal, default=Decimal("100"))
    parser.add_argument(
        "--default-holding-type",
        type=TradingStyle,
        choices=list(TradingStyle),
        default=TradingStyle.DAY,
    )
    return parser


def _orders_path(orders_dir: Path, trade_mode: str, target_date: date) -> Path:
    return (
        orders_dir / f"trade_mode={trade_mode}" / f"date={target_date.isoformat()}" / "orders.jsonl"
    )


def _count_jsonl_rows(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _write_books_jsonl(
    *,
    book_dir: Path,
    target_date: date,
    symbols: list[str] | None,
    output_path: Path,
) -> int:
    resolved_symbols = symbols or enumerate_book_symbols(book_dir, target_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for symbol in resolved_symbols:
            df = load_book_partition(book_dir, symbol, target_date)
            if df.is_empty():
                continue
            for row in df.sort("timestamp").iter_rows(named=True):
                book = OrderBookSnapshot.model_validate(
                    {
                        "symbol": row["symbol"],
                        "timestamp": row["timestamp"],
                        "bids": json.loads(row["bids_json"]),
                        "asks": json.loads(row["asks_json"]),
                    }
                )
                f.write(book.model_dump_json())
                f.write("\n")
                count += 1
    return count


def main() -> int:
    args = _build_parser().parse_args()
    orders_path = _orders_path(args.orders_dir, args.trade_mode, args.target_date)
    if not orders_path.exists():
        print(f"NG  orders archive not found: {orders_path}", flush=True)
        return 2

    order_count = _count_jsonl_rows(orders_path)
    if order_count == 0:
        print(f"NG  no archived orders found: {orders_path}", flush=True)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    books_path = args.output_dir / "books.jsonl"
    fill_path = args.output_dir / "fills.jsonl"
    rejected_path = args.output_dir / "rejected.jsonl"
    positions_path = args.output_dir / "positions.json"
    report_path = args.output_dir / "backtest_report.json"
    gate_path = args.output_dir / "gate_report.json"
    summary_path = args.output_dir / "summary.md"
    metadata_path = args.output_dir / "metadata.json"

    book_count = _write_books_jsonl(
        book_dir=args.book_dir,
        target_date=args.target_date,
        symbols=args.symbols,
        output_path=books_path,
    )
    if book_count == 0:
        print(
            f"NG  no order books found: book_dir={args.book_dir} date={args.target_date}",
            flush=True,
        )
        return 2

    initial_positions = read_positions_json(args.positions) if args.positions else {}
    summary = run_backtest(
        orders=iter_order_requests(orders_path),
        books=iter_order_books(books_path),
        initial_positions=initial_positions,
        default_holding_type=args.default_holding_type,
    )

    write_jsonl(summary.fills, fill_path)
    write_jsonl(summary.no_fills, rejected_path)
    write_positions_json(summary.final_positions, positions_path)
    report = build_backtest_report(summary.closed_trades, summary.execution_quality)
    write_backtest_report(report, report_path)

    gate_failed = False
    if args.run_gate:
        config = BacktestGateConfig(
            min_execution_quality_count=args.min_execution_quality_count,
            min_total_net_pnl=args.min_total_net_pnl,
            min_profit_factor=args.min_profit_factor,
            max_drawdown=args.max_drawdown,
            min_average_fill_ratio=args.min_average_fill_ratio,
            max_partial_fill_rate=args.max_partial_fill_rate,
            max_average_spread_bps=args.max_average_spread_bps,
            max_spread_bps=args.max_spread_bps,
        )
        gate = check_backtest_report(
            report.model_dump(mode="json"),
            config=config,
            report_path=str(report_path),
        )
        gate_path.write_text(
            json.dumps(gate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        gate_failed = gate["status"] == "FAIL"
    else:
        gate = None

    metadata: dict[str, Any] = {
        "target_date": args.target_date.isoformat(),
        "trade_mode": args.trade_mode,
        "orders_path": str(orders_path),
        "book_dir": str(args.book_dir),
        "order_count": order_count,
        "book_count": book_count,
        "fill_count": summary.fill_count,
        "no_fill_count": summary.no_fill_count,
        "report_path": str(report_path),
        "gate_path": str(gate_path) if args.run_gate else None,
        "summary_path": str(summary_path) if args.summary else None,
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if args.summary:
        _write_summary(
            path=summary_path,
            target_date=args.target_date,
            report_path=report_path,
            report=report.model_dump(mode="json"),
            gate_path=gate_path if args.run_gate else None,
            gate=gate,
            metadata=metadata,
        )

    print(
        "paper archive backtest done: "
        f"date={args.target_date.isoformat()} orders={order_count} books={book_count} "
        f"fills={summary.fill_count} no_fills={summary.no_fill_count} report={report_path}",
        flush=True,
    )
    if args.run_gate:
        print(f"gate {'FAIL' if gate_failed else 'PASS'}: {gate_path}", flush=True)
    if args.summary:
        print(f"summary: {summary_path}", flush=True)
    return 1 if gate_failed else 0


def _write_summary(
    *,
    path: Path,
    target_date: date,
    report_path: Path,
    report: dict[str, Any],
    gate_path: Path | None,
    gate: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> None:
    status = "n/a" if gate is None else str(gate.get("status", "n/a"))
    lines = [
        "# Paper Archive Backtest Summary",
        "",
        f"- target_date: `{target_date.isoformat()}`",
        f"- backtest_report: `{report_path}`",
        f"- gate_report: `{gate_path or 'n/a'}`",
        f"- gate_status: `{status}`",
        f"- order_count: `{_value(metadata, 'order_count')}`",
        f"- book_count: `{_value(metadata, 'book_count')}`",
        f"- fill_count: `{_value(metadata, 'fill_count')}`",
        f"- no_fill_count: `{_value(metadata, 'no_fill_count')}`",
        "",
        "## Core Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| total_net_pnl | {_value(report, 'total_net_pnl')} |",
        f"| profit_factor | {_value(report, 'profit_factor')} |",
        f"| max_drawdown | {_value(report, 'max_drawdown')} |",
        f"| closed_trade_count | {_value(report, 'closed_trade_count')} |",
        f"| win_rate | {_value(report, 'win_rate')} |",
        "",
        "## Execution Quality",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| execution_quality_count | {_value(report, 'execution_quality_count')} |",
        f"| average_fill_ratio | {_value(report, 'average_fill_ratio')} |",
        f"| partial_fill_count | {_value(report, 'partial_fill_count')} |",
        f"| average_spread_bps | {_value(report, 'average_spread_bps')} |",
        f"| max_spread_bps | {_value(report, 'max_spread_bps')} |",
        f"| average_order_book_imbalance | {_value(report, 'average_order_book_imbalance')} |",
        "",
        "## Gate Failures",
        "",
    ]
    failures = gate.get("failures") if gate is not None else None
    if isinstance(failures, list) and failures:
        lines.extend(f"- {failure}" for failure in failures)
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
