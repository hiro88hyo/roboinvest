#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Report paper observation signals for the current hardening rollout.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/report-paper-observation.py
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=datetime.now(JST).date(),
        help="JST trading date to report, YYYY-MM-DD. Defaults to today in JST.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--max-rows",
        type=int,
        default=12,
        help="Maximum detail rows to print per section.",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _date_bounds_jst(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, JST).astimezone(UTC)
    end = datetime.combine(trading_date + timedelta(days=1), time.min, JST).astimezone(UTC)
    return start, end


def _get(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def _fetch_day_rows(
    client: httpx.Client,
    *,
    table: str,
    timestamp_field: str,
    trading_date: date,
    select: str,
    order: str,
) -> list[dict[str, Any]]:
    start, end = _date_bounds_jst(trading_date)
    return _get(
        client,
        f"/rest/v1/{table}",
        {
            "select": select,
            "and": (
                f"({timestamp_field}.gte.{start.isoformat()},"
                f"{timestamp_field}.lt.{end.isoformat()})"
            ),
            "order": order,
        },
    )


def _fetch_positions(client: httpx.Client, *, trade_type: str) -> list[dict[str, Any]]:
    return _get(
        client,
        "/rest/v1/positions",
        {
            "trade_type": f"eq.{trade_type}",
            "select": (
                "symbol,quantity,entry_price,current_price,unrealized_pnl,"
                "holding_type,target_price,stop_loss_price,trailing_stop_pct,opened_at"
            ),
            "order": "symbol.asc",
        },
    )


def _count_by(rows: list[dict[str, Any]], *fields: str) -> Counter[tuple[str, ...]]:
    counts: Counter[tuple[str, ...]] = Counter()
    for row in rows:
        counts[tuple(str(row.get(field, "")) for field in fields)] += 1
    return counts


def _print_counts(label: str, counts: Counter[tuple[str, ...]]) -> None:
    print(label)
    if not counts:
        print("  none")
        return
    for key, count in sorted(counts.items()):
        print(f"  {' / '.join(key)}: {count}")


def _print_rows(label: str, rows: list[dict[str, Any]], *, max_rows: int) -> None:
    print(label)
    if not rows:
        print("  none")
        return
    for row in rows[:max_rows]:
        print("  " + _format_row(row))
    if len(rows) > max_rows:
        print(f"  ... {len(rows) - max_rows} more")


def _format_row(row: dict[str, Any]) -> str:
    preferred = (
        "executed_at",
        "created_at",
        "opened_at",
        "symbol",
        "side",
        "action",
        "quantity",
        "price",
        "confidence",
        "signal_source",
        "source",
        "unified_signal_id",
        "holding_type",
        "entry_price",
        "current_price",
        "unrealized_pnl",
        "target_price",
        "stop_loss_price",
    )
    parts = [f"{key}={row.get(key)}" for key in preferred if key in row]
    return " ".join(parts)


def _print_cloud_logging_filters(trading_date: date) -> None:
    print("== Cloud Logging filters ==")
    print(f"# Date context: {trading_date.isoformat()} JST")
    print(
        'logName:"roboinvest"\n'
        'jsonPayload.service="oms-paper"\n'
        '(jsonPayload.event="day_stop_exit" OR jsonPayload.event="day_stop_trail")'
    )
    print()
    print(
        'logName:"roboinvest"\n'
        'jsonPayload.service="oms-live"\n'
        '(jsonPayload.event="live_stop_exit" OR jsonPayload.event="live_stop_trail")'
    )
    print()
    print(
        'logName:"roboinvest"\njsonPayload.service="gateway"\njsonPayload.event="signal_rejected"'
    )
    print()
    print('logName:"roboinvest"\njsonPayload.event="order_published"')


def main() -> int:
    args = parse_args()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY missing", file=sys.stderr)
        return 2

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        strategy_rows = _fetch_day_rows(
            client,
            table="strategy_logs",
            timestamp_field="created_at",
            trading_date=args.date,
            select="created_at,source,symbol,action,confidence,reasoning",
            order="created_at.asc",
        )
        aggregator_rows = _fetch_day_rows(
            client,
            table="aggregator_logs",
            timestamp_field="created_at",
            trading_date=args.date,
            select="created_at,symbol,action,confidence,signal_source,signal_id",
            order="created_at.asc",
        )
        paper_trades = _fetch_day_rows(
            client,
            table="trades_paper",
            timestamp_field="executed_at",
            trading_date=args.date,
            select="executed_at,symbol,side,quantity,price,signal_source,unified_signal_id",
            order="executed_at.asc",
        )
        paper_positions = _fetch_positions(client, trade_type="paper")
        live_positions = _fetch_positions(client, trade_type="live")

    paper_stop_sells = [
        row
        for row in paper_trades
        if row.get("side") == "SELL" and row.get("unified_signal_id") is None
    ]

    print(f"== Paper observation {args.date.isoformat()} JST ==")
    _print_counts("strategy_logs by source/action", _count_by(strategy_rows, "source", "action"))
    _print_counts(
        "aggregator_logs by signal_source/action",
        _count_by(aggregator_rows, "signal_source", "action"),
    )
    _print_counts("trades_paper by side", _count_by(paper_trades, "side"))
    print(f"paper_stop_or_closeout_sells: {len(paper_stop_sells)}")
    print(f"open_positions: paper={len(paper_positions)} live={len(live_positions)}")
    print()

    _print_rows(
        "paper stop/closeout SELL rows (unified_signal_id is null)",
        paper_stop_sells,
        max_rows=args.max_rows,
    )
    _print_rows("open paper positions", paper_positions, max_rows=args.max_rows)
    _print_rows("recent aggregator logs", aggregator_rows[-args.max_rows :], max_rows=args.max_rows)
    print()
    _print_cloud_logging_filters(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
