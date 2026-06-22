#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Compare long/short outcomes after aggregator BUY/SELL signals."""

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
class Signal:
    signal_id: str
    symbol: str
    action: str
    confidence: float
    signal_source: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaperTrade:
    symbol: str
    side: str
    quantity: int
    price: float
    executed_at: datetime
    signal_source: str
    unified_signal_id: str | None


@dataclass(frozen=True, slots=True)
class FeatureTick:
    symbol: str
    timestamp: datetime
    price: float


@dataclass(frozen=True, slots=True)
class ScannerGate:
    max_risk_penalty: float | None
    max_volume_surge: float | None
    max_momentum: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", required=True, help="Comma-separated JST dates.")
    parser.add_argument(
        "--features",
        action="append",
        default=[],
        help="DATE=PATH mapping for collected feature JSONL. May be repeated.",
    )
    parser.add_argument("--cooldown-seconds", type=int, default=300)
    parser.add_argument("--horizons", default="5,10,15,30,45")
    parser.add_argument("--quantity", type=int, default=100)
    parser.add_argument("--max-risk-penalty", type=float)
    parser.add_argument("--max-volume-surge", type=float)
    parser.add_argument("--max-momentum", type=float)
    parser.add_argument(
        "--actual-fills",
        action="store_true",
        help="Analyze actual paper BUY fills instead of all aggregator signals.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
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


def parse_horizons(value: str) -> list[int]:
    horizons = sorted({int(part) for part in value.split(",") if part})
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise SystemExit("--horizons must contain positive minute values")
    return horizons


def date_bounds_jst(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, JST).astimezone(UTC)
    end = datetime.combine(trading_date + timedelta(days=1), time.min, JST).astimezone(UTC)
    return start, end


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def fetch_signals(client: httpx.Client, trading_date: date) -> list[Signal]:
    start, end = date_bounds_jst(trading_date)
    resp = client.get(
        "/rest/v1/aggregator_logs",
        params={
            "select": "signal_id,symbol,action,confidence,signal_source,created_at",
            "and": f"(created_at.gte.{start.isoformat()},created_at.lt.{end.isoformat()})",
            "order": "created_at.asc",
        },
    )
    resp.raise_for_status()
    rows = resp.json()
    return [
        Signal(
            signal_id=str(row["signal_id"]),
            symbol=str(row["symbol"]),
            action=str(row["action"]),
            confidence=float(row["confidence"]),
            signal_source=str(row["signal_source"]),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )
        for row in rows
        if row.get("action") in {"BUY", "SELL"}
    ]


def fetch_watchlist(client: httpx.Client, trading_date: date) -> dict[str, dict[str, Any]]:
    resp = client.get(
        "/rest/v1/watchlist",
        params={
            "select": "symbol,selected_reasons",
            "valid_date": f"eq.{trading_date.isoformat()}",
        },
    )
    resp.raise_for_status()
    rows = resp.json()
    return {
        str(row["symbol"]): row.get("selected_reasons") or {} for row in rows if row.get("symbol")
    }


def fetch_paper_trades(client: httpx.Client, trading_date: date) -> list[PaperTrade]:
    start, end = date_bounds_jst(trading_date)
    resp = client.get(
        "/rest/v1/trades_paper",
        params={
            "select": "symbol,side,quantity,price,executed_at,signal_source,unified_signal_id",
            "and": f"(executed_at.gte.{start.isoformat()},executed_at.lt.{end.isoformat()})",
            "order": "executed_at.asc",
        },
    )
    resp.raise_for_status()
    rows = resp.json()
    return [
        PaperTrade(
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            quantity=int(row["quantity"]),
            price=float(row["price"]),
            executed_at=datetime.fromisoformat(str(row["executed_at"]).replace("Z", "+00:00")),
            signal_source=str(row["signal_source"]),
            unified_signal_id=(
                None if row.get("unified_signal_id") is None else str(row["unified_signal_id"])
            ),
        )
        for row in rows
    ]


def read_features(path: Path) -> list[FeatureTick]:
    ticks: list[FeatureTick] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            ticks.append(
                FeatureTick(
                    symbol=str(raw["symbol"]),
                    timestamp=datetime.fromisoformat(raw["timestamp"]),
                    price=float(raw["price"]),
                )
            )
    return sorted(ticks, key=lambda row: (row.symbol, row.timestamp))


def timeline(rows: list[FeatureTick]) -> dict[str, tuple[list[datetime], list[float]]]:
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for row in rows:
        if row.symbol not in out:
            out[row.symbol] = ([], [])
        out[row.symbol][0].append(row.timestamp)
        out[row.symbol][1].append(row.price)
    return out


def price_at_or_after(
    symbol: str,
    timestamp: datetime,
    by_symbol: dict[str, tuple[list[datetime], list[float]]],
) -> float | None:
    if symbol not in by_symbol:
        return None
    times, prices = by_symbol[symbol]
    index = bisect_left(times, timestamp)
    if index >= len(prices):
        return None
    return prices[index]


def dedupe_signals(signals: list[Signal], cooldown_seconds: int) -> list[Signal]:
    out: list[Signal] = []
    last_by_key: dict[tuple[str, str, str], datetime] = {}
    for signal in sorted(signals, key=lambda item: item.created_at):
        key = (signal.symbol, signal.action, signal.signal_source)
        last = last_by_key.get(key)
        if last is not None and (signal.created_at - last).total_seconds() < cooldown_seconds:
            continue
        out.append(signal)
        last_by_key[key] = signal.created_at
    return out


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "pnl": 0, "avg": 0.0, "win_rate": 0.0}
    return {
        "count": len(values),
        "pnl": round(sum(values)),
        "avg": round(mean(values), 1),
        "win_rate": round(sum(1 for value in values if value > 0) / len(values), 3),
    }


