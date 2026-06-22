#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Report paper execution diagnostics from Supabase and local paper archives.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/report-paper-execution-diagnostics.py --date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
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
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Paper archive root. Defaults to the newest out/paper-archive-* containing the date.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-rows", type=int, default=15)
    parser.add_argument(
        "--duplicate-window-seconds",
        type=float,
        default=300.0,
        help="Window for same-symbol BUY repeat diagnostics. Defaults to 300 seconds.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
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


def _get_all(
    client: httpx.Client,
    path: str,
    params: dict[str, str],
    *,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = client.get(
            path, params=params, headers={"Range": f"{offset}-{offset + page_size - 1}"}
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"unexpected response for {path}: {payload!r}")
        rows.extend(payload)
        if len(payload) < page_size:
            return rows
        offset += page_size


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
    return _get_all(
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL: {path}:{lineno}") from exc
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _find_archive_dir(trading_date: date) -> Path | None:
    out_dir = Path("out")
    if not out_dir.exists():
        return None
    candidates: list[Path] = []
    rel = Path("orders") / "trade_mode=paper" / f"date={trading_date.isoformat()}" / "orders.jsonl"
    for path in out_dir.glob("paper-archive-*"):
        if path.is_dir() and (path / rel).exists():
            candidates.append(path)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _orders_path(archive_dir: Path, trading_date: date) -> Path:
    return (
        archive_dir
        / "orders"
        / "trade_mode=paper"
        / f"date={trading_date.isoformat()}"
        / "orders.jsonl"
    )


def _backtest_dir(archive_dir: Path, trading_date: date) -> Path:
    dated = archive_dir / f"backtest-{trading_date.isoformat()}"
    if dated.exists():
        return dated
    return archive_dir / "backtest"


def _counter(rows: list[dict[str, Any]], *fields: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        key = " / ".join(str(row.get(field, "")) for field in fields)
        counts[key] += 1
    return dict(sorted(counts.items()))


def _rate(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(numerator / denominator * 100):.1f}%"


def _as_id_set(rows: list[dict[str, Any]], field: str) -> set[str]:
    return {str(row[field]) for row in rows if row.get(field)}


def _top_symbols(
    rows: list[dict[str, Any]], *, key: str = "symbol", limit: int = 10
) -> list[dict[str, Any]]:
    counts = Counter(str(row.get(key, "")) for row in rows if row.get(key))
    return [{"symbol": symbol, "count": count} for symbol, count in counts.most_common(limit)]


def _parse_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _repeat_diagnostics(orders: list[dict[str, Any]], *, window_seconds: float) -> dict[str, Any]:
    buy_orders = [row for row in orders if row.get("side") == "BUY"]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in buy_orders:
        symbol = str(row.get("symbol") or "")
        if symbol:
            by_symbol.setdefault(symbol, []).append(row)

    duplicate_details: list[dict[str, Any]] = []
    cooldown_rejected: list[dict[str, Any]] = []
    cooldown_kept = 0
    for symbol, rows in by_symbol.items():
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                _parse_datetime(row.get("created_at")) or datetime.min.replace(tzinfo=UTC)
            ),
        )

        previous_ts: datetime | None = None
        for row in sorted_rows:
            current_ts = _parse_datetime(row.get("created_at"))
            if current_ts is None:
                continue
            if previous_ts is not None:
                delta = (current_ts - previous_ts).total_seconds()
                if 0 <= delta <= window_seconds:
                    duplicate_details.append(
                        {
                            "created_at": row.get("created_at"),
                            "symbol": symbol,
                            "seconds_since_previous": round(delta, 3),
                            "limit_price": row.get("limit_price"),
                            "signal_source": row.get("signal_source"),
                            "unified_signal_id": row.get("unified_signal_id"),
                        }
                    )
            previous_ts = current_ts

        last_kept_ts: datetime | None = None
        for row in sorted_rows:
            current_ts = _parse_datetime(row.get("created_at"))
            if current_ts is None:
                continue
            if last_kept_ts is None:
                cooldown_kept += 1
                last_kept_ts = current_ts
                continue
            delta = (current_ts - last_kept_ts).total_seconds()
            if 0 <= delta <= window_seconds:
                cooldown_rejected.append(
                    {
                        "created_at": row.get("created_at"),
                        "symbol": symbol,
                        "seconds_since_kept": round(delta, 3),
                        "limit_price": row.get("limit_price"),
                        "signal_source": row.get("signal_source"),
                        "unified_signal_id": row.get("unified_signal_id"),
                    }
                )
                continue
            cooldown_kept += 1
            last_kept_ts = current_ts

    duplicate_details.sort(key=lambda row: str(row.get("created_at") or ""))
    cooldown_rejected.sort(key=lambda row: str(row.get("created_at") or ""))
    return {
        "window_seconds": window_seconds,
        "buy_orders": len(buy_orders),
        "symbol_count": len(by_symbol),
        "top_ordered_symbols": _top_symbols(buy_orders),
        "duplicate_within_window_count": len(duplicate_details),
        "cooldown_kept_count": cooldown_kept,
        "cooldown_rejected_count": len(cooldown_rejected),
        "cooldown_rejected_rate": _rate(len(cooldown_rejected), len(buy_orders)),
        "top_cooldown_rejected_symbols": _top_symbols(cooldown_rejected),
        "duplicate_details": duplicate_details,
        "cooldown_rejected_details": cooldown_rejected,
    }


def _trade_integrity(paper_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect long-only paper trade inconsistencies from the trade ledger."""
    open_qty: Counter[str] = Counter()
    unmatched_sell_details: list[dict[str, Any]] = []
    duplicate_trade_ids = [
        signal_id
        for signal_id, count in Counter(
            str(row.get("unified_signal_id"))
            for row in paper_trades
            if row.get("unified_signal_id") is not None
        ).items()
        if count > 1
    ]

    for row in sorted(paper_trades, key=lambda item: str(item.get("executed_at") or "")):
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        quantity = int(row.get("quantity") or 0)
        if row.get("side") == "BUY":
            open_qty[symbol] += quantity
            continue
        if row.get("side") != "SELL":
            continue
        unmatched = max(0, quantity - open_qty[symbol])
        if unmatched:
            unmatched_sell_details.append(
                {
                    "executed_at": row.get("executed_at"),
                    "symbol": symbol,
                    "quantity": quantity,
                    "unmatched_quantity": unmatched,
                    "price": row.get("price"),
                    "signal_source": row.get("signal_source"),
                    "unified_signal_id": row.get("unified_signal_id"),
                }
            )
        open_qty[symbol] = max(0, open_qty[symbol] - quantity)

    nonzero_net = [
        {"symbol": symbol, "net_quantity": quantity}
        for symbol, quantity in sorted(open_qty.items())
        if quantity != 0
    ]
    return {
        "unmatched_sell_count": len(unmatched_sell_details),
        "unmatched_sell_quantity": sum(
            int(row["unmatched_quantity"]) for row in unmatched_sell_details
        ),
        "unmatched_sell_details": unmatched_sell_details,
        "nonzero_net_positions_from_trades": nonzero_net,
        "duplicate_unified_signal_ids": sorted(duplicate_trade_ids),
    }


def _build_result(
    *,
    trading_date: date,
    aggregator_rows: list[dict[str, Any]],
    paper_trades: list[dict[str, Any]],
    archive_dir: Path | None,
    orders: list[dict[str, Any]],
    replay_fills: list[dict[str, Any]],
    replay_rejected: list[dict[str, Any]],
    replay_metadata: dict[str, Any],
    duplicate_window_seconds: float,
) -> dict[str, Any]:
    aggregator_buy = [row for row in aggregator_rows if row.get("action") == "BUY"]
    aggregator_sell = [row for row in aggregator_rows if row.get("action") == "SELL"]
    archived_buy_orders = [row for row in orders if row.get("side") == "BUY"]
    archived_sell_orders = [row for row in orders if row.get("side") == "SELL"]
    paper_buy_fills = [row for row in paper_trades if row.get("side") == "BUY"]
    paper_sell_fills = [row for row in paper_trades if row.get("side") == "SELL"]

    trade_signal_ids = _as_id_set(paper_trades, "unified_signal_id")
    replay_fill_ids = _as_id_set(replay_fills, "unified_signal_id")
    replay_reject_by_id = {
        str(row["unified_signal_id"]): row
        for row in replay_rejected
        if row.get("unified_signal_id")
    }

    archived_unfilled_db = [
        row for row in orders if row.get("unified_signal_id") not in trade_signal_ids
    ]
    archived_buy_unfilled_db = [
        row for row in archived_buy_orders if row.get("unified_signal_id") not in trade_signal_ids
    ]
    db_unfilled_with_replay_fill = [
        row for row in archived_unfilled_db if row.get("unified_signal_id") in replay_fill_ids
    ]

    unfilled_details: list[dict[str, Any]] = []
    for order in archived_buy_unfilled_db:
        signal_id = str(order.get("unified_signal_id") or "")
        replay_reject = replay_reject_by_id.get(signal_id, {})
        unfilled_details.append(
            {
                "created_at": order.get("created_at"),
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "quantity": order.get("quantity"),
                "limit_price": order.get("limit_price"),
                "signal_source": order.get("signal_source"),
                "unified_signal_id": signal_id or None,
                "replay_outcome": "filled" if signal_id in replay_fill_ids else "no_fill",
                "replay_no_fill_reason": replay_reject.get("reason"),
            }
        )

    result = {
        "date_jst": trading_date.isoformat(),
        "archive_dir": str(archive_dir) if archive_dir is not None else None,
        "db": {
            "aggregator_total": len(aggregator_rows),
            "aggregator_buy": len(aggregator_buy),
            "aggregator_sell": len(aggregator_sell),
            "paper_trades_total": len(paper_trades),
            "paper_buy_fills": len(paper_buy_fills),
            "paper_sell_fills": len(paper_sell_fills),
            "paper_buy_fill_per_aggregator_buy": _rate(len(paper_buy_fills), len(aggregator_buy)),
            "aggregator_by_source_action": _counter(aggregator_rows, "signal_source", "action"),
            "paper_trades_by_source_side": _counter(paper_trades, "signal_source", "side"),
        },
        "archive": {
            "orders_total": len(orders),
            "buy_orders": len(archived_buy_orders),
            "sell_orders": len(archived_sell_orders),
            "order_rate_per_aggregator_buy": _rate(len(archived_buy_orders), len(aggregator_buy)),
            "orders_by_source_side": _counter(orders, "signal_source", "side"),
        },
        "replay": {
            "available": bool(replay_metadata or replay_fills or replay_rejected),
            "order_count": replay_metadata.get("order_count", len(orders)),
            "fill_count": replay_metadata.get("fill_count", len(replay_fills)),
            "no_fill_count": replay_metadata.get("no_fill_count", len(replay_rejected)),
            "fill_rate": _rate(
                int(replay_metadata.get("fill_count", len(replay_fills)) or 0),
                int(replay_metadata.get("order_count", len(orders)) or 0),
            ),
            "no_fill_by_reason": _counter(replay_rejected, "reason"),
        },
        "gaps": {
            "archived_orders_without_db_fill": len(archived_unfilled_db),
            "archived_buy_orders_without_db_fill": len(archived_buy_unfilled_db),
            "db_unfilled_orders_that_replay_filled": len(db_unfilled_with_replay_fill),
            "top_unfilled_db_symbols": _top_symbols(archived_buy_unfilled_db),
            "top_replay_no_fill_symbols": _top_symbols(replay_rejected),
            "unfilled_buy_order_details": unfilled_details,
        },
        "integrity": _trade_integrity(paper_trades),
        "repeats": _repeat_diagnostics(
            archived_buy_orders,
            window_seconds=duplicate_window_seconds,
        ),
    }
    return result


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    if not counts:
        print("  none")
        return
    for key, count in counts.items():
        print(f"  {key}: {count}")


def _print_result(result: dict[str, Any], *, max_rows: int) -> None:
    db = result["db"]
    archive = result["archive"]
    replay = result["replay"]
    gaps = result["gaps"]
    integrity = result["integrity"]
    repeats = result["repeats"]

    print(f"== Paper execution diagnostics {result['date_jst']} JST ==")
    print(f"archive_dir={result['archive_dir'] or 'none'}")
    print()
    print("== Funnel ==")
    print(f"aggregator_buy={db['aggregator_buy']}")
    print(
        "archived_buy_orders="
        f"{archive['buy_orders']} ({archive['order_rate_per_aggregator_buy']} of aggregator BUY)"
    )
    print(
        "db_paper_buy_fills="
        f"{db['paper_buy_fills']} ({db['paper_buy_fill_per_aggregator_buy']} of aggregator BUY)"
    )
    print(f"archive_replay_fills={replay['fill_count']} ({replay['fill_rate']} of archived orders)")
    print(f"archived_buy_orders_without_db_fill={gaps['archived_buy_orders_without_db_fill']}")
    print(f"db_unfilled_orders_that_replay_filled={gaps['db_unfilled_orders_that_replay_filled']}")
    print()

    _print_counts("aggregator by source/action", db["aggregator_by_source_action"])
    _print_counts("archived orders by source/side", archive["orders_by_source_side"])
    _print_counts("db paper trades by source/side", db["paper_trades_by_source_side"])
    _print_counts("archive replay no_fill by reason", replay["no_fill_by_reason"])
    print()

    print("trade ledger integrity")
    print(
        f"  unmatched_sell_count={integrity['unmatched_sell_count']} "
        f"unmatched_sell_quantity={integrity['unmatched_sell_quantity']}"
    )
    if integrity["nonzero_net_positions_from_trades"]:
        print("  nonzero net quantities from trades")
        for row in integrity["nonzero_net_positions_from_trades"][:max_rows]:
            print(f"    {row['symbol']}: {row['net_quantity']}")
    if integrity["duplicate_unified_signal_ids"]:
        print("  duplicate unified_signal_id trades")
        for signal_id in integrity["duplicate_unified_signal_ids"][:max_rows]:
            print(f"    {signal_id}")
    if integrity["unmatched_sell_details"]:
        print("  unmatched SELL details")
        for row in integrity["unmatched_sell_details"][:max_rows]:
            print(
                "    "
                f"{row['executed_at']} {row['symbol']} "
                f"qty={row['quantity']} unmatched={row['unmatched_quantity']} "
                f"price={row['price']} source={row['signal_source']} "
                f"signal_id={row['unified_signal_id']}"
            )
    print()

    print(f"same-symbol BUY repeats (window={repeats['window_seconds']}s)")
    print(f"  buy_orders={repeats['buy_orders']} symbols={repeats['symbol_count']}")
    print(f"  duplicate_within_window={repeats['duplicate_within_window_count']}")
    print(
        "  cooldown_simulation="
        f"kept={repeats['cooldown_kept_count']} "
        f"rejected={repeats['cooldown_rejected_count']} "
        f"({repeats['cooldown_rejected_rate']} of BUY orders)"
    )
    print("  top ordered symbols")
    if not repeats["top_ordered_symbols"]:
        print("    none")
    else:
        for row in repeats["top_ordered_symbols"][:max_rows]:
            print(f"    {row['symbol']}: {row['count']}")
    print("  top cooldown-rejected symbols")
    if not repeats["top_cooldown_rejected_symbols"]:
        print("    none")
    else:
        for row in repeats["top_cooldown_rejected_symbols"][:max_rows]:
            print(f"    {row['symbol']}: {row['count']}")
    print()

    print("top DB-unfilled BUY symbols")
    if not gaps["top_unfilled_db_symbols"]:
        print("  none")
    else:
        for row in gaps["top_unfilled_db_symbols"]:
            print(f"  {row['symbol']}: {row['count']}")
    print()

    print("DB-unfilled BUY order details")
    details = gaps["unfilled_buy_order_details"]
    if not details:
        print("  none")
    else:
        for row in details[:max_rows]:
            print(
                "  "
                f"{row['created_at']} {row['symbol']} qty={row['quantity']} "
                f"limit={row['limit_price']} source={row['signal_source']} "
                f"replay={row['replay_outcome']}"
                + (
                    f" reason={row['replay_no_fill_reason']}"
                    if row.get("replay_no_fill_reason")
                    else ""
                )
            )
        if len(details) > max_rows:
            print(f"  ... {len(details) - max_rows} more")


def main() -> int:
    args = parse_args()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY missing", file=sys.stderr)
        return 2

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
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

    archive_dir = args.archive_dir or _find_archive_dir(args.date)
    orders: list[dict[str, Any]] = []
    replay_fills: list[dict[str, Any]] = []
    replay_rejected: list[dict[str, Any]] = []
    replay_metadata: dict[str, Any] = {}
    if archive_dir is not None:
        orders = _read_jsonl(_orders_path(archive_dir, args.date))
        bt_dir = _backtest_dir(archive_dir, args.date)
        replay_fills = _read_jsonl(bt_dir / "fills.jsonl")
        replay_rejected = _read_jsonl(bt_dir / "rejected.jsonl")
        metadata_path = bt_dir / "metadata.json"
        if metadata_path.exists():
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                replay_metadata = loaded

    result = _build_result(
        trading_date=args.date,
        aggregator_rows=aggregator_rows,
        paper_trades=paper_trades,
        archive_dir=archive_dir,
        orders=orders,
        replay_fills=replay_fills,
        replay_rejected=replay_rejected,
        replay_metadata=replay_metadata,
        duplicate_window_seconds=args.duplicate_window_seconds,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _print_result(result, max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
