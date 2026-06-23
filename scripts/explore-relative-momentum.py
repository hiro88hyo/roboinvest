#!/usr/bin/env python3
"""Explore intraday relative momentum candidates from archived features.

This diagnostic uses the archived watchlist itself as the peer universe. It
does not have TOPIX or sector baselines, so it is only a first-pass proxy:
symbols must rank near the top by return from open, trade above VWAP, and make
an intraday high with acceptable execution quality.
"""

from __future__ import annotations

import argparse
import csv
import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median


@dataclass(frozen=True, slots=True)
class Row:
    symbol: str
    trading_date: date
    timestamp: datetime
    price: float
    vwap: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    book_imbalance_5: float | None
    minutes_from_open: int | None
    minutes_to_close: int | None


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    trading_date: date
    timestamp: datetime
    price: float
    open_price: float
    return_from_open_bps: float
    peer_percentile: float
    vwap: float
    vwap_distance_bps: float
    session_high: float
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    ret_15_bps: float | None
    ret_30_bps: float | None


@dataclass(frozen=True, slots=True)
class Params:
    entry_minute: int
    min_minutes_to_close: int
    min_return_from_open_bps: float
    min_peer_percentile: float
    min_vwap_distance_bps: float
    max_spread_bps: float | None
    max_spread_ticks: float | None
    min_ask_depth_5: int | None
    min_book_imbalance_5: float | None
    one_per_symbol_day: bool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count relative momentum candidates from ProcessedFeatures JSONL.",
    )
    parser.add_argument("--features", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=None, help="Optional candidate CSV output.")
    parser.add_argument("--entry-minute", type=int, default=15)
    parser.add_argument("--min-minutes-to-close", type=int, default=45)
    parser.add_argument("--min-return-from-open-bps", type=float, default=100.0)
    parser.add_argument("--min-peer-percentile", type=float, default=0.80)
    parser.add_argument("--min-vwap-distance-bps", type=float, default=20.0)
    parser.add_argument("--max-spread-bps", type=float, default=None)
    parser.add_argument("--max-spread-ticks", type=float, default=None)
    parser.add_argument("--min-ask-depth-5", type=int, default=None)
    parser.add_argument("--min-book-imbalance-5", type=float, default=None)
    parser.add_argument("--allow-multiple-per-day", action="store_true")
    args = parser.parse_args()

    params = Params(
        entry_minute=args.entry_minute,
        min_minutes_to_close=args.min_minutes_to_close,
        min_return_from_open_bps=args.min_return_from_open_bps,
        min_peer_percentile=args.min_peer_percentile,
        min_vwap_distance_bps=args.min_vwap_distance_bps,
        max_spread_bps=args.max_spread_bps,
        max_spread_ticks=args.max_spread_ticks,
        min_ask_depth_5=args.min_ask_depth_5,
        min_book_imbalance_5=args.min_book_imbalance_5,
        one_per_symbol_day=not args.allow_multiple_per_day,
    )
    rows = read_rows(args.features)
    open_prices = build_open_prices(rows)
    peer_percentiles = build_peer_percentiles(rows, open_prices)
    candidates = find_candidates(rows, open_prices, peer_percentiles, params)
    if args.output is not None:
        write_candidates(candidates, args.output)
    print_summary(rows, candidates, params, args.output)
    print_stage_diagnostics(rows, open_prices, peer_percentiles, params)
    return 0


def read_rows(paths: list[Path]) -> list[Row]:
    rows: list[Row] = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    timestamp = datetime.fromisoformat(raw["timestamp"])
                    rows.append(
                        Row(
                            symbol=str(raw["symbol"]),
                            trading_date=timestamp.date(),
                            timestamp=timestamp,
                            price=float(raw["price"]),
                            vwap=to_float(raw.get("vwap")),
                            spread_bps=to_float(raw.get("spread_bps")),
                            spread_ticks=to_float(raw.get("spread_ticks")),
                            ask_depth_5=to_int(raw.get("ask_depth_5")),
                            book_imbalance_5=to_float(raw.get("book_imbalance_5")),
                            minutes_from_open=to_int(raw.get("minutes_from_open")),
                            minutes_to_close=to_int(raw.get("minutes_to_close")),
                        )
                    )
                except Exception as exc:
                    msg = f"invalid feature row: path={path} line={line_no}"
                    raise ValueError(msg) from exc
    return sorted(rows, key=lambda row: (row.trading_date, row.symbol, row.timestamp))


