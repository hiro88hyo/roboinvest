#!/usr/bin/env python3
"""Explore BUY limit price policies using archived paper orders and order books."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from oms_paper.backtest import iter_order_books, iter_order_requests
from oms_paper.fill_simulator import simulate_fill
from trade_contracts.enums import OrderType, Side
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest
from trade_contracts.tick_size import tse_tick_size

PolicyKind = Literal["original", "original_plus_ticks", "best_ask_plus_ticks", "market"]


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    kind: PolicyKind
    ticks: int = 0


@dataclass(slots=True)
class PolicyStats:
    policy: Policy
    orders: int = 0
    fills: int = 0
    partials: int = 0
    no_fills: int = 0
    total_quantity: int = 0
    filled_quantity: int = 0
    original_notional: Decimal = Decimal("0")
    fill_notional: Decimal = Decimal("0")
    limit_notional: Decimal = Decimal("0")
    fill_vs_original_bps_sum: Decimal = Decimal("0")
    limit_vs_original_bps_sum: Decimal = Decimal("0")
    limit_comparison_count: int = 0
    max_fill_vs_original_bps: Decimal | None = None
    max_limit_vs_original_bps: Decimal | None = None
    no_fill_reasons: Counter[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.no_fill_reasons is None:
            self.no_fill_reasons = Counter()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--orders-dir", type=Path, default=None)
    parser.add_argument("--book-jsonl", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--policies",
        default="original,orig+1t,orig+2t,orig+3t,orig+5t,orig+10t,ask,ask+1t,ask+2t,market",
        help="Comma-separated policies: original, orig+Nt, ask, ask+Nt, market.",
    )
    return parser.parse_args()


def _find_archive_dir(target_date: date) -> Path | None:
    out_dir = Path("out")
    if not out_dir.exists():
        return None
    rel = Path("orders") / "trade_mode=paper" / f"date={target_date.isoformat()}" / "orders.jsonl"
    candidates = [
        path for path in out_dir.glob("paper-archive-*") if path.is_dir() and (path / rel).exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _orders_path(*, archive_dir: Path | None, orders_dir: Path | None, target_date: date) -> Path:
    root = orders_dir if orders_dir is not None else archive_dir / "orders" if archive_dir else None
    if root is None:
        raise SystemExit("orders path not found; pass --archive-dir or --orders-dir")
    return root / "trade_mode=paper" / f"date={target_date.isoformat()}" / "orders.jsonl"


def _book_jsonl_path(
    *, archive_dir: Path | None, book_jsonl: Path | None, target_date: date
) -> Path:
    if book_jsonl is not None:
        return book_jsonl
    if archive_dir is None:
        raise SystemExit("book jsonl not found; pass --archive-dir or --book-jsonl")
    dated = archive_dir / f"backtest-{target_date.isoformat()}" / "books.jsonl"
    if dated.exists():
        return dated
    return archive_dir / "backtest" / "books.jsonl"


def _parse_policy(raw: str) -> Policy:
    value = raw.strip().lower()
    if value == "original":
        return Policy(name="original", kind="original")
    if value == "market":
        return Policy(name="market", kind="market")
    if value == "ask":
        return Policy(name="ask", kind="best_ask_plus_ticks", ticks=0)
    if value.startswith("ask+") and value.endswith("t"):
        return Policy(name=value, kind="best_ask_plus_ticks", ticks=int(value[4:-1]))
    if value.startswith("orig+") and value.endswith("t"):
        return Policy(name=value, kind="original_plus_ticks", ticks=int(value[5:-1]))
    raise argparse.ArgumentTypeError(f"unknown policy: {raw}")


def _parse_policies(raw: str) -> list[Policy]:
    return [_parse_policy(part) for part in raw.split(",") if part.strip()]


def _merge_events(
    orders: list[OrderRequest],
    books: list[OrderBookSnapshot],
) -> list[tuple[object, str]]:
    events: list[tuple[object, str, object]] = []
    for book in books:
        events.append((book.timestamp, "book", book))
    for order in orders:
        events.append((order.created_at, "order", order))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "book" else 1))
    return [(payload, kind) for _, kind, payload in events]


def _tick_for_price(price: Decimal) -> Decimal:
    return tse_tick_size(price)


def _adjust_order(
    order: OrderRequest, *, policy: Policy, book: OrderBookSnapshot | None
) -> OrderRequest:
    if order.side is not Side.BUY:
        return order
    if policy.kind == "original":
        return order
    if policy.kind == "market":
        return order.model_copy(update={"order_type": OrderType.MARKET, "limit_price": None})
    if policy.kind == "original_plus_ticks":
        if order.limit_price is None:
            return order
        tick = _tick_for_price(order.limit_price)
        limit_price = order.limit_price + (tick * Decimal(policy.ticks))
        return order.model_copy(update={"order_type": OrderType.LIMIT, "limit_price": limit_price})
    if policy.kind == "best_ask_plus_ticks":
        if book is None or not book.asks:
            return order.model_copy(update={"order_type": OrderType.LIMIT, "limit_price": None})
        best_ask = book.asks[0].price
        tick = _tick_for_price(best_ask)
        limit_price = best_ask + (tick * Decimal(policy.ticks))
        return order.model_copy(update={"order_type": OrderType.LIMIT, "limit_price": limit_price})
    raise AssertionError(f"unknown policy kind: {policy.kind}")


def _bps(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return (numerator / denominator) * Decimal("10000")


def _record_result(
    stats: PolicyStats,
    *,
    original_order: OrderRequest,
    adjusted_order: OrderRequest,
    fill_reason: str,
    filled_quantity: int,
    fill_price: Decimal | None,
) -> None:
    stats.orders += 1
    stats.total_quantity += original_order.quantity
    original_limit = original_order.limit_price
    if original_limit is not None:
        stats.original_notional += original_limit * Decimal(original_order.quantity)
    if adjusted_order.limit_price is not None:
        stats.limit_notional += adjusted_order.limit_price * Decimal(original_order.quantity)
        if original_limit is not None:
            limit_delta_bps = _bps(adjusted_order.limit_price - original_limit, original_limit)
            if limit_delta_bps is not None:
                stats.limit_comparison_count += 1
                stats.limit_vs_original_bps_sum += limit_delta_bps
                if (
                    stats.max_limit_vs_original_bps is None
                    or limit_delta_bps > stats.max_limit_vs_original_bps
                ):
                    stats.max_limit_vs_original_bps = limit_delta_bps

    if filled_quantity <= 0 or fill_price is None:
        stats.no_fills += 1
        stats.no_fill_reasons[fill_reason] += 1
        return

    stats.fills += 1
    stats.filled_quantity += filled_quantity
    if filled_quantity < original_order.quantity:
        stats.partials += 1
    stats.fill_notional += fill_price * Decimal(filled_quantity)
    if original_limit is not None:
        fill_delta_bps = _bps(fill_price - original_limit, original_limit)
        if fill_delta_bps is not None:
            stats.fill_vs_original_bps_sum += fill_delta_bps
            if (
                stats.max_fill_vs_original_bps is None
                or fill_delta_bps > stats.max_fill_vs_original_bps
            ):
                stats.max_fill_vs_original_bps = fill_delta_bps


def _quant(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def _stats_to_row(stats: PolicyStats) -> dict[str, object]:
    fill_rate = Decimal(stats.fills) / Decimal(stats.orders) if stats.orders else Decimal("0")
    fill_qty_rate = (
        Decimal(stats.filled_quantity) / Decimal(stats.total_quantity)
        if stats.total_quantity
        else Decimal("0")
    )
    avg_fill_px = (
        stats.fill_notional / Decimal(stats.filled_quantity) if stats.filled_quantity else None
    )
    avg_limit_px = (
        stats.limit_notional / Decimal(stats.total_quantity)
        if stats.total_quantity and stats.limit_notional > 0
        else None
    )
    avg_fill_bps = stats.fill_vs_original_bps_sum / Decimal(stats.fills) if stats.fills else None
    avg_limit_bps = (
        stats.limit_vs_original_bps_sum / Decimal(stats.limit_comparison_count)
        if stats.limit_comparison_count
        else None
    )
    return {
        "policy": stats.policy.name,
        "orders": stats.orders,
        "fills": stats.fills,
        "partials": stats.partials,
        "no_fills": stats.no_fills,
        "fill_rate_pct": _quant(fill_rate * Decimal("100")),
        "fill_quantity_rate_pct": _quant(fill_qty_rate * Decimal("100")),
        "avg_fill_price": _quant(avg_fill_px),
        "avg_limit_price": _quant(avg_limit_px),
        "avg_fill_vs_original_bps": _quant(avg_fill_bps),
        "max_fill_vs_original_bps": _quant(stats.max_fill_vs_original_bps),
        "avg_limit_vs_original_bps": _quant(avg_limit_bps),
        "max_limit_vs_original_bps": _quant(stats.max_limit_vs_original_bps),
        "no_fill_reasons": dict(sorted(stats.no_fill_reasons.items())),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "policy",
        "orders",
        "fills",
        "partials",
        "no_fills",
        "fill_rate_pct",
        "fill_quantity_rate_pct",
        "avg_fill_price",
        "avg_limit_price",
        "avg_fill_vs_original_bps",
        "max_fill_vs_original_bps",
        "avg_limit_vs_original_bps",
        "max_limit_vs_original_bps",
        "no_fill_reasons",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            encoded = dict(row)
            encoded["no_fill_reasons"] = json.dumps(row["no_fill_reasons"], sort_keys=True)
            writer.writerow(encoded)


def _print_rows(rows: list[dict[str, object]]) -> None:
    header = (
        "policy fills/orders fill_rate avg_fill_vs_orig_bps "
        "max_fill_vs_orig_bps avg_limit_vs_orig_bps no_fill_reasons"
    )
    print(header)
    for row in rows:
        print(
            f"{row['policy']} {row['fills']}/{row['orders']} "
            f"{row['fill_rate_pct']}% "
            f"{row['avg_fill_vs_original_bps']} "
            f"{row['max_fill_vs_original_bps']} "
            f"{row['avg_limit_vs_original_bps']} "
            f"{row['no_fill_reasons']}"
        )


def main() -> int:
    args = parse_args()
    archive_dir = args.archive_dir or _find_archive_dir(args.date)
    orders_path = _orders_path(
        archive_dir=archive_dir, orders_dir=args.orders_dir, target_date=args.date
    )
    books_path = _book_jsonl_path(
        archive_dir=archive_dir, book_jsonl=args.book_jsonl, target_date=args.date
    )
    if not orders_path.exists():
        raise SystemExit(f"orders archive not found: {orders_path}")
    if not books_path.exists():
        raise SystemExit(f"book jsonl not found: {books_path}")

    policies = _parse_policies(args.policies)
    stats_by_policy = {policy.name: PolicyStats(policy=policy) for policy in policies}
    orders = list(iter_order_requests(orders_path))
    books = list(iter_order_books(books_path))
    book_cache: dict[str, OrderBookSnapshot] = {}

    for payload, kind in _merge_events(orders, books):
        if kind == "book":
            assert isinstance(payload, OrderBookSnapshot)
            book_cache[payload.symbol] = payload
            continue
        assert isinstance(payload, OrderRequest)
        order = payload
        book = book_cache.get(order.symbol)
        for policy in policies:
            adjusted = _adjust_order(order, policy=policy, book=book)
            if book is None:
                _record_result(
                    stats_by_policy[policy.name],
                    original_order=order,
                    adjusted_order=adjusted,
                    fill_reason="no_book",
                    filled_quantity=0,
                    fill_price=None,
                )
                continue
            fill = simulate_fill(order=adjusted, book=book)
            _record_result(
                stats_by_policy[policy.name],
                original_order=order,
                adjusted_order=adjusted,
                fill_reason=fill.reason,
                filled_quantity=fill.filled_quantity,
                fill_price=fill.fill_price,
            )

    rows = [_stats_to_row(stats_by_policy[policy.name]) for policy in policies]
    result = {
        "date": args.date.isoformat(),
        "archive_dir": str(archive_dir) if archive_dir is not None else None,
        "orders_path": str(orders_path),
        "books_path": str(books_path),
        "rows": rows,
    }
    if args.output_csv is not None:
        _write_csv(args.output_csv, rows)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"date={args.date.isoformat()}")
        print(f"archive_dir={result['archive_dir']}")
        print(f"orders={len(orders)} books={len(books)}")
        _print_rows(rows)
        if args.output_csv is not None:
            print(f"csv={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
