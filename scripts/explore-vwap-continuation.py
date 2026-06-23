#!/usr/bin/env python3
"""Explore VWAP continuation candidates from archived ProcessedFeatures.

This is a diagnostic counter, not a trading backtest. It looks for symbols that
are already trading above VWAP, pull back near VWAP without breaking down, and
then reclaim upward momentum.
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
    sma_short: float | None
    sma_long: float | None
    volume_ratio: float | None
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
    vwap: float
    pullback_low: float
    pullback_high: float
    stop_price: float
    risk_bps: float
    vwap_distance_bps: float
    volume_ratio: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    ret_15_bps: float | None
    ret_30_bps: float | None


@dataclass(frozen=True, slots=True)
class Params:
    entry_minute: int
    min_minutes_to_close: int
    trend_min_bps: float
    pullback_above_vwap_bps: float
    max_below_vwap_bps: float
    reclaim_above_vwap_bps: float
    max_risk_bps: float
    max_spread_bps: float | None
    max_spread_ticks: float | None
    min_ask_depth_5: int | None
    min_book_imbalance_5: float | None
    require_sma_uptrend: bool
    one_per_symbol_day: bool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count VWAP continuation candidates from ProcessedFeatures JSONL.",
    )
    parser.add_argument("--features", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=None, help="Optional candidate CSV output.")
    parser.add_argument("--entry-minute", type=int, default=15)
    parser.add_argument("--min-minutes-to-close", type=int, default=45)
    parser.add_argument("--trend-min-bps", type=float, default=50.0)
    parser.add_argument("--pullback-above-vwap-bps", type=float, default=35.0)
    parser.add_argument("--max-below-vwap-bps", type=float, default=10.0)
    parser.add_argument("--reclaim-above-vwap-bps", type=float, default=40.0)
    parser.add_argument("--max-risk-bps", type=float, default=200.0)
    parser.add_argument("--max-spread-bps", type=float, default=None)
    parser.add_argument("--max-spread-ticks", type=float, default=None)
    parser.add_argument("--min-ask-depth-5", type=int, default=None)
    parser.add_argument("--min-book-imbalance-5", type=float, default=None)
    parser.add_argument("--require-sma-uptrend", action="store_true")
    parser.add_argument("--allow-multiple-per-day", action="store_true")
    args = parser.parse_args()

    params = Params(
        entry_minute=args.entry_minute,
        min_minutes_to_close=args.min_minutes_to_close,
        trend_min_bps=args.trend_min_bps,
        pullback_above_vwap_bps=args.pullback_above_vwap_bps,
        max_below_vwap_bps=args.max_below_vwap_bps,
        reclaim_above_vwap_bps=args.reclaim_above_vwap_bps,
        max_risk_bps=args.max_risk_bps,
        max_spread_bps=args.max_spread_bps,
        max_spread_ticks=args.max_spread_ticks,
        min_ask_depth_5=args.min_ask_depth_5,
        min_book_imbalance_5=args.min_book_imbalance_5,
        require_sma_uptrend=args.require_sma_uptrend,
        one_per_symbol_day=not args.allow_multiple_per_day,
    )
    rows = read_rows(args.features)
    candidates = find_candidates(rows, params)
    if args.output is not None:
        write_candidates(candidates, args.output)
    print_summary(rows, candidates, params, args.output)
    print_stage_diagnostics(rows, params)
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
                            sma_short=to_float(raw.get("sma_short")),
                            sma_long=to_float(raw.get("sma_long")),
                            volume_ratio=to_float(raw.get("volume_ratio")),
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


def find_candidates(rows: list[Row], params: Params) -> list[Candidate]:
    by_key: dict[tuple[date, str], list[Row]] = {}
    for row in rows:
        by_key.setdefault((row.trading_date, row.symbol), []).append(row)

    out: list[Candidate] = []
    for (_trading_date, _symbol), symbol_rows in by_key.items():
        ordered = sorted(symbol_rows, key=lambda item: item.timestamp)
        times = [row.timestamp for row in ordered]
        prices = [row.price for row in ordered]
        trend_seen = False
        in_pullback = False
        pullback_low: float | None = None
        pullback_high: float | None = None
        previous: Row | None = None

        for row in ordered:
            if not passes_time(row, params) or row.vwap is None:
                previous = row
                continue
            distance_bps = ((row.price - row.vwap) / row.vwap) * 10000.0
            if passes_trend(row, params, distance_bps):
                trend_seen = True

            in_pullback_zone = (
                -params.max_below_vwap_bps <= distance_bps <= params.pullback_above_vwap_bps
            )
            if trend_seen and in_pullback_zone:
                in_pullback = True
                pullback_low = row.price if pullback_low is None else min(pullback_low, row.price)
                pullback_high = (
                    row.price if pullback_high is None else max(pullback_high, row.price)
                )
                previous = row
                continue

            if not in_pullback or pullback_low is None or pullback_high is None:
                previous = row
                continue

            reclaimed = (
                distance_bps >= params.reclaim_above_vwap_bps
                and row.price > pullback_high
                and (previous is None or row.price > previous.price)
            )
            if not reclaimed or not passes_execution(row, params):
                previous = row
                continue

            stop_price = min(row.vwap, pullback_low)
            if stop_price >= row.price:
                previous = row
                continue
            risk_bps = ((row.price - stop_price) / row.price) * 10000.0
            if risk_bps > params.max_risk_bps:
                previous = row
                continue

            out.append(
                Candidate(
                    symbol=row.symbol,
                    trading_date=row.trading_date,
                    timestamp=row.timestamp,
                    price=row.price,
                    vwap=row.vwap,
                    pullback_low=pullback_low,
                    pullback_high=pullback_high,
                    stop_price=stop_price,
                    risk_bps=risk_bps,
                    vwap_distance_bps=distance_bps,
                    volume_ratio=row.volume_ratio,
                    spread_bps=row.spread_bps,
                    spread_ticks=row.spread_ticks,
                    ask_depth_5=row.ask_depth_5,
                    ret_15_bps=forward_return_bps(row, times, prices, 15),
                    ret_30_bps=forward_return_bps(row, times, prices, 30),
                )
            )
            if params.one_per_symbol_day:
                break
            trend_seen = False
            in_pullback = False
            pullback_low = None
            pullback_high = None
            previous = row
    return sorted(out, key=lambda item: (item.timestamp, item.symbol))


def passes_time(row: Row, params: Params) -> bool:
    return not (
        row.minutes_from_open is None
        or row.minutes_from_open < params.entry_minute
        or row.minutes_to_close is None
        or row.minutes_to_close < params.min_minutes_to_close
    )


def passes_trend(row: Row, params: Params, distance_bps: float) -> bool:
    if distance_bps < params.trend_min_bps:
        return False
    return not (
        params.require_sma_uptrend
        and (row.sma_short is None or row.sma_long is None or row.sma_short < row.sma_long)
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
    print_value_stats("risk_bps", [item.risk_bps for item in candidates])
    print_value_stats("vwap_distance_bps", [item.vwap_distance_bps for item in candidates])
    print_value_stats("spread_bps", [item.spread_bps for item in candidates])
    print_value_stats("spread_ticks", [item.spread_ticks for item in candidates])


def print_stage_diagnostics(rows: list[Row], params: Params) -> None:
    timed = [row for row in rows if passes_time(row, params) and row.vwap is not None]
    trend = [
        row
        for row in timed
        if passes_trend(row, params, ((row.price - row.vwap) / row.vwap) * 10000.0)
    ]
    pullback = [
        row
        for row in timed
        if -params.max_below_vwap_bps
        <= ((row.price - row.vwap) / row.vwap) * 10000.0
        <= params.pullback_above_vwap_bps
    ]
    execution = [row for row in timed if passes_execution(row, params)]
    print("stage diagnostics:")
    print(f"  timed_vwap_rows={len(timed)}")
    print(f"  trend_rows={len(trend)}")
    print(f"  pullback_zone_rows={len(pullback)}")
    print(f"  execution_pass_rows={len(execution)}")
    print_value_stats(
        "  timed_vwap_distance_bps",
        [((row.price - row.vwap) / row.vwap) * 10000.0 for row in timed if row.vwap],
    )


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
                "vwap",
                "pullback_low",
                "pullback_high",
                "stop_price",
                "risk_bps",
                "vwap_distance_bps",
                "volume_ratio",
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
                    "vwap": item.vwap,
                    "pullback_low": item.pullback_low,
                    "pullback_high": item.pullback_high,
                    "stop_price": item.stop_price,
                    "risk_bps": item.risk_bps,
                    "vwap_distance_bps": item.vwap_distance_bps,
                    "volume_ratio": item.volume_ratio,
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
