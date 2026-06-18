#!/usr/bin/env python3
"""Explore intraday rule settings from archived ProcessedFeatures.

This is intentionally a diagnostic tool, not a backtest. It estimates whether
settings produce a usable number of BUY candidates with executable books and
reasonable short forward returns.
"""

from __future__ import annotations

import argparse
import csv
import json
from bisect import bisect_left
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any


@dataclass(frozen=True, slots=True)
class Row:
    symbol: str
    timestamp: datetime
    price: float
    rsi: float | None
    vwap: float | None
    sma_short: float | None
    sma_long: float | None
    volume_ratio: float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    best_ask: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    book_imbalance_5: float | None
    minutes_from_open: int | None
    minutes_to_close: int | None
    book_age_seconds: float | None
    ret_15: float | None = None
    ret_30: float | None = None


@dataclass(frozen=True, slots=True)
class Params:
    rsi_buy: float
    bollinger_tolerance: float
    volume_ratio_min: float | None
    max_spread_bps: float
    max_spread_ticks: float
    min_ask_depth_5: int
    min_book_imbalance_5: float
    min_minutes_from_open: int
    min_minutes_to_close: int
    max_book_age_seconds: float | None
    max_price: float


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    rows = read_rows(args.features)
    timeline = build_timeline(rows)
    rows = attach_forward_returns(rows, timeline)
    base = [
        row
        for row in rows
        if row.minutes_from_open is not None
        and row.minutes_to_close is not None
        and (is_rsi_buy(row, 35.0) or is_bollinger_buy(row, 0.0))
    ]

    results = [evaluate(base, timeline, params) for params in param_grid()]
    results.sort(key=lambda item: item["score"], reverse=True)
    write_csv(results, args.output)

    current = Params(
        rsi_buy=25.0,
        bollinger_tolerance=0.15,
        volume_ratio_min=None,
        max_spread_bps=30.0,
        max_spread_ticks=2.0,
        min_ask_depth_5=300,
        min_book_imbalance_5=-0.5,
        min_minutes_from_open=15,
        min_minutes_to_close=60,
        max_book_age_seconds=None,
        max_price=10000.0,
    )
    current_result = evaluate(base, timeline, current)

    print(f"rows={len(rows)} base_buy_candidates={len(base)} output={args.output}")
    print("current_like")
    print(format_result(current_result))
    print("top")
    for result in results[: args.top]:
        print(format_result(result))
    return 0


def read_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            timestamp = datetime.fromisoformat(raw["timestamp"])
            book_ts = None
            if raw.get("order_book") and raw["order_book"].get("timestamp"):
                book_ts = datetime.fromisoformat(raw["order_book"]["timestamp"])
            rows.append(
                Row(
                    symbol=str(raw["symbol"]),
                    timestamp=timestamp,
                    price=float(raw["price"]),
                    rsi=to_float(raw.get("rsi")),
                    vwap=to_float(raw.get("vwap")),
                    sma_short=to_float(raw.get("sma_short")),
                    sma_long=to_float(raw.get("sma_long")),
                    volume_ratio=to_float(raw.get("volume_ratio")),
                    bollinger_upper=to_float(raw.get("bollinger_upper")),
                    bollinger_lower=to_float(raw.get("bollinger_lower")),
                    best_ask=to_float(raw.get("best_ask")),
                    spread_bps=to_float(raw.get("spread_bps")),
                    spread_ticks=to_float(raw.get("spread_ticks")),
                    ask_depth_5=to_int(raw.get("ask_depth_5")),
                    book_imbalance_5=to_float(raw.get("book_imbalance_5")),
                    minutes_from_open=to_int(raw.get("minutes_from_open")),
                    minutes_to_close=to_int(raw.get("minutes_to_close")),
                    book_age_seconds=None
                    if book_ts is None
                    else max(0.0, (timestamp - book_ts).total_seconds()),
                )
            )
    return sorted(rows, key=lambda row: (row.symbol, row.timestamp))


def build_timeline(rows: list[Row]) -> dict[str, tuple[list[datetime], list[float]]]:
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    by_symbol: dict[str, list[Row]] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    for symbol, symbol_rows in by_symbol.items():
        ordered = sorted(symbol_rows, key=lambda row: row.timestamp)
        out[symbol] = ([row.timestamp for row in ordered], [row.price for row in ordered])
    return out


def attach_forward_returns(
    rows: list[Row],
    timeline: dict[str, tuple[list[datetime], list[float]]],
) -> list[Row]:
    return [
        Row(
            symbol=row.symbol,
            timestamp=row.timestamp,
            price=row.price,
            rsi=row.rsi,
            vwap=row.vwap,
            sma_short=row.sma_short,
            sma_long=row.sma_long,
            volume_ratio=row.volume_ratio,
            bollinger_upper=row.bollinger_upper,
            bollinger_lower=row.bollinger_lower,
            best_ask=row.best_ask,
            spread_bps=row.spread_bps,
            spread_ticks=row.spread_ticks,
            ask_depth_5=row.ask_depth_5,
            book_imbalance_5=row.book_imbalance_5,
            minutes_from_open=row.minutes_from_open,
            minutes_to_close=row.minutes_to_close,
            book_age_seconds=row.book_age_seconds,
            ret_15=forward_return(row, timeline, 15),
            ret_30=forward_return(row, timeline, 30),
        )
        for row in rows
    ]


