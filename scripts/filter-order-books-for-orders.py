#!/usr/bin/env python3
"""Filter OrderBookSnapshot JSONL to symbols present in an OrderRequest JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--books", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    symbols = read_order_symbols(args.orders)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    read_count = 0
    write_count = 0
    with args.books.open(encoding="utf-8") as src, args.output.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            read_count += 1
            raw = json.loads(line)
            if str(raw.get("symbol")) not in symbols:
                continue
            dst.write(line)
            write_count += 1

    print(
        f"symbols={len(symbols)} books_read={read_count} "
        f"books_written={write_count} output={args.output}"
    )
    return 0


def read_order_symbols(path: Path) -> set[str]:
    symbols: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            symbols.add(str(raw["symbol"]))
    return symbols


if __name__ == "__main__":
    raise SystemExit(main())
