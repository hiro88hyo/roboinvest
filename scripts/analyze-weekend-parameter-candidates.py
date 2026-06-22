#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Explore scanner and intraday parameters across recent paper-trading days.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/analyze-weekend-parameter-candidates.py \\
        --dates 2026-06-15,2026-06-16,2026-06-19 \\
        --features 2026-06-16=out/paper-archive-2026-06-16/features.jsonl \\
        --features 2026-06-19=out/param-search-2026-06-19/features.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
from bisect import bisect_left
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import httpx

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    side: str
    quantity: int
    price: Decimal
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class ScannerCandidate:
    max_score: float
    max_risk: float
    max_momentum: float
    max_volume_surge: float
    max_latest_close: float


@dataclass(frozen=True, slots=True)
class FeatureRow:
    symbol: str
    timestamp: datetime
    price: float
    rsi: float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    spread_bps: float | None
    spread_ticks: float | None
    ask_depth_5: int | None
    book_imbalance_5: float | None
    minutes_from_open: int | None
    minutes_to_close: int | None
    book_age_seconds: float | None


@dataclass(frozen=True, slots=True)
class IntradayParams:
    rsi_buy: float
    bollinger_tolerance: float
    max_spread_ticks: float
    min_ask_depth_5: int
    min_book_imbalance_5: float
    min_minutes_from_open: int
    min_minutes_to_close: int
    max_book_age_seconds: float | None
    max_price: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates",
        required=True,
        help="Comma-separated JST trading dates, e.g. 2026-06-15,2026-06-16,2026-06-19.",
    )
    parser.add_argument(
        "--features",
        action="append",
        default=[],
        help="DATE=PATH mapping for collected feature JSONL. May be repeated.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--top", type=int, default=12)
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


def fetch_trades(client: httpx.Client, trading_date: date) -> list[Trade]:
    start, end = date_bounds_jst(trading_date)
    rows = get_rows(
        client,
        "/rest/v1/trades_paper",
        {
            "select": "executed_at,symbol,side,quantity,price",
            "and": f"(executed_at.gte.{start.isoformat()},executed_at.lt.{end.isoformat()})",
            "order": "executed_at.asc",
        },
    )
    return [
        Trade(
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            quantity=int(row["quantity"]),
            price=Decimal(str(row["price"])),
            executed_at=datetime.fromisoformat(str(row["executed_at"]).replace("Z", "+00:00")),
        )
        for row in rows
    ]


def fetch_watchlist(client: httpx.Client, trading_date: date) -> list[dict[str, Any]]:
    return get_rows(
        client,
        "/rest/v1/watchlist",
        {
            "select": "symbol,symbol_name,score,selected_reasons",
            "valid_date": f"eq.{trading_date.isoformat()}",
            "order": "score.desc",
        },
    )


def realized_by_symbol(trades: list[Trade]) -> dict[str, Decimal]:
    lots: defaultdict[str, deque[tuple[int, Decimal]]] = defaultdict(deque)
    realized: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in trades:
        if trade.side == "BUY":
            lots[trade.symbol].append((trade.quantity, trade.price))
            continue
        remaining = trade.quantity
        while remaining > 0 and lots[trade.symbol]:
            lot_qty, lot_price = lots[trade.symbol].popleft()
            matched = min(remaining, lot_qty)
            realized[trade.symbol] += (trade.price - lot_price) * matched
            remaining -= matched
            if lot_qty > matched:
                lots[trade.symbol].appendleft((lot_qty - matched, lot_price))
    return dict(realized)


def scanner_rows(
    trading_date: date,
    watchlist: list[dict[str, Any]],
    pnl_by_symbol: dict[str, Decimal],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, row in enumerate(watchlist, start=1):
        reasons = row.get("selected_reasons") or {}
        symbol = str(row["symbol"])
        rows.append(
            {
                "date": trading_date.isoformat(),
                "symbol": symbol,
                "rank": rank,
                "score": float(row["score"]),
                "risk": float(reasons.get("risk_penalty", 0.0)),
                "momentum": float(reasons.get("momentum", 0.0)),
                "volume_surge": float(reasons.get("volume_surge", 1.0)),
                "latest_close": float(reasons.get("latest_close") or 0.0),
                "pnl": int(pnl_by_symbol.get(symbol, Decimal("0"))),
                "traded": symbol in pnl_by_symbol,
            }
        )
    return rows


def scanner_grid() -> list[ScannerCandidate]:
    return [
        ScannerCandidate(score, risk, momentum, surge, close)
        for score in (3.0, 4.0, 5.0, 6.0, 8.0, 99.0)
        for risk in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 99.0)
        for momentum in (0.15, 0.25, 0.4, 0.6, 0.8, 99.0)
        for surge in (1.2, 1.5, 1.8, 2.1, 99.0)
        for close in (1000.0, 2000.0, 3000.0, 5000.0, 99999.0)
    ]