def param_grid() -> list[Params]:
    params: list[Params] = []
    for rsi_buy in (20.0, 25.0, 30.0, 35.0):
        for boll_tol in (0.0, 0.05, 0.15):
            for vol_min in (None, 0.05, 0.10, 0.20):
                for spread_bps in (15.0, 30.0):
                    for spread_ticks in (1.0, 2.0):
                        for ask_depth in (300, 1000):
                            for imbalance in (-0.5, 0.0):
                                for min_open in (15, 30):
                                    for min_close in (30, 60):
                                        for book_age in (30.0, 300.0, None):
                                            for max_price in (3000.0, 5000.0, 10000.0):
                                                params.append(
                                                    Params(
                                                        rsi_buy=rsi_buy,
                                                        bollinger_tolerance=boll_tol,
                                                        volume_ratio_min=vol_min,
                                                        max_spread_bps=spread_bps,
                                                        max_spread_ticks=spread_ticks,
                                                        min_ask_depth_5=ask_depth,
                                                        min_book_imbalance_5=imbalance,
                                                        min_minutes_from_open=min_open,
                                                        min_minutes_to_close=min_close,
                                                        max_book_age_seconds=book_age,
                                                        max_price=max_price,
                                                    )
                                                )
    return params


def evaluate(
    rows: list[Row],
    timeline: dict[str, tuple[list[datetime], list[float]]],
    params: Params,
) -> dict[str, Any]:
    candidates = [row for row in rows if passes(row, params)]
    deduped = dedupe(candidates, cooldown_seconds=60)
    fillable = [row for row in deduped if row.best_ask is not None and row.best_ask <= row.price]
    del timeline
    ret_15 = [row.ret_15 for row in deduped if row.ret_15 is not None]
    ret_30 = [row.ret_30 for row in deduped if row.ret_30 is not None]
    avg_spread = mean([row.spread_bps for row in deduped if row.spread_bps is not None] or [0.0])
    count = len(deduped)
    fill_rate = len(fillable) / count if count else 0.0
    avg_ret_15 = mean(ret_15) if ret_15 else 0.0
    avg_ret_30 = mean(ret_30) if ret_30 else 0.0
    count_penalty = abs(count - 40) * 0.03
    sparse_penalty = 8.0 if count < 10 else 0.0
    score = (fill_rate * 100.0) + (avg_ret_30 * 10000.0) - count_penalty - sparse_penalty
    return {
        "score": round(score, 4),
        "raw_count": len(candidates),
        "dedup_count": count,
        "symbols": len({row.symbol for row in deduped}),
        "immediate_fillable": len(fillable),
        "immediate_fill_rate": round(fill_rate, 4),
        "avg_ret_15_bps": round(avg_ret_15 * 10000.0, 3),
        "avg_ret_30_bps": round(avg_ret_30 * 10000.0, 3),
        "avg_spread_bps": round(avg_spread, 3),
        **asdict(params),
    }


def passes(row: Row, params: Params) -> bool:
    if row.price > params.max_price:
        return False
    if not (is_rsi_buy(row, params.rsi_buy) or is_bollinger_buy(row, params.bollinger_tolerance)):
        return False
    if params.volume_ratio_min is not None and (
        row.volume_ratio is None or row.volume_ratio < params.volume_ratio_min
    ):
        return False
    if row.spread_bps is None or row.spread_bps > params.max_spread_bps:
        return False
    if row.spread_ticks is None or row.spread_ticks > params.max_spread_ticks:
        return False
    if row.ask_depth_5 is None or row.ask_depth_5 < params.min_ask_depth_5:
        return False
    if row.book_imbalance_5 is None or row.book_imbalance_5 < params.min_book_imbalance_5:
        return False
    if row.minutes_from_open is None or row.minutes_from_open < params.min_minutes_from_open:
        return False
    if row.minutes_to_close is None or row.minutes_to_close < params.min_minutes_to_close:
        return False
    return not (
        params.max_book_age_seconds is not None
        and (row.book_age_seconds is None or row.book_age_seconds > params.max_book_age_seconds)
    )


def is_rsi_buy(row: Row, threshold: float) -> bool:
    return row.rsi is not None and row.rsi <= threshold


def is_bollinger_buy(row: Row, tolerance: float) -> bool:
    if row.bollinger_upper is None or row.bollinger_lower is None:
        return False
    width = row.bollinger_upper - row.bollinger_lower
    return width > 0 and row.price < row.bollinger_lower - (width * tolerance)


def dedupe(rows: list[Row], *, cooldown_seconds: int) -> list[Row]:
    out: list[Row] = []
    last_by_symbol: dict[str, datetime] = {}
    for row in sorted(rows, key=lambda item: item.timestamp):
        last = last_by_symbol.get(row.symbol)
        if last is not None and (row.timestamp - last).total_seconds() < cooldown_seconds:
            continue
        out.append(row)
        last_by_symbol[row.symbol] = row.timestamp
    return out


def forward_return(
    row: Row,
    timeline: dict[str, tuple[list[datetime], list[float]]],
    minutes: int,
) -> float | None:
    times, prices = timeline[row.symbol]
    idx = bisect_left(times, row.timestamp + timedelta(minutes=minutes))
    if idx >= len(prices) or row.price <= 0:
        return None
    return (prices[idx] / row.price) - 1.0


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def format_result(row: dict[str, Any]) -> str:
    keys = (
        "score",
        "dedup_count",
        "symbols",
        "immediate_fill_rate",
        "avg_ret_15_bps",
        "avg_ret_30_bps",
        "avg_spread_bps",
        "rsi_buy",
        "bollinger_tolerance",
        "volume_ratio_min",
        "max_spread_bps",
        "max_spread_ticks",
        "min_ask_depth_5",
        "min_book_imbalance_5",
        "min_minutes_from_open",
        "min_minutes_to_close",
        "max_book_age_seconds",
        "max_price",
    )
    return " ".join(f"{key}={row[key]}" for key in keys)


def to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
