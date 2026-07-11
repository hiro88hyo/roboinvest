#!/usr/bin/env python3
"""Export J-Quants financial summaries into a local mixed-record JSONL archive.

This script is for event-driven research archives. It does not write to
Supabase and it does not normalize or infer unavailable API fields. Source rows
carry `_roboinvest_fetched_at`, the UTC timestamp at which that API response
completed. Each response ends with a synthetic `fetch_metadata` completion row,
including responses that contained zero source rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
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
                for target_date, records, fetched_at in batch_results:
                    for record in records:
                        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    f.write(
                        json.dumps(
                            fetch_metadata_record(
                                target_date=target_date,
                                fetched_at=fetched_at,
                                row_count=len(records),
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
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
) -> tuple[date, list[dict[str, Any]], datetime]:
    rows = await jquants.financial_summaries_by_date(target_date)
    fetched_at = datetime.now(UTC)
    return target_date, attach_fetch_metadata(rows, fetched_at=fetched_at), fetched_at


def attach_fetch_metadata(
    rows: list[dict[str, Any]],
    *,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    """Copy raw rows and attach response-level receipt provenance."""

    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    value = fetched_at.astimezone(UTC).isoformat()
    return [{**row, "_roboinvest_fetched_at": value} for row in rows]


def fetch_metadata_record(
    *,
    target_date: date,
    fetched_at: datetime,
    row_count: int,
) -> dict[str, Any]:
    """Record a dated fetch even when the API returned zero rows."""

    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    return {
        "_roboinvest_record_type": "fetch_metadata",
        "_roboinvest_target_date": target_date.isoformat(),
        "_roboinvest_fetched_at": fetched_at.astimezone(UTC).isoformat(),
        "_roboinvest_row_count": row_count,
    }


def read_existing_disclosed_dates(path: Path) -> set[str]:
    """Return dates with a completed fetch, plus explicitly legacy rows.

    New exporter rows carry ``_roboinvest_fetched_at`` and are considered
    complete only after their trailing fetch-metadata marker is present.  Raw
    rows from archives created before receipt metadata existed retain the old
    resume behavior so migrating an existing archive does not refetch years of
    data.
    """

    if not path.exists() or path.stat().st_size == 0:
        return set()
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        rows.append(json.loads(line))

    legacy_dates = {
        value
        for row in rows
        if row.get("_roboinvest_record_type") != "fetch_metadata"
        and row.get("_roboinvest_fetched_at") in (None, "")
        and (value := _financial_row_date_text(row)) is not None
    }
    latest_markers: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("_roboinvest_record_type") != "fetch_metadata":
            continue
        target = str(row.get("_roboinvest_target_date", ""))[:10]
        if target:
            latest_markers[target] = row

    completed_dates: set[str] = set()
    for target, marker in latest_markers.items():
        fetched_at = _metadata_timestamp(marker.get("_roboinvest_fetched_at"))
        try:
            expected_count = int(marker.get("_roboinvest_row_count"))
        except (TypeError, ValueError):
            continue
        if fetched_at is None or expected_count < 0:
            continue
        actual_count = sum(
            1
            for row in rows
            if row.get("_roboinvest_record_type") != "fetch_metadata"
            and _financial_row_date_text(row) == target
            and _metadata_timestamp(row.get("_roboinvest_fetched_at")) == fetched_at
        )
        if actual_count == expected_count:
            completed_dates.add(target)

    return (legacy_dates - latest_markers.keys()) | completed_dates


def _financial_row_date_text(row: dict[str, Any]) -> str | None:
    value = row.get("DisclosedDate") or row.get("DiscDate") or row.get("Date") or row.get("date")
    return None if value in (None, "") else str(value)[:10]


def _metadata_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def iter_dates(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