def build_open_prices(rows: list[Row]) -> dict[tuple[date, str], float]:
    out: dict[tuple[date, str], float] = {}
    for row in rows:
        out.setdefault((row.trading_date, row.symbol), row.price)
    return out


def build_peer_percentiles(
    rows: list[Row],
    open_prices: dict[tuple[date, str], float],
) -> dict[tuple[date, int, str], float]:
    by_minute: dict[tuple[date, int], dict[str, float]] = {}
    for row in rows:
        if row.minutes_from_open is None:
            continue
        open_price = open_prices.get((row.trading_date, row.symbol))
        if open_price is None or open_price <= 0:
            continue
        ret_bps = ((row.price - open_price) / open_price) * 10000.0
        by_minute.setdefault((row.trading_date, row.minutes_from_open), {})[row.symbol] = ret_bps

    out: dict[tuple[date, int, str], float] = {}
    for (trading_date, minute), values in by_minute.items():
        ordered = sorted(values.items(), key=lambda item: item[1])
        denom = max(1, len(ordered) - 1)
        for idx, (symbol, _ret_bps) in enumerate(ordered):
            out[(trading_date, minute, symbol)] = idx / denom
    return out


def find_candidates(
    rows: list[Row],
    open_prices: dict[tuple[date, str], float],
    peer_percentiles: dict[tuple[date, int, str], float],
    params: Params,
) -> list[Candidate]:
    by_key: dict[tuple[date, str], list[Row]] = {}
    for row in rows:
        by_key.setdefault((row.trading_date, row.symbol), []).append(row)

    out: list[Candidate] = []
    for key, symbol_rows in by_key.items():
        open_price = open_prices.get(key)
        if open_price is None or open_price <= 0:
            continue
        ordered = sorted(symbol_rows, key=lambda item: item.timestamp)
        times = [row.timestamp for row in ordered]
        prices = [row.price for row in ordered]
        session_high = ordered[0].price
        previous_high = session_high
        for row in ordered:
            previous_high = session_high
            session_high = max(session_high, row.price)
            if not passes_time(row, params) or row.vwap is None:
                continue
            assert row.minutes_from_open is not None
            ret_open_bps = ((row.price - open_price) / open_price) * 10000.0
            peer_percentile = peer_percentiles.get(
                (row.trading_date, row.minutes_from_open, row.symbol)
            )
            if peer_percentile is None:
                continue
            vwap_distance_bps = ((row.price - row.vwap) / row.vwap) * 10000.0
            if not (
                ret_open_bps >= params.min_return_from_open_bps
                and peer_percentile >= params.min_peer_percentile
                and vwap_distance_bps >= params.min_vwap_distance_bps
                and row.price >= session_high
                and row.price > previous_high
                and passes_execution(row, params)
            ):
                continue
            out.append(
                Candidate(
                    symbol=row.symbol,
                    trading_date=row.trading_date,
                    timestamp=row.timestamp,
                    price=row.price,
                    open_price=open_price,
                    return_from_open_bps=ret_open_bps,
                    peer_percentile=peer_percentile,
                    vwap=row.vwap,
                    vwap_distance_bps=vwap_distance_bps,
                    session_high=session_high,
                    spread_bps=row.spread_bps,
                    spread_ticks=row.spread_ticks,
                    ask_depth_5=row.ask_depth_5,
                    ret_15_bps=forward_return_bps(row, times, prices, 15),
                    ret_30_bps=forward_return_bps(row, times, prices, 30),
                )
            )
            if params.one_per_symbol_day:
                break
    return sorted(out, key=lambda item: (item.timestamp, item.symbol))


def passes_time(row: Row, params: Params) -> bool:
    return not (
        row.minutes_from_open is None
        or row.minutes_from_open < params.entry_minute
        or row.minutes_to_close is None
        or row.minutes_to_close < params.min_minutes_to_close
    )


def passes_execution(row: Row, params: Params) -> bool:
    if params.max_spread_bps is not None and (
        row.spread_bps is None or row.spread_bps > params.max_spread_bps
    ):
        return False
    if params.max_spread_ticks is not None and (
        row.spread_ticks is None or row.spread_ticks > params.max_spread_ticks
    ):
        return False
    if params.min_ask_depth_5 is not None and (
        row.ask_depth_5 is None or row.ask_depth_5 < params.min_ask_depth_5
    ):
        return False
    return not (
        params.min_book_imbalance_5 is not None
        and (row.book_imbalance_5 is None or row.book_imbalance_5 < params.min_book_imbalance_5)
    )