def evaluate_scanner(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    dates = {str(row["date"]) for row in rows}
    for params in scanner_grid():
        kept = [
            row
            for row in rows
            if row["score"] <= params.max_score
            and row["risk"] <= params.max_risk
            and row["momentum"] <= params.max_momentum
            and row["volume_surge"] <= params.max_volume_surge
            and row["latest_close"] <= params.max_latest_close
        ]
        traded = [row for row in kept if row["traded"]]
        if len(traded) < 4:
            continue
        by_date = {
            trading_date: sum(row["pnl"] for row in traded if row["date"] == trading_date)
            for trading_date in sorted(dates)
        }
        out.append(
            {
                **asdict(params),
                "pnl": sum(by_date.values()),
                "traded_symbols": len(traded),
                "watchlist_rows": len(kept),
                "positive_days": sum(1 for value in by_date.values() if value > 0),
                "negative_days": sum(1 for value in by_date.values() if value < 0),
                "by_date": by_date,
            }
        )
    out.sort(
        key=lambda item: (item["pnl"], item["positive_days"], -item["negative_days"]),
        reverse=True,
    )
    return out


def read_features(path: Path) -> list[FeatureRow]:
    rows: list[FeatureRow] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            timestamp = datetime.fromisoformat(raw["timestamp"])
            book_timestamp = None
            if raw.get("order_book") and raw["order_book"].get("timestamp"):
                book_timestamp = datetime.fromisoformat(raw["order_book"]["timestamp"])
            rows.append(
                FeatureRow(
                    symbol=str(raw["symbol"]),
                    timestamp=timestamp,
                    price=float(raw["price"]),
                    rsi=to_float(raw.get("rsi")),
                    bollinger_upper=to_float(raw.get("bollinger_upper")),
                    bollinger_lower=to_float(raw.get("bollinger_lower")),
                    spread_bps=to_float(raw.get("spread_bps")),
                    spread_ticks=to_float(raw.get("spread_ticks")),
                    ask_depth_5=to_int(raw.get("ask_depth_5")),
                    book_imbalance_5=to_float(raw.get("book_imbalance_5")),
                    minutes_from_open=to_int(raw.get("minutes_from_open")),
                    minutes_to_close=to_int(raw.get("minutes_to_close")),
                    book_age_seconds=None
                    if book_timestamp is None
                    else max(0.0, (timestamp - book_timestamp).total_seconds()),
                )
            )
    return sorted(rows, key=lambda row: (row.symbol, row.timestamp))


def to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def intraday_grid() -> list[IntradayParams]:
    return [
        IntradayParams(rsi, 0.15, 1.0, depth, imbalance, min_open, 60, book_age, max_price)
        for rsi in (15.0, 20.0, 25.0, 30.0)
        for depth in (300, 1000)
        for imbalance in (-0.5, 0.0)
        for min_open in (15, 30, 45)
        for book_age in (30.0, 300.0, None)
        for max_price in (2000.0, 3000.0, 5000.0)
    ]


def feature_timeline(rows: list[FeatureRow]) -> dict[str, tuple[list[datetime], list[float]]]:
    out: dict[str, tuple[list[datetime], list[float]]] = {}
    for row in rows:
        if row.symbol not in out:
            out[row.symbol] = ([], [])
        out[row.symbol][0].append(row.timestamp)
        out[row.symbol][1].append(row.price)
    return out


def forward_price(
    row: FeatureRow,
    timeline: dict[str, tuple[list[datetime], list[float]]],
    minutes: int,
) -> float | None:
    times, prices = timeline[row.symbol]
    index = bisect_left(times, row.timestamp + timedelta(minutes=minutes))
    if index >= len(prices):
        return None
    return prices[index]


def is_bollinger_buy(row: FeatureRow, tolerance: float) -> bool:
    if row.bollinger_upper is None or row.bollinger_lower is None:
        return False
    width = row.bollinger_upper - row.bollinger_lower
    return width > 0 and row.price < row.bollinger_lower - width * tolerance


def passes_intraday(row: FeatureRow, params: IntradayParams) -> bool:
    if row.price > params.max_price:
        return False
    if not (
        (row.rsi is not None and row.rsi <= params.rsi_buy)
        or is_bollinger_buy(row, params.bollinger_tolerance)
    ):
        return False
    if row.spread_bps is None or row.spread_bps > 30.0:
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


def dedupe_features(rows: list[FeatureRow], *, cooldown_seconds: int) -> list[FeatureRow]:
    out: list[FeatureRow] = []
    last_by_symbol: dict[str, datetime] = {}
    for row in sorted(rows, key=lambda item: item.timestamp):
        last = last_by_symbol.get(row.symbol)
        if last is not None and (row.timestamp - last).total_seconds() < cooldown_seconds:
            continue
        out.append(row)
        last_by_symbol[row.symbol] = row.timestamp
    return out


def evaluate_intraday(feature_paths: dict[date, Path]) -> list[dict[str, Any]]:
    data = {trading_date: read_features(path) for trading_date, path in feature_paths.items()}
    timelines = {trading_date: feature_timeline(rows) for trading_date, rows in data.items()}
    out: list[dict[str, Any]] = []
    for params in intraday_grid():
        by_date: dict[str, dict[str, Any]] = {}
        total_15 = 0.0
        total_30 = 0.0
        count = 0
        for trading_date, rows in data.items():
            candidates = [row for row in rows if passes_intraday(row, params)]
            deduped = dedupe_features(candidates, cooldown_seconds=300)
            pnl_15: list[float] = []
            pnl_30: list[float] = []
            for row in deduped:
                price_15 = forward_price(row, timelines[trading_date], 15)
                price_30 = forward_price(row, timelines[trading_date], 30)
                if price_15 is not None:
                    pnl_15.append((price_15 - row.price) * 100)
                if price_30 is not None:
                    pnl_30.append((price_30 - row.price) * 100)
            by_date[trading_date.isoformat()] = {
                "count": len(deduped),
                "symbols": len({row.symbol for row in deduped}),
                "pnl_15": round(sum(pnl_15)),
                "pnl_30": round(sum(pnl_30)),
                "avg_30": round(mean(pnl_30), 1) if pnl_30 else 0.0,
            }
            total_15 += sum(pnl_15)
            total_30 += sum(pnl_30)
            count += len(deduped)
        if count < 20:
            continue
        out.append(
            {
                **asdict(params),
                "count": count,
                "pnl_15": round(total_15),
                "pnl_30": round(total_30),
                "by_date": by_date,
            }
        )
    out.sort(key=lambda item: (item["pnl_30"], item["pnl_15"]), reverse=True)
    return out


def main() -> int:
    args = parse_args()
    dates = [parse_date(part) for part in args.dates.split(",") if part]
    feature_paths = parse_feature_args(args.features)

    url = require_env("SUPABASE_URL").rstrip("/")
    key = require_env("SUPABASE_SECRET_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    scanner_input: list[dict[str, Any]] = []
    daily_actual: dict[str, Any] = {}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        for trading_date in dates:
            trades = fetch_trades(client, trading_date)
            pnl_by_symbol = realized_by_symbol(trades)
            watchlist = fetch_watchlist(client, trading_date)
            scanner_input.extend(scanner_rows(trading_date, watchlist, pnl_by_symbol))
            daily_actual[trading_date.isoformat()] = {
                "trades": len(trades),
                "pnl": int(sum(pnl_by_symbol.values())),
                "symbols": len(pnl_by_symbol),
            }

    result = {
        "dates": [trading_date.isoformat() for trading_date in dates],
        "daily_actual": daily_actual,
        "scanner_top": evaluate_scanner(scanner_input)[: args.top],
        "intraday_top": evaluate_intraday(feature_paths)[: args.top] if feature_paths else [],
        "feature_dates": [trading_date.isoformat() for trading_date in sorted(feature_paths)],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"dates={','.join(result['dates'])}")
    print("actual")
    for trading_date, row in daily_actual.items():
        print(f"  {trading_date}: trades={row['trades']} symbols={row['symbols']} pnl={row['pnl']}")
    print()
    print("scanner candidates")
    for row in result["scanner_top"]:
        print(
            "  "
            f"pnl={row['pnl']} traded={row['traded_symbols']} pos_days={row['positive_days']} "
            f"neg_days={row['negative_days']} score<={row['max_score']} "
            f"risk<={row['max_risk']} mom<={row['max_momentum']} "
            f"surge<={row['max_volume_surge']} close<={row['max_latest_close']} "
            f"by_date={row['by_date']}"
        )
    print()
    print(f"intraday candidates feature_dates={','.join(result['feature_dates'])}")
    for row in result["intraday_top"]:
        print(
            "  "
            f"pnl30={row['pnl_30']} pnl15={row['pnl_15']} count={row['count']} "
            f"rsi<={row['rsi_buy']} open>={row['min_minutes_from_open']} "
            f"price<={row['max_price']} depth>={row['min_ask_depth_5']} "
            f"imb>={row['min_book_imbalance_5']} book_age<={row['max_book_age_seconds']} "
            f"by_date={row['by_date']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
