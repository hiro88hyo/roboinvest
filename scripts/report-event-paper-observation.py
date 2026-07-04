#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True, slots=True)
class SupabaseRows:
    strategy_logs: list[dict[str, Any]]
    aggregator_logs: list[dict[str, Any]]
    trades_paper: list[dict[str, Any]]
    positions: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile event cluster paper candidates with Supabase paper execution rows."
    )
    parser.add_argument("--candidates-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--to-date", type=date.fromisoformat)
    parser.add_argument("--skip-supabase", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    payload = json.loads(args.candidates_json.read_text(encoding="utf-8"))
    rows: SupabaseRows | None = None
    if not args.skip_supabase:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        if url and key:
            with httpx.Client(
                base_url=url.rstrip("/"),
                timeout=args.timeout,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                rows = fetch_supabase_rows(
                    client,
                    payload=payload,
                    from_date=args.from_date,
                    to_date=args.to_date,
                )
        else:
            print(
                "SUPABASE_URL / SUPABASE_SECRET_KEY missing; writing candidate-only report",
                file=sys.stderr,
            )

    report = build_report(payload, rows=rows)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, report["rows"])
    print(
        "event_paper_observation_report "
        f"candidates={report['summary']['candidate_count']} "
        f"with_supabase={report['summary']['with_supabase']} "
        f"output={args.output_json}"
    )
    return 0


def fetch_supabase_rows(
    client: httpx.Client,
    *,
    payload: dict[str, Any],
    from_date: date | None = None,
    to_date: date | None = None,
) -> SupabaseRows:
    signal_ids = _published_signal_ids(payload)
    symbols = sorted({str(row["symbol"]) for row in payload.get("candidates", [])})
    strategy_logs = (
        _get(
            client,
            "/rest/v1/strategy_logs",
            {
                "select": "signal_id,source,symbol,action,confidence,reasoning,created_at",
                "signal_id": _in_filter(signal_ids),
            },
        )
        if signal_ids
        else []
    )
    aggregator_logs = (
        _get(
            client,
            "/rest/v1/aggregator_logs",
            {
                "select": (
                    "signal_id,symbol,action,confidence,signal_source,"
                    "strategy_signal_id_a,strategy_signal_id_b,created_at"
                ),
                "strategy_signal_id_a": _in_filter(signal_ids),
            },
        )
        if signal_ids
        else []
    )
    unified_ids = [str(row["signal_id"]) for row in aggregator_logs]
    start, end = _date_bounds(payload, from_date=from_date, to_date=to_date)
    trade_filters = {
        "select": (
            "trade_id,symbol,side,quantity,price,signal_source,unified_signal_id,executed_at"
        ),
        "order": "executed_at.asc",
    }
    if symbols:
        trade_filters["symbol"] = _in_filter(symbols)
    if start is not None:
        trade_filters["executed_at"] = f"gte.{start.isoformat()}"
    if end is not None:
        trade_filters["executed_at"] = f"lt.{end.isoformat()}"
    trades_paper = _get(client, "/rest/v1/trades_paper", trade_filters) if symbols else []
    if unified_ids:
        linked_trades = _get(
            client,
            "/rest/v1/trades_paper",
            {
                "select": (
                    "trade_id,symbol,side,quantity,price,signal_source,"
                    "unified_signal_id,executed_at"
                ),
                "unified_signal_id": _in_filter(unified_ids),
                "order": "executed_at.asc",
            },
        )
        trades_paper = _dedupe_by_key(trades_paper + linked_trades, "trade_id")
    positions = (
        _get(
            client,
            "/rest/v1/positions",
            {
                "select": (
                    "symbol,trade_type,side,quantity,entry_price,current_price,"
                    "unrealized_pnl,holding_type,stop_loss_price,max_hold_days,"
                    "scheduled_exit_date,opened_at"
                ),
                "trade_type": "eq.paper",
                "symbol": _in_filter(symbols),
                "order": "symbol.asc",
            },
        )
        if symbols
        else []
    )
    return SupabaseRows(
        strategy_logs=strategy_logs,
        aggregator_logs=aggregator_logs,
        trades_paper=trades_paper,
        positions=positions,
    )


def build_report(payload: dict[str, Any], *, rows: SupabaseRows | None) -> dict[str, Any]:
    published_by_symbol = {
        str(row.get("symbol")): row for row in payload.get("published", []) if row.get("symbol")
    }
    strategy_by_id = _index(rows.strategy_logs if rows else [], "signal_id")
    aggregator_by_strategy_id = _index(rows.aggregator_logs if rows else [], "strategy_signal_id_a")
    positions_by_symbol = _index(rows.positions if rows else [], "symbol")
    trades = rows.trades_paper if rows else []
    trades_by_unified = _group(trades, "unified_signal_id")
    trades_by_symbol = _group(trades, "symbol")

    out_rows: list[dict[str, Any]] = []
    for candidate in payload.get("candidates", []):
        symbol = str(candidate["symbol"])
        published = published_by_symbol.get(symbol, {})
        strategy_signal_id = published.get("signal_id")
        strategy_log = strategy_by_id.get(str(strategy_signal_id)) if strategy_signal_id else None
        aggregator_log = (
            aggregator_by_strategy_id.get(str(strategy_signal_id)) if strategy_signal_id else None
        )
        unified_signal_id = None if aggregator_log is None else aggregator_log.get("signal_id")
        linked_trades = (
            trades_by_unified.get(str(unified_signal_id), []) if unified_signal_id else []
        )
        symbol_trades = trades_by_symbol.get(symbol, [])
        buy_trade = _first_trade(linked_trades, side="BUY") or _first_trade(
            symbol_trades,
            side="BUY",
        )
        sell_trade = _first_trade(linked_trades, side="SELL") or _first_trade(
            symbol_trades,
            side="SELL",
        )
        position = positions_by_symbol.get(symbol)
        intended = _decimal(candidate.get("entry_price_assumption"))
        buy_price = _decimal(None if buy_trade is None else buy_trade.get("price"))
        out_rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "symbol": symbol,
                "signal_date": candidate.get("signal_date"),
                "entry_date": candidate.get("entry_date"),
                "intended_entry_price": candidate.get("entry_price_assumption"),
                "stop_loss_price": candidate.get("stop_loss_price"),
                "max_hold_days": candidate.get("max_hold_days"),
                "strategy_signal_id": strategy_signal_id,
                "strategy_log_found": strategy_log is not None,
                "unified_signal_id": unified_signal_id,
                "aggregator_log_found": aggregator_log is not None,
                "buy_trade_id": None if buy_trade is None else buy_trade.get("trade_id"),
                "buy_executed_at": None if buy_trade is None else buy_trade.get("executed_at"),
                "buy_price": None if buy_trade is None else buy_trade.get("price"),
                "buy_quantity": None if buy_trade is None else buy_trade.get("quantity"),
                "entry_slippage_bps": _slippage_bps(fill=buy_price, intended=intended),
                "sell_trade_id": None if sell_trade is None else sell_trade.get("trade_id"),
                "sell_executed_at": None if sell_trade is None else sell_trade.get("executed_at"),
                "sell_price": None if sell_trade is None else sell_trade.get("price"),
                "position_open": position is not None,
                "position_quantity": None if position is None else position.get("quantity"),
                "position_entry_price": None if position is None else position.get("entry_price"),
                "position_current_price": None
                if position is None
                else position.get("current_price"),
                "position_unrealized_pnl": None
                if position is None
                else position.get("unrealized_pnl"),
                "position_scheduled_exit_date": None
                if position is None
                else position.get("scheduled_exit_date"),
                "reconciliation_status": _status(
                    strategy_signal_id=strategy_signal_id,
                    strategy_log=strategy_log,
                    aggregator_log=aggregator_log,
                    buy_trade=buy_trade,
                    sell_trade=sell_trade,
                    position=position,
                ),
            }
        )
    return {
        "candidate_id": payload.get("candidate_id"),
        "source_mode": payload.get("mode"),
        "summary": {
            "candidate_count": len(out_rows),
            "with_supabase": rows is not None,
            "strategy_log_count": 0 if rows is None else len(rows.strategy_logs),
            "aggregator_log_count": 0 if rows is None else len(rows.aggregator_logs),
            "trades_paper_count": 0 if rows is None else len(rows.trades_paper),
            "open_position_count": 0 if rows is None else len(rows.positions),
            "status_counts": _status_counts(out_rows),
        },
        "rows": out_rows,
    }


