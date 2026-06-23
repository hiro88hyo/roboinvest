#!/usr/bin/env python3
"""Add relative-momentum fields to archived ProcessedFeatures JSONL.

This is a replay helper for old archives that predate the Feature Engine
relative-momentum fields. It preserves every original field and adds:

- return_from_open_bps
- intraday_peer_percentile
- intraday_high_price
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Row:
    index: int
    symbol: str
    trading_date: date
    timestamp: datetime
    minute: int | None
    price: Decimal
    raw: dict[str, Any]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrich ProcessedFeatures JSONL with relative momentum fields.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = read_rows(args.input)
    enriched = enrich(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for row in sorted(enriched, key=lambda item: item.index):
            f.write(json.dumps(row.raw, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"rows={len(enriched)}")
    print(f"output={args.output}")
    return 0


def read_rows(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(encoding="utf-8") as f:
        for index, line in enumerate(f):
            if not line.strip():
                continue
            raw = json.loads(line)
            timestamp = datetime.fromisoformat(raw["timestamp"])
            rows.append(
                Row(
                    index=index,
                    symbol=str(raw["symbol"]),
                    trading_date=timestamp.date(),
                    timestamp=timestamp,
                    minute=to_int(raw.get("minutes_from_open")),
                    price=Decimal(str(raw["price"])),
                    raw=raw,
                )
            )
    return rows


def enrich(rows: list[Row]) -> list[Row]:
    open_prices: dict[tuple[date, str], Decimal] = {}
    intraday_highs: dict[tuple[date, str], Decimal] = {}
    returns_by_minute: dict[tuple[date, int], dict[str, Decimal]] = {}

    ordered = sorted(rows, key=lambda item: (item.trading_date, item.timestamp, item.symbol))
    for row in ordered:
        key = (row.trading_date, row.symbol)
        open_price = open_prices.setdefault(key, row.price)
        high_price = max(intraday_highs.get(key, row.price), row.price)
        intraday_highs[key] = high_price
        return_from_open_bps = None
        if open_price > 0:
            return_from_open_bps = ((row.price - open_price) / open_price) * Decimal("10000")
            if row.minute is not None:
                returns_by_minute.setdefault((row.trading_date, row.minute), {})[row.symbol] = (
                    return_from_open_bps
                )
        row.raw["return_from_open_bps"] = (
            str(return_from_open_bps) if return_from_open_bps is not None else None
        )
        row.raw["intraday_high_price"] = str(high_price)

    for row in ordered:
        percentile = None
        if row.minute is not None:
            values = returns_by_minute.get((row.trading_date, row.minute), {})
            own = values.get(row.symbol)
            if own is not None and len(values) >= 2:
                lower_or_equal = sum(1 for value in values.values() if value <= own) - 1
                percentile = Decimal(lower_or_equal) / Decimal(len(values) - 1)
        row.raw["intraday_peer_percentile"] = str(percentile) if percentile is not None else None
    return rows


def to_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
