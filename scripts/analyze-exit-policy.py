#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Explore target/trailing/max-hold exit policies for BUY signals or fills."""

from __future__ import annotations

import argparse
import json
import os
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True, slots=True)
class Entry:
    symbol: str
    timestamp: datetime
    price: float
    quantity: int
    source: str
    gate_pass: bool | None


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    timestamp: datetime
    price: float


@dataclass(frozen=True, slots=True)
class Policy:
    target_pct: float | None
    trailing_pct: float | None
    max_hold_minutes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", required=True)
    parser.add_argument("--features", action="append", default=[], help="DATE=PATH")
    parser.add_argument("--actual-fills", action="store_true")
    parser.add_argument("--cooldown-seconds", type=int, default=300)
    parser.add_argument("--quantity", type=int, default=100)
    parser.add_argument("--max-risk-penalty", type=float, default=1.5)
    parser.add_argument("--max-volume-surge", type=float, default=2.1)
    parser.add_argument("--max-momentum", type=float, default=0.4)
    parser.add_argument("--target-pcts", default="0,0.003,0.005,0.008")
    parser.add_argument("--trailing-pcts", default="0,0.002,0.003,0.005")
    parser.add_argument("--max-holds", default="10,15,30,45")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--top", type=int, default=20)
    return parser.parse_args()


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_feature_args(items: list[str]) -> dict[date, Path]:
    out: dict[date, Path] = {}
    for item in items:
        raw_date, sep, raw_path = item.partition("=")
        if sep != "=":
            raise SystemExit(f"--features must be DATE=PATH: {item}")
        out[parse_date(raw_date)] = Path(raw_path)
    return out


def parse_float_options(value: str) -> list[float | None]:
    out: list[float | None] = []
    for part in value.split(","):
        if not part:
            continue
        parsed = float(part)
        out.append(None if parsed == 0 else parsed)
    return out


def parse_int_options(value: str) -> list[int]:
    return sorted({int(part) for part in value.split(",") if part})


