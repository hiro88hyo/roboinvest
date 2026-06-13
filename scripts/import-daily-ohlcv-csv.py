#!/usr/bin/env python
"""Import an exported daily_ohlcv CSV into Supabase.

Default input is the local reusable archive copied from /tmp:

    data/reference/daily_ohlcv_500bd_bydate.csv

Run with resolved Supabase env, for example:

    op run --env-file infra/env.production -- \
      uv run python scripts/import-daily-ohlcv-csv.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from universe_scanner.clients.supabase import SupabaseWriter
from universe_scanner.config import ScannerSettings
from universe_scanner.symbols import normalize_symbol_code

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "reference" / "daily_ohlcv_500bd_bydate.csv"
REQUIRED_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "turnover")


@dataclass(slots=True)
class ImportStats:
    rows_seen: int = 0
    rows_imported: int = 0
    first_date: date | None = None
    last_date: date | None = None

    def observe_date(self, value: date) -> None:
        if self.first_date is None or value < self.first_date:
            self.first_date = value
        if self.last_date is None or value > self.last_date:
            self.last_date = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize the CSV without writing to Supabase.",
    )
    return parser.parse_args()


def iter_payload_batches(
    path: Path,
    *,
    batch_size: int,
    stats: ImportStats,
) -> Iterable[list[dict[str, Any]]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV header is missing")
        missing = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

        batch: list[dict[str, Any]] = []
        for line_number, row in enumerate(reader, start=2):
            stats.rows_seen += 1
            payload = _row_to_payload(row, line_number=line_number)
            stats.observe_date(date.fromisoformat(payload["date"]))
            batch.append(payload)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def _row_to_payload(row: dict[str, str], *, line_number: int) -> dict[str, Any]:
    try:
        parsed_date = date.fromisoformat(row["date"])
        return {
            "symbol": normalize_symbol_code(row["symbol"]),
            "date": parsed_date.isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "turnover": float(row["turnover"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid daily_ohlcv row at CSV line {line_number}") from exc


async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = args.input.resolve()
    if not path.exists():
        logging.error("input CSV does not exist: %s", path)
        return 2

    stats = ImportStats()
    if args.dry_run:
        for batch in iter_payload_batches(path, batch_size=args.batch_size, stats=stats):
            stats.rows_imported += len(batch)
        _log_summary(stats, dry_run=True)
        return 0

    settings = ScannerSettings()
    async with SupabaseWriter(
        url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        timeout_seconds=args.timeout,
        upsert_chunk_size=args.batch_size,
    ) as writer:
        for batch in iter_payload_batches(path, batch_size=args.batch_size, stats=stats):
            await writer.upsert("daily_ohlcv", batch, on_conflict="symbol,date")
            stats.rows_imported += len(batch)
            if stats.rows_imported % (args.batch_size * 10) == 0:
                logging.info("imported rows=%d", stats.rows_imported)

    _log_summary(stats, dry_run=False)
    return 0


def _log_summary(stats: ImportStats, *, dry_run: bool) -> None:
    mode = "validated" if dry_run else "imported"
    logging.info(
        "%s rows=%d first_date=%s last_date=%s",
        mode,
        stats.rows_imported,
        stats.first_date,
        stats.last_date,
    )


def main() -> None:
    sys.exit(asyncio.run(amain(parse_args())))


if __name__ == "__main__":
    main()