def _get(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def _published_signal_ids(payload: dict[str, Any]) -> list[str]:
    return [str(row["signal_id"]) for row in payload.get("published", []) if row.get("signal_id")]


def _in_filter(values: list[str]) -> str:
    return "in.(" + ",".join(values) + ")"


def _date_bounds(
    payload: dict[str, Any],
    *,
    from_date: date | None,
    to_date: date | None,
) -> tuple[datetime | None, datetime | None]:
    entry_dates = [
        date.fromisoformat(str(row["entry_date"]))
        for row in payload.get("candidates", [])
        if row.get("entry_date")
    ]
    if from_date is None and entry_dates:
        from_date = min(entry_dates)
    if to_date is None and entry_dates:
        to_date = max(entry_dates) + timedelta(days=30)
    start = (
        None if from_date is None else datetime.combine(from_date, time.min, JST).astimezone(UTC)
    )
    end = None if to_date is None else datetime.combine(to_date, time.min, JST).astimezone(UTC)
    return start, end


def _dedupe_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row[key])] = row
    return list(out.values())


def _index(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row[key]): row for row in rows if row.get(key) is not None}


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get(key) is None:
            continue
        grouped.setdefault(str(row[key]), []).append(row)
    return grouped


def _first_trade(rows: list[dict[str, Any]], *, side: str) -> dict[str, Any] | None:
    for row in sorted(rows, key=lambda item: str(item.get("executed_at") or "")):
        if row.get("side") == side:
            return row
    return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _slippage_bps(*, fill: Decimal | None, intended: Decimal | None) -> str | None:
    if fill is None or intended is None or intended <= 0:
        return None
    return str(((fill / intended) - Decimal("1")) * Decimal("10000"))


