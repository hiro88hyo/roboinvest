#!/usr/bin/env python3
"""Explore opening range breakout candidates from archived ProcessedFeatures.

This is a diagnostic counter, not a trading backtest. It estimates whether an
opening-range breakout hypothesis produces enough candidates and whether those
candidates have short forward follow-through in archived feature data.
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
    volume_ratio: float | None
    cumulative_volume: int | None
    trade_volume_delta: int | None
    best_ask: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    minutes_from_open: int | None
    minutes_to_close: int | None


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    trading_date: date
    timestamp: datetime
    price: float
    opening_high: float
    opening_low: float
    stop_price: float
    risk_bps: float
    vwap: float | None
    volume_ratio: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    ret_15_bps: float | None
    ret_30_bps: float | None


@dataclass(frozen=True, slots=True)
class Params:
    range_minutes: int
    entry_minute: int
    min_minutes_to_close: int
    min_volume_ratio: float | None
    max_spread_bps: float | None
    max_spread_ticks: float | None
    min_ask_depth_5: int | None
    max_risk_bps: float | None
    cooldown_seconds: int
    require_vwap: bool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count opening range breakout candidates from ProcessedFeatures JSONL.",
    )
    parser.add_argument("--features", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=None, help="Optional candidate CSV output.")
    parser.add_argument("--range-minutes", type=int, default=15)
    parser.add_argument("--entry-minute", type=int, default=15)
    parser.add_argument("--min-minutes-to-close", type=int, default=45)
    parser.add_argument("--min-volume-ratio", type=float, default=2.0)
    parser.add_argument("--max-spread-bps", type=float, default=None)
    parser.add_argument("--max-spread-ticks", type=float, default=None)
    parser.add_argument("--min-ask-depth-5", type=int, default=None)
    parser.add_argument("--max-risk-bps", type=float, default=300.0)
    parser.add_argument("--cooldown-seconds", type=int, default=900)
    parser.add_argument(
        "--no-volume-ratio", action="store_true", help="Do not require volume_ratio."
    )
    parser.add_argument("--no-vwap", action="store_true", help="Do not require price >= VWAP.")
    args = parser.parse_args()

    params = Params(
        range_minutes=args.range_minutes,
        entry_minute=args.entry_minute,
        min_minutes_to_close=args.min_minutes_to_close,
        min_volume_ratio=None if args.no_volume_ratio else args.min_volume_ratio,
        max_spread_bps=args.max_spread_bps,
        max_spread_ticks=args.max_spread_ticks,
        min_ask_depth_5=args.min_ask_depth_5,
        max_risk_bps=args.max_risk_bps,
        cooldown_seconds=args.cooldown_seconds,
        require_vwap=not args.no_vwap,
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
                            volume_ratio=to_float(raw.get("volume_ratio")),
                            cumulative_volume=to_int(raw.get("cumulative_volume")),
                            trade_volume_delta=to_int(raw.get("trade_volume_delta")),
                            best_ask=to_float(raw.get("best_ask")),
                            spread_bps=to_float(raw.get("spread_bps")),
                            spread_ticks=to_float(raw.get("spread_ticks")),
                            ask_depth_5=to_int(raw.get("ask_depth_5")),
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
        opening = [
            row
            for row in ordered
            if row.minutes_from_open is not None
            and 0 <= row.minutes_from_open < params.range_minutes
        ]
        if not opening:
            continue
        opening_high = max(row.price for row in opening)
        opening_low = min(row.price for row in opening)
        last_candidate_at: datetime | None = None
        previous: Row | None = None
        times = [row.timestamp for row in ordered]
        prices = [row.price for row in ordered]
        for row in ordered:
            if not passes_base(row, params):
                previous = row
                continue
            crossed = row.price > opening_high and (
                previous is None or previous.price <= opening_high
            )
            previous = row
            if not crossed:
                continue
            if last_candidate_at is not None:
                age = (row.timestamp - last_candidate_at).total_seconds()
                if age < params.cooldown_seconds:
                    continue
            stop_price = max(opening_low, row.vwap or opening_low)
            if stop_price >= row.price:
                continue
            risk_bps = ((row.price - stop_price) / row.price) * 10000.0
            if params.max_risk_bps is not None and risk_bps > params.max_risk_bps:
                continue
            out.append(
                Candidate(
                    symbol=row.symbol,
                    trading_date=row.trading_date,
                    timestamp=row.timestamp,
                    price=row.price,
                    opening_high=opening_high,
                    opening_low=opening_low,
                    stop_price=stop_price,
                    risk_bps=risk_bps,
                    vwap=row.vwap,
                    volume_ratio=row.volume_ratio,
                    spread_bps=row.spread_bps,
                    spread_ticks=row.spread_ticks,
                    ask_depth_5=row.ask_depth_5,
                    ret_15_bps=forward_return_bps(row, times, prices, 15),
                    ret_30_bps=forward_return_bps(row, times, prices, 30),
                )
            )
            last_candidate_at = row.timestamp
    return sorted(out, key=lambda item: (item.timestamp, item.symbol))


def passes_base(row: Row, params: Params) -> bool:
    if not passes_time(row, params):
        return False
    if params.require_vwap and (row.vwap is None or row.price < row.vwap):
        return False
    if params.min_volume_ratio is not None and (
        row.volume_ratio is None or row.volume_ratio < params.min_volume_ratio
    ):
        return False
    return passes_execution(row, params)


def passes_time(row: Row, params: Params) -> bool:
    if row.minutes_from_open is None or row.minutes_from_open < params.entry_minute:
        return False
    return not (row.minutes_to_close is None or row.minutes_to_close < params.min_minutes_to_close)


def passes_execution(row: Row, params: Params) -> bool:
    if params.max_spread_bps is not None and (
        row.spread_bps is None or row.spread_bps > params.max_spread_bps
    ):
        return False
    if params.max_spread_ticks is not None and (
        row.spread_ticks is None or row.spread_ticks > params.max_spread_ticks
    ):
        return False
    return not (
        params.min_ask_depth_5 is not None
        and (row.ask_depth_5 is None or row.ask_depth_5 < params.min_ask_depth_5)
    )


def passes_risk(row: Row, opening_low: float, params: Params) -> bool:
    stop_price = max(opening_low, row.vwap or opening_low)
    if stop_price >= row.price:
        return False
    risk_bps = ((row.price - stop_price) / row.price) * 10000.0
    return not (params.max_risk_bps is not None and risk_bps > params.max_risk_bps)


def forward_return_bps(
    row: Row,
    times: list[datetime],
    prices: list[float],
    minutes: int,
) -> float | None:
    idx = bisect_left(times, row.timestamp + timedelta(minutes=minutes))
    if idx >= len(prices) or row.price <= 0:
        return None
    return ((prices[idx] / row.price) - 1.0) * 10000.0


def print_summary(
    rows: list[Row],
    candidates: list[Candidate],
    params: Params,
    output: Path | None,
) -> None:
    ret_15 = [item.ret_15_bps for item in candidates if item.ret_15_bps is not None]
    ret_30 = [item.ret_30_bps for item in candidates if item.ret_30_bps is not None]
    risk = [item.risk_bps for item in candidates]
    by_day: dict[date, int] = {}
    for item in candidates:
        by_day[item.trading_date] = by_day.get(item.trading_date, 0) + 1

    print(
        "opening_range_breakout "
        f"rows={len(rows)} candidates={len(candidates)} "
        f"symbols={len({item.symbol for item in candidates})} "
        f"days={len(by_day)}"
    )
    print(
        "params "
        f"range_minutes={params.range_minutes} "
        f"entry_minute={params.entry_minute} "
        f"min_volume_ratio={params.min_volume_ratio} "
        f"max_spread_bps={params.max_spread_bps} "
        f"max_spread_ticks={params.max_spread_ticks} "
        f"min_ask_depth_5={params.min_ask_depth_5} "
        f"max_risk_bps={params.max_risk_bps} "
        f"require_vwap={params.require_vwap}"
    )
    print(
        "returns "
        f"avg_ret_15_bps={round(mean(ret_15), 3) if ret_15 else None} "
        f"avg_ret_30_bps={round(mean(ret_30), 3) if ret_30 else None} "
        f"avg_risk_bps={round(mean(risk), 3) if risk else None}"
    )
    if by_day:
        day_counts = " ".join(f"{day.isoformat()}={count}" for day, count in sorted(by_day.items()))
        print(f"by_day {day_counts}")
    if output is not None:
        print(f"output={output}")


def print_stage_diagnostics(rows: list[Row], params: Params) -> None:
    stages = stage_diagnostics(rows, params)
    print(
        "stages "
        f"crosses={stages['crosses']} "
        f"vwap={stages['vwap']} "
        f"volume={stages['volume']} "
        f"execution={stages['execution']} "
        f"risk={stages['risk']} "
        f"cooldown={stages['cooldown']}"
    )
    ratios = stages["vwap_volume_ratios"]
    if isinstance(ratios, list) and ratios:
        print(
            "volume_ratio_after_vwap "
            f"count={len(ratios)} "
            f"avg={round(mean(ratios), 4)} "
            f"median={round(median(ratios), 4)} "
            f"max={round(max(ratios), 4)} "
            f"ge_1_0={sum(1 for value in ratios if value >= 1.0)} "
            f"ge_1_2={sum(1 for value in ratios if value >= 1.2)} "
            f"ge_1_5={sum(1 for value in ratios if value >= 1.5)} "
            f"ge_2_0={sum(1 for value in ratios if value >= 2.0)}"
        )
    deltas = stages["vwap_trade_volume_deltas"]
    if isinstance(deltas, list) and deltas:
        print(
            "trade_volume_delta_after_vwap "
            f"count={len(deltas)} "
            f"avg={round(mean(deltas), 3)} "
            f"median={round(median(deltas), 3)} "
            f"max={max(deltas)}"
        )


def stage_diagnostics(rows: list[Row], params: Params) -> dict[str, object]:
    by_key: dict[tuple[date, str], list[Row]] = {}
    for row in rows:
        by_key.setdefault((row.trading_date, row.symbol), []).append(row)

    cross_rows: list[tuple[Row, float]] = []
    for (_trading_date, _symbol), symbol_rows in by_key.items():
        ordered = sorted(symbol_rows, key=lambda item: item.timestamp)
        opening = [
            row
            for row in ordered
            if row.minutes_from_open is not None
            and 0 <= row.minutes_from_open < params.range_minutes
        ]
        if not opening:
            continue
        opening_high = max(row.price for row in opening)
        opening_low = min(row.price for row in opening)
        previous: Row | None = None
        for row in ordered:
            if not passes_time(row, params):
                previous = row
                continue
            crossed = row.price > opening_high and (
                previous is None or previous.price <= opening_high
            )
            previous = row
            if crossed:
                cross_rows.append((row, opening_low))

    vwap_rows = [
        item
        for item in cross_rows
        if not params.require_vwap or (item[0].vwap is not None and item[0].price >= item[0].vwap)
    ]
    volume_rows = [
        item
        for item in vwap_rows
        if params.min_volume_ratio is None
        or (item[0].volume_ratio is not None and item[0].volume_ratio >= params.min_volume_ratio)
    ]
    execution_rows = [item for item in volume_rows if passes_execution(item[0], params)]
    risk_rows = [item for item in execution_rows if passes_risk(item[0], item[1], params)]
    return {
        "crosses": len(cross_rows),
        "vwap": len(vwap_rows),
        "volume": len(volume_rows),
        "execution": len(execution_rows),
        "risk": len(risk_rows),
        "cooldown": len(find_candidates(rows, params)),
        "vwap_volume_ratios": [
            item[0].volume_ratio for item in vwap_rows if item[0].volume_ratio is not None
        ],
        "vwap_trade_volume_deltas": [
            item[0].trade_volume_delta
            for item in vwap_rows
            if item[0].trade_volume_delta is not None
        ],
    }


def write_candidates(candidates: list[Candidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trading_date",
        "symbol",
        "timestamp",
        "price",
        "opening_high",
        "opening_low",
        "stop_price",
        "risk_bps",
        "vwap",
        "volume_ratio",
        "spread_bps",
        "spread_ticks",
        "ask_depth_5",
        "ret_15_bps",
        "ret_30_bps",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidates:
            writer.writerow(
                {
                    "trading_date": item.trading_date.isoformat(),
                    "symbol": item.symbol,
                    "timestamp": item.timestamp.isoformat(),
                    "price": item.price,
                    "opening_high": item.opening_high,
                    "opening_low": item.opening_low,
                    "stop_price": item.stop_price,
                    "risk_bps": round(item.risk_bps, 3),
                    "vwap": item.vwap,
                    "volume_ratio": item.volume_ratio,
                    "spread_bps": item.spread_bps,
                    "spread_ticks": item.spread_ticks,
                    "ask_depth_5": item.ask_depth_5,
                    "ret_15_bps": None if item.ret_15_bps is None else round(item.ret_15_bps, 3),
                    "ret_30_bps": None if item.ret_30_bps is None else round(item.ret_30_bps, 3),
                }
            )


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