def forward_return_bps(
    row: Row,
    times: list[datetime],
    prices: list[float],
    minutes: int,
) -> float | None:
    target = row.timestamp + timedelta(minutes=minutes)
    idx = bisect_left(times, target)
    if idx >= len(prices):
        return None
    return ((prices[idx] - row.price) / row.price) * 10000.0


def print_summary(
    rows: list[Row],
    candidates: list[Candidate],
    params: Params,
    output: Path | None,
) -> None:
    print(f"rows={len(rows)}")
    print(f"candidates={len(candidates)}")
    print(f"params={params}")
    if output is not None:
        print(f"output={output}")
    print_return_stats("ret_15_bps", [item.ret_15_bps for item in candidates])
    print_return_stats("ret_30_bps", [item.ret_30_bps for item in candidates])
    print_value_stats("return_from_open_bps", [item.return_from_open_bps for item in candidates])
    print_value_stats("peer_percentile", [item.peer_percentile for item in candidates])
    print_value_stats("vwap_distance_bps", [item.vwap_distance_bps for item in candidates])
    print_value_stats("spread_bps", [item.spread_bps for item in candidates])
    print_value_stats("spread_ticks", [item.spread_ticks for item in candidates])


def print_stage_diagnostics(
    rows: list[Row],
    open_prices: dict[tuple[date, str], float],
    peer_percentiles: dict[tuple[date, int, str], float],
    params: Params,
) -> None:
    timed = [row for row in rows if passes_time(row, params)]
    with_vwap = [row for row in timed if row.vwap is not None]
    momentum: list[Row] = []
    peer_pass: list[Row] = []
    for row in with_vwap:
        assert row.minutes_from_open is not None
        open_price = open_prices.get((row.trading_date, row.symbol))
        if open_price is None or open_price <= 0:
            continue
        ret_open_bps = ((row.price - open_price) / open_price) * 10000.0
        if ret_open_bps >= params.min_return_from_open_bps:
            momentum.append(row)
        percentile = peer_percentiles.get((row.trading_date, row.minutes_from_open, row.symbol))
        if percentile is not None and percentile >= params.min_peer_percentile:
            peer_pass.append(row)
    execution = [row for row in with_vwap if passes_execution(row, params)]
    print("stage diagnostics:")
    print(f"  timed_rows={len(timed)}")
    print(f"  with_vwap_rows={len(with_vwap)}")
    print(f"  momentum_rows={len(momentum)}")
    print(f"  peer_percentile_pass_rows={len(peer_pass)}")
    print(f"  execution_pass_rows={len(execution)}")


def print_return_stats(label: str, values: list[float | None]) -> None:
    clean = [value for value in values if value is not None]
    if not clean:
        print(f"{label}: n=0")
        return
    positives = sum(1 for value in clean if value > 0)
    print(
        f"{label}: n={len(clean)} avg={mean(clean):.3f} "
        f"median={median(clean):.3f} positive_rate={positives / len(clean):.3f}"
    )


def print_value_stats(label: str, values: list[float | None]) -> None:
    clean = [value for value in values if value is not None]
    if not clean:
        print(f"{label}: n=0")
        return
    print(
        f"{label}: n={len(clean)} avg={mean(clean):.3f} "
        f"median={median(clean):.3f} min={min(clean):.3f} max={max(clean):.3f}"
    )


def write_candidates(candidates: list[Candidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "symbol",
                "trading_date",
                "timestamp",
                "price",
                "open_price",
                "return_from_open_bps",
                "peer_percentile",
                "vwap",
                "vwap_distance_bps",
                "session_high",
                "spread_bps",
                "spread_ticks",
                "ask_depth_5",
                "ret_15_bps",
                "ret_30_bps",
            ],
        )
        writer.writeheader()
        for item in candidates:
            writer.writerow(
                {
                    "symbol": item.symbol,
                    "trading_date": item.trading_date.isoformat(),
                    "timestamp": item.timestamp.isoformat(),
                    "price": item.price,
                    "open_price": item.open_price,
                    "return_from_open_bps": item.return_from_open_bps,
                    "peer_percentile": item.peer_percentile,
                    "vwap": item.vwap,
                    "vwap_distance_bps": item.vwap_distance_bps,
                    "session_high": item.session_high,
                    "spread_bps": item.spread_bps,
                    "spread_ticks": item.spread_ticks,
                    "ask_depth_5": item.ask_depth_5,
                    "ret_15_bps": item.ret_15_bps,
                    "ret_30_bps": item.ret_30_bps,
                }
            )


def to_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def to_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