def date_bounds_jst(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, JST).astimezone(UTC)
    end = datetime.combine(trading_date + timedelta(days=1), time.min, JST).astimezone(UTC)
    return start, end


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def get_rows(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def fetch_watchlist(client: httpx.Client, trading_date: date) -> dict[str, dict[str, Any]]:
    rows = get_rows(
        client,
        "/rest/v1/watchlist",
        {"select": "symbol,selected_reasons", "valid_date": f"eq.{trading_date.isoformat()}"},
    )
    return {str(row["symbol"]): row.get("selected_reasons") or {} for row in rows}


def gate_pass(reasons: dict[str, Any] | None, args: argparse.Namespace) -> bool | None:
    if reasons is None:
        return None
    return not (
        float(reasons.get("risk_penalty", 0.0)) > args.max_risk_penalty
        or float(reasons.get("volume_surge", 1.0)) > args.max_volume_surge
        or float(reasons.get("momentum", 0.0)) > args.max_momentum
    )


def fetch_signal_entries(
    client: httpx.Client,
    trading_date: date,
    watchlist: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[Entry]:
    start, end = date_bounds_jst(trading_date)
    rows = get_rows(
        client,
        "/rest/v1/aggregator_logs",
        {
            "select": "symbol,action,signal_source,created_at",
            "and": f"(created_at.gte.{start.isoformat()},created_at.lt.{end.isoformat()})",
            "order": "created_at.asc",
        },
    )
    entries: list[Entry] = []
    last_by_key: dict[tuple[str, str], datetime] = {}
    for row in rows:
        if row.get("action") != "BUY":
            continue
        symbol = str(row["symbol"])
        source = str(row["signal_source"])
        timestamp = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        key = (symbol, source)
        last = last_by_key.get(key)
        if last is not None and (timestamp - last).total_seconds() < args.cooldown_seconds:
            continue
        last_by_key[key] = timestamp
        entries.append(
            Entry(
                symbol=symbol,
                timestamp=timestamp,
                price=0.0,
                quantity=args.quantity,
                source=source,
                gate_pass=gate_pass(watchlist.get(symbol), args),
            )
        )
    return entries


def fetch_fill_entries(
    client: httpx.Client,
    trading_date: date,
    watchlist: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> list[Entry]:
    start, end = date_bounds_jst(trading_date)
    rows = get_rows(
        client,
        "/rest/v1/trades_paper",
        {
            "select": "symbol,side,quantity,price,executed_at,signal_source",
            "and": f"(executed_at.gte.{start.isoformat()},executed_at.lt.{end.isoformat()})",
            "order": "executed_at.asc",
        },
    )
    return [
        Entry(
            symbol=str(row["symbol"]),
            timestamp=datetime.fromisoformat(str(row["executed_at"]).replace("Z", "+00:00")),
            price=float(row["price"]),
            quantity=int(row["quantity"]),
            source=str(row["signal_source"]),
            gate_pass=gate_pass(watchlist.get(str(row["symbol"])), args),
        )
        for row in rows
        if row.get("side") == "BUY"
    ]


def read_features(path: Path) -> dict[str, tuple[list[datetime], list[float]]]:
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            symbol = str(raw["symbol"])
            if symbol not in out:
                out[symbol] = ([], [])
            out[symbol][0].append(datetime.fromisoformat(raw["timestamp"]))
            out[symbol][1].append(float(raw["price"]))
    for times, prices in out.values():
        paired = sorted(zip(times, prices, strict=True))
        times[:] = [item[0] for item in paired]
        prices[:] = [item[1] for item in paired]
    return out


def simulate_entry(
    entry: Entry,
    timeline: dict[str, tuple[list[datetime], list[float]]],
    policy: Policy,
) -> float | None:
    if entry.symbol not in timeline:
        return None
    times, prices = timeline[entry.symbol]
    start = bisect_left(times, entry.timestamp)
    if start >= len(prices):
        return None
    entry_price = entry.price if entry.price > 0 else prices[start]
    target = None if policy.target_pct is None else entry_price * (1.0 + policy.target_pct)
    peak = entry_price
    end_at = entry.timestamp + timedelta(minutes=policy.max_hold_minutes)
    last_price = entry_price
    for idx in range(start, len(prices)):
        if times[idx] > end_at:
            break
        price = prices[idx]
        last_price = price
        if target is not None and price >= target:
            return (target - entry_price) * entry.quantity
        if price > peak:
            peak = price
        if policy.trailing_pct is not None and peak > entry_price:
            trail = peak * (1.0 - policy.trailing_pct)
            if price <= trail:
                return (trail - entry_price) * entry.quantity
    return (last_price - entry_price) * entry.quantity


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "pnl": 0, "avg": 0.0, "win_rate": 0.0}
    return {
        "count": len(values),
        "pnl": round(sum(values)),
        "avg": round(mean(values), 1),
        "win_rate": round(sum(1 for value in values if value > 0) / len(values), 3),
    }


def main() -> int:
    args = parse_args()
    dates = [parse_date(part) for part in args.dates.split(",") if part]
    feature_paths = parse_feature_args(args.features)
    policies = [
        Policy(target, trailing, max_hold)
        for target in parse_float_options(args.target_pcts)
        for trailing in parse_float_options(args.trailing_pcts)
        for max_hold in parse_int_options(args.max_holds)
    ]

    url = require_env("SUPABASE_URL").rstrip("/")
    key = require_env("SUPABASE_SECRET_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    totals: defaultdict[Policy, list[float]] = defaultdict(list)
    gate_totals: defaultdict[tuple[Policy, str], list[float]] = defaultdict(list)

    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        for trading_date in dates:
            watchlist = fetch_watchlist(client, trading_date)
            entries = (
                fetch_fill_entries(client, trading_date, watchlist, args)
                if args.actual_fills
                else fetch_signal_entries(client, trading_date, watchlist, args)
            )
            timeline = read_features(feature_paths[trading_date])
            for policy in policies:
                for entry in entries:
                    pnl = simulate_entry(entry, timeline, policy)
                    if pnl is None:
                        continue
                    totals[policy].append(pnl)
                    gate_label = "missing" if entry.gate_pass is None else str(entry.gate_pass)
                    gate_totals[(policy, gate_label)].append(pnl)

    ranked = sorted(
        (
            {
                "target_pct": policy.target_pct,
                "trailing_pct": policy.trailing_pct,
                "max_hold": policy.max_hold_minutes,
                **summarize(values),
                "gate_pass": summarize(gate_totals[(policy, "True")]),
                "gate_fail": summarize(gate_totals[(policy, "False")]),
            }
            for policy, values in totals.items()
        ),
        key=lambda row: (row["pnl"], row["avg"]),
        reverse=True,
    )
    for row in ranked[: args.top]:
        print(
            f"target={row['target_pct']} trailing={row['trailing_pct']} "
            f"hold={row['max_hold']} total={summarize_text(row)} "
            f"gate_pass={summarize_text(row['gate_pass'])} "
            f"gate_fail={summarize_text(row['gate_fail'])}"
        )
    return 0


def summarize_text(row: dict[str, Any]) -> str:
    return f"count={row['count']} pnl={row['pnl']} avg={row['avg']} win={row['win_rate']}"


if __name__ == "__main__":
    raise SystemExit(main())
