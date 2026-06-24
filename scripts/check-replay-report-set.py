#!/usr/bin/env python3
"""Check a multi-day OMS Paper replay report set.

Single-day reports are too noisy for strategy acceptance. This helper aggregates
multiple ``backtest-report.json`` files and optionally compares a stressed
execution run, such as BUY +1 tick, against explicit acceptance gates.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReportRow:
    path: Path
    total_net_pnl: Decimal
    closed_trade_count: int
    execution_quality_count: int
    no_fill_count: int


@dataclass(frozen=True, slots=True)
class Summary:
    report_count: int
    total_net_pnl: Decimal
    closed_trade_count: int
    execution_quality_count: int
    no_fill_count: int
    weighted_no_fill_rate: Decimal
    positive_day_count: int
    negative_day_count: int
    flat_day_count: int


def main() -> int:
    args = build_parser().parse_args()
    rows = read_reports(args.report)
    summary = summarize(rows)
    stress_summary = summarize(read_reports(args.stress_report)) if args.stress_report else None
    result = check(
        label=args.label,
        summary=summary,
        stress_summary=stress_summary,
        min_total_net_pnl=args.min_total_net_pnl,
        min_closed_trades=args.min_closed_trades,
        min_positive_days=args.min_positive_days,
        max_weighted_no_fill_rate=args.max_weighted_no_fill_rate,
        min_stress_total_net_pnl=args.min_stress_total_net_pnl,
    )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(f"{result['status']}  {args.label}")
    print(
        "base: "
        f"net={summary.total_net_pnl} closed={summary.closed_trade_count} "
        f"positive_days={summary.positive_day_count}/{summary.report_count} "
        f"weighted_no_fill={summary.weighted_no_fill_rate}"
    )
    if stress_summary is not None:
        print(
            "stress: "
            f"net={stress_summary.total_net_pnl} closed={stress_summary.closed_trade_count} "
            f"positive_days={stress_summary.positive_day_count}/{stress_summary.report_count} "
            f"weighted_no_fill={stress_summary.weighted_no_fill_rate}"
        )
    for failure in result["failures"]:
        print(f"- {failure}")
    return 1 if result["failures"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check-replay-report-set.py",
        description="複数日の OMS Paper replay report を strategy acceptance gate で判定する。",
    )
    parser.add_argument("--label", default="replay-report-set")
    parser.add_argument("--report", type=Path, nargs="+", required=True)
    parser.add_argument("--stress-report", type=Path, nargs="+", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-total-net-pnl", type=decimal_arg, default=Decimal("0"))
    parser.add_argument("--min-closed-trades", type=int, default=20)
    parser.add_argument("--min-positive-days", type=int, default=3)
    parser.add_argument("--max-weighted-no-fill-rate", type=decimal_arg, default=Decimal("0.30"))
    parser.add_argument("--min-stress-total-net-pnl", type=decimal_arg, default=Decimal("0"))
    return parser


def read_reports(paths: list[Path]) -> list[ReportRow]:
    rows: list[ReportRow] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"report must be a JSON object: {path}")
        rows.append(
            ReportRow(
                path=path,
                total_net_pnl=decimal_field(payload, "total_net_pnl"),
                closed_trade_count=int(payload.get("closed_trade_count") or 0),
                execution_quality_count=int(payload.get("execution_quality_count") or 0),
                no_fill_count=int(payload.get("no_fill_count") or 0),
            )
        )
    return rows


def summarize(rows: list[ReportRow]) -> Summary:
    if not rows:
        raise ValueError("at least one report is required")
    total_net_pnl = sum((row.total_net_pnl for row in rows), Decimal("0"))
    closed_trade_count = sum(row.closed_trade_count for row in rows)
    execution_quality_count = sum(row.execution_quality_count for row in rows)
    no_fill_count = sum(row.no_fill_count for row in rows)
    weighted_no_fill_rate = (
        Decimal("0")
        if execution_quality_count == 0
        else Decimal(no_fill_count) / Decimal(execution_quality_count)
    )
    return Summary(
        report_count=len(rows),
        total_net_pnl=total_net_pnl,
        closed_trade_count=closed_trade_count,
        execution_quality_count=execution_quality_count,
        no_fill_count=no_fill_count,
        weighted_no_fill_rate=weighted_no_fill_rate,
        positive_day_count=sum(1 for row in rows if row.total_net_pnl > 0),
        negative_day_count=sum(1 for row in rows if row.total_net_pnl < 0),
        flat_day_count=sum(1 for row in rows if row.total_net_pnl == 0),
    )


def check(
    *,
    label: str,
    summary: Summary,
    stress_summary: Summary | None,
    min_total_net_pnl: Decimal,
    min_closed_trades: int,
    min_positive_days: int,
    max_weighted_no_fill_rate: Decimal,
    min_stress_total_net_pnl: Decimal,
) -> dict[str, Any]:
    failures: list[str] = []
    if summary.total_net_pnl < min_total_net_pnl:
        failures.append(f"total_net_pnl {summary.total_net_pnl} < {min_total_net_pnl}")
    if summary.closed_trade_count < min_closed_trades:
        failures.append(f"closed_trade_count {summary.closed_trade_count} < {min_closed_trades}")
    if summary.positive_day_count < min_positive_days:
        failures.append(f"positive_day_count {summary.positive_day_count} < {min_positive_days}")
    if summary.weighted_no_fill_rate > max_weighted_no_fill_rate:
        failures.append(
            f"weighted_no_fill_rate {summary.weighted_no_fill_rate} > {max_weighted_no_fill_rate}"
        )
    if stress_summary is not None and stress_summary.total_net_pnl < min_stress_total_net_pnl:
        failures.append(
            f"stress_total_net_pnl {stress_summary.total_net_pnl} < {min_stress_total_net_pnl}"
        )

    return {
        "status": "FAIL" if failures else "PASS",
        "label": label,
        "failures": failures,
        "metrics": encode_summary(summary),
        "stress_metrics": None if stress_summary is None else encode_summary(stress_summary),
        "thresholds": {
            "min_total_net_pnl": str(min_total_net_pnl),
            "min_closed_trades": min_closed_trades,
            "min_positive_days": min_positive_days,
            "max_weighted_no_fill_rate": str(max_weighted_no_fill_rate),
            "min_stress_total_net_pnl": str(min_stress_total_net_pnl),
        },
    }


def encode_summary(summary: Summary) -> dict[str, Any]:
    return {
        "report_count": summary.report_count,
        "total_net_pnl": str(summary.total_net_pnl),
        "closed_trade_count": summary.closed_trade_count,
        "execution_quality_count": summary.execution_quality_count,
        "no_fill_count": summary.no_fill_count,
        "weighted_no_fill_rate": str(summary.weighted_no_fill_rate),
        "positive_day_count": summary.positive_day_count,
        "negative_day_count": summary.negative_day_count,
        "flat_day_count": summary.flat_day_count,
    }


def decimal_field(payload: dict[str, Any], key: str) -> Decimal:
    value = payload.get(key)
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def decimal_arg(raw: str) -> Decimal:
    return Decimal(raw)


if __name__ == "__main__":
    raise SystemExit(main())