def passes_scanner_gate(reasons: dict[str, Any] | None, gate: ScannerGate) -> bool | None:
    if reasons is None:
        return None
    if (
        gate.max_risk_penalty is not None
        and float(reasons.get("risk_penalty", 0.0)) > gate.max_risk_penalty
    ):
        return False
    if (
        gate.max_volume_surge is not None
        and float(reasons.get("volume_surge", 1.0)) > gate.max_volume_surge
    ):
        return False
    return not (
        gate.max_momentum is not None and float(reasons.get("momentum", 0.0)) > gate.max_momentum
    )


def analyze(
    signals_by_date: dict[date, list[Signal]],
    watchlist_by_date: dict[date, dict[str, dict[str, Any]]],
    feature_paths: dict[date, Path],
    *,
    cooldown_seconds: int,
    horizons: list[int],
    quantity: int,
    scanner_gate: ScannerGate,
) -> dict[str, Any]:
    metric_names = [f"{side}_{horizon}" for horizon in horizons for side in ("long", "short")]

    def metric_bucket() -> dict[str, list[float]]:
        return {name: [] for name in metric_names}

    result: dict[str, Any] = {}
    aggregate: defaultdict[str, dict[str, list[float]]] = defaultdict(metric_bucket)
    for trading_date, signals in signals_by_date.items():
        feature_path = feature_paths.get(trading_date)
        if feature_path is None:
            result[trading_date.isoformat()] = {"error": "missing_feature_path"}
            continue
        by_symbol = timeline(read_features(feature_path))
        watchlist = watchlist_by_date.get(trading_date, {})
        deduped = dedupe_signals(signals, cooldown_seconds)
        buckets: defaultdict[str, dict[str, list[float]]] = defaultdict(metric_bucket)
        missing = 0
        for signal in deduped:
            entry = price_at_or_after(signal.symbol, signal.created_at, by_symbol)
            forward_prices = {
                horizon: price_at_or_after(
                    signal.symbol,
                    signal.created_at + timedelta(minutes=horizon),
                    by_symbol,
                )
                for horizon in horizons
            }
            if entry is None or not any(price is not None for price in forward_prices.values()):
                missing += 1
                continue
            keys = [
                "ALL",
                f"action={signal.action}",
                f"source={signal.signal_source}",
                f"source={signal.signal_source},action={signal.action}",
            ]
            gate_pass = passes_scanner_gate(watchlist.get(signal.symbol), scanner_gate)
            if gate_pass is not None:
                gate_label = "pass" if gate_pass else "fail"
                keys.append(f"scanner_gate={gate_label}")
                keys.append(f"scanner_gate={gate_label},action={signal.action}")
            for key in keys:
                for horizon, price in forward_prices.items():
                    if price is None:
                        continue
                    long_pnl = (price - entry) * quantity
                    buckets[key][f"long_{horizon}"].append(long_pnl)
                    buckets[key][f"short_{horizon}"].append(-long_pnl)
                    aggregate[key][f"long_{horizon}"].append(long_pnl)
                    aggregate[key][f"short_{horizon}"].append(-long_pnl)
        result[trading_date.isoformat()] = {
            "signals": len(signals),
            "deduped": len(deduped),
            "missing_prices": missing,
            "buckets": {
                key: {metric: summarize(values) for metric, values in metrics.items()}
                for key, metrics in sorted(buckets.items())
            },
        }
    result["TOTAL"] = {
        "buckets": {
            key: {metric: summarize(values) for metric, values in metrics.items()}
            for key, metrics in sorted(aggregate.items())
        }
    }
    return result


