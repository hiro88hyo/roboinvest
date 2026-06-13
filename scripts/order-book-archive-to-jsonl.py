#!/usr/bin/env python3
"""Export feature-engine book archive Parquet to OMS Paper OrderBookSnapshot JSONL."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from feature_engine.storage.book import enumerate_book_symbols, load_book_partition
from trade_contracts.market import OrderBookSnapshot


def _parse_symbols(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="order-book-archive-to-jsonl.py",
        description="feature-engine の板 Parquet archive を OrderBookSnapshot JSONL に変換する。",
    )
    parser.add_argument("--book-dir", type=Path, default=Path("./data/books"))
    parser.add_argument("--date", dest="target_date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbols", type=_parse_symbols, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    symbols = args.symbols or enumerate_book_symbols(args.book_dir, args.target_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with args.output.open("w", encoding="utf-8") as f:
        for symbol in symbols:
            df = load_book_partition(args.book_dir, symbol, args.target_date)
            if df.is_empty():
                continue
            for row in df.sort("timestamp").iter_rows(named=True):
                book = OrderBookSnapshot.model_validate(
                    {
                        "symbol": row["symbol"],
                        "timestamp": row["timestamp"],
                        "bids": json.loads(row["bids_json"]),
                        "asks": json.loads(row["asks_json"]),
                    }
                )
                f.write(book.model_dump_json())
                f.write("\n")
                count += 1

    print(
        f"exported order books: date={args.target_date.isoformat()} "
        f"symbols={len(symbols)} rows={count} output={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
