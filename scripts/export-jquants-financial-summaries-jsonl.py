#!/usr/bin/env python3
"""Export J-Quants financial summary rows into a local JSONL archive.

This script is for event-driven research archives. It does not write to
Supabase and it does not normalize or infer unavailable API fields. Each output
line is one raw J-Quants row as returned by the configured API version.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from universe_scanner.calendar import is_tse_business_day
from universe_scanner.clients.jquants import JQuantsApiVersion, JQuantsClient

logger = logging.getLogger("export-jquants-financial-summaries-jsonl")


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(export_archive(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip disclosed dates already present in the output JSONL.",
    )
    parser.add_argument(
        "--limit-dates",
        type=int,
        default=None,
        help="Fetch at most this many missing business dates. Useful for smoke tests.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--log-every-dates",
        type=int,
        default=1,
        help="Log progress every N completed dates.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of dates to fetch concurrently. Keep modest to avoid API throttling.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("JQUANTS_API_BASE", "https://api.jquants.com/v2"),
    )
    parser.add_argument(
        "--api-version",
        choices=("v1", "v2"),
        default=os.getenv("JQUANTS_API_VERSION", "v2"),
    )
    return parser


async def export_archive(args: argparse.Namespace) -> int:
    if args.start_date > args.end_date:
        raise ValueError("--start-date must be <= --end-date")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be positive")
    if args.log_every_dates <= 0:
        raise ValueError("--log-every-dates must be positive")

    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    existing_dates = read_existing_disclosed_dates(output) if args.resume else set()
    target_dates = [
        item
        for item in iter_dates(args.start_date, args.end_date)
        if is_tse_business_day(item) and item.isoformat() not in existing_dates
    ]
    if args.limit_dates is not None:
        target_dates = target_dates[: args.limit_dates]

    logger.info(
        "financial_summaries export: output=%s start=%s end=%s missing_business_dates=%d resume=%s",
        output,
        args.start_date,
        args.end_date,
        len(target_dates),
        args.resume,
    )
    if not target_dates:
        return 0

    client = JQuantsClient(
        api_key=os.getenv("JQUANTS_API_KEY"),
        refresh_token=os.getenv("JQUANTS_REFRESH_TOKEN"),
        api_version=JQuantsApiVersion(args.api_version),
        base_url=args.api_base,
        timeout_seconds=args.timeout_seconds,
    )
    total_rows = 0
    async with client as jquants:
        with output.open("a", encoding="utf-8") as f:
            completed = 0
            for batch_start in range(0, len(target_dates), args.concurrency):
                batch_dates = target_dates[batch_start : batch_start + args.concurrency]
                batch_results = await asyncio.gather(
                    *(
                        fetch_financial_summary_records(jquants, target_date)
                        for target_date in batch_dates
                    )
                )
                for target_date, records in batch_results:
                    for record in records:
                        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    completed += 1
                    total_rows += len(records)
                    if (
                        completed == 1
                        or completed == len(target_dates)
                        or completed % args.log_every_dates == 0
                    ):
                        logger.info(
                            "financial_summaries export: %s fetched=%d progress=%d/%d "
                            "total_rows=%d",
                            target_date,
                            len(records),
                            completed,
                            len(target_dates),
                            total_rows,
                        )
                f.flush()
                if args.sleep_seconds > 0:
                    await asyncio.sleep(args.sleep_seconds)
    logger.info("financial_summaries export: complete rows_written=%d", total_rows)
    return 0


async def fetch_financial_summary_records(
    jquants: JQuantsClient,
    target_date: date,
) -> tuple[date, list[dict[str, Any]]]:
    rows = await jquants.financial_summaries_by_date(target_date)
    return target_date, rows


def read_existing_disclosed_dates(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    dates: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        value = (
            row.get("DisclosedDate") or row.get("DiscDate") or row.get("Date") or row.get("date")
        )
        if value:
            dates.add(str(value)[:10])
    return dates


def iter_dates(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