def _status(
    *,
    strategy_signal_id: Any,
    strategy_log: dict[str, Any] | None,
    aggregator_log: dict[str, Any] | None,
    buy_trade: dict[str, Any] | None,
    sell_trade: dict[str, Any] | None,
    position: dict[str, Any] | None,
) -> str:
    if strategy_signal_id is None:
        return "dry_run_only"
    if strategy_log is None:
        return "missing_strategy_log"
    if aggregator_log is None:
        return "missing_aggregator_log"
    if buy_trade is None:
        return "missing_buy_fill"
    if position is not None:
        return "open_position"
    if sell_trade is not None:
        return "closed_or_exited"
    return "no_open_position_no_sell"


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["reconciliation_status"])
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "symbol",
        "signal_date",
        "entry_date",
        "intended_entry_price",
        "stop_loss_price",
        "max_hold_days",
        "strategy_signal_id",
        "strategy_log_found",
        "unified_signal_id",
        "aggregator_log_found",
        "buy_trade_id",
        "buy_executed_at",
        "buy_price",
        "buy_quantity",
        "entry_slippage_bps",
        "sell_trade_id",
        "sell_executed_at",
        "sell_price",
        "position_open",
        "position_quantity",
        "position_entry_price",
        "position_current_price",
        "position_unrealized_pnl",
        "position_scheduled_exit_date",
        "reconciliation_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