def analyze_actual_fills(
    trades_by_date: dict[date, list[PaperTrade]],
    signals_by_date: dict[date, list[Signal]],
    watchlist_by_date: dict[date, dict[str, dict[str, Any]]],
    feature_paths: dict[date, Path],
    *,
    horizons: list[int],
    scanner_gate: ScannerGate,
) -> dict[str, Any]:
    metric_names = [f"{side}_{horizon}" for horizon in horizons for side in ("long", "short")]

    def metric_bucket() -> dict[str, list[float]]:
        return {name: [] for name in metric_names}

    result: dict[str, Any] = {}
    aggregate: defaultdict[str, dict[str, list[float]]] = defaultdict(metric_bucket)
    for trading_date, trades in trades_by_date.items():
        feature_path = feature_paths.get(trading_date)
        if feature_path is None:
            result[trading_date.isoformat()] = {"error": "missing_feature_path"}
            continue
        by_symbol = timeline(read_features(feature_path))
        watchlist = watchlist_by_date.get(trading_date, {})
        signals_by_id = {
            signal.signal_id: signal for signal in signals_by_date.get(trading_date, [])
        }
        buckets: defaultdict[str, dict[str, list[float]]] = defaultdict(metric_bucket)
        buy_fills = [trade for trade in trades if trade.side == "BUY"]
        missing_prices = 0
        missing_signal = 0
        for trade in buy_fills:
            forward_prices = {
                horizon: price_at_or_after(
                    trade.symbol,
                    trade.executed_at + timedelta(minutes=horizon),
                    by_symbol,
                )
                for horizon in horizons
            }
            if not any(price is not None for price in forward_prices.values()):
                missing_prices += 1
                continue
            signal = (
                None
                if trade.unified_signal_id is None
                else signals_by_id.get(trade.unified_signal_id)
            )
            if signal is None:
                missing_signal += 1
            keys = ["ALL", f"source={trade.signal_source}"]
            gate_pass = passes_scanner_gate(watchlist.get(trade.symbol), scanner_gate)
            if gate_pass is not None:
                gate_label = "pass" if gate_pass else "fail"
                keys.append(f"scanner_gate={gate_label}")
                keys.append(f"scanner_gate={gate_label},source={trade.signal_source}")
            for key in keys:
                for horizon, price in forward_prices.items():
                    if price is None:
                        continue
                    long_pnl = (price - trade.price) * trade.quantity
                    buckets[key][f"long_{horizon}"].append(long_pnl)
                    buckets[key][f"short_{horizon}"].append(-long_pnl)
                    aggregate[key][f"long_{horizon}"].append(long_pnl)
                    aggregate[key][f"short_{horizon}"].append(-long_pnl)
        result[trading_date.isoformat()] = {
            "buy_fills": len(buy_fills),
            "missing_prices": missing_prices,
            "missing_signal": missing_signal,
            "buckets": {
                key: {metric: summarize(values) for metric, values in metrics.items()}
                for key, metrics in sorted(buckets.items())
            },
        }
    result["TOTAL"] = {
        "buckets": {
            key: {metric: summarize(values) for metric, values in metrics.items()}
            for key, metrics in sorted(aggregate.items())
        }
    }
    return result


def main() -> int:
    args = parse_args()
    dates = [parse_date(part) for part in args.dates.split(",") if part]
    feature_paths = parse_feature_args(args.features)
    horizons = parse_horizons(args.horizons)

    url = require_env("SUPABASE_URL").rstrip("/")
    key = require_env("SUPABASE_SECRET_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        signals_by_date = {
            trading_date: fetch_signals(client, trading_date) for trading_date in dates
        }
        watchlist_by_date = {
            trading_date: fetch_watchlist(client, trading_date) for trading_date in dates
        }
        trades_by_date = (
            {trading_date: fetch_paper_trades(client, trading_date) for trading_date in dates}
            if args.actual_fills
            else {}
        )

    scanner_gate = ScannerGate(
        max_risk_penalty=args.max_risk_penalty,
        max_volume_surge=args.max_volume_surge,
        max_momentum=args.max_momentum,
    )
    if args.actual_fills:
        result = analyze_actual_fills(
            trades_by_date,
            signals_by_date,
            watchlist_by_date,
            feature_paths,
            horizons=horizons,
            scanner_gate=scanner_gate,
        )
    else:
        result = analyze(
            signals_by_date,
            watchlist_by_date,
            feature_paths,
            cooldown_seconds=args.cooldown_seconds,
            horizons=horizons,
            quantity=args.quantity,
            scanner_gate=scanner_gate,
        )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for trading_date, row in result.items():
        if "error" in row:
            print(f"{trading_date}: {row['error']}")
            continue
        if trading_date == "TOTAL":
            print("TOTAL")
        elif args.actual_fills:
            print(
                f"{trading_date}: buy_fills={row['buy_fills']} "
                f"missing_prices={row['missing_prices']} missing_signal={row['missing_signal']}"
            )
        else:
            print(
                f"{trading_date}: signals={row['signals']} deduped={row['deduped']} "
                f"missing_prices={row['missing_prices']}"
            )
        for key in (
            "ALL",
            "action=BUY",
            "action=SELL",
            "scanner_gate=pass",
            "scanner_gate=fail",
            "scanner_gate=pass,action=BUY",
            "scanner_gate=fail,action=BUY",
            "source=RULE,action=BUY",
            "source=RULE,action=SELL",
            "source=CONSENSUS,action=BUY",
            "source=CONSENSUS,action=SELL",
        ):
            metrics = row["buckets"].get(key)
            if not metrics:
                continue
            horizon_summary = " ".join(
                f"long{horizon}={metrics[f'long_{horizon}']} "
                f"short{horizon}={metrics[f'short_{horizon}']}"
                for horizon in horizons
            )
            print(f"  {key}: {horizon_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
