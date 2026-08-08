#!/usr/bin/env python3
"""Export immutable J-Quants inputs for paper-inspired liquidity research.

The archive is deliberately separate from Supabase ``daily_ohlcv`` and the
production Universe Scanner cache.  It preserves raw v2 API rows, including
adjusted prices/volume and historical master fields, with response receipt
timestamps, completion markers, payload hashes, and a file manifest.

This script only acquires research inputs.  It does not calculate LIQIMP1M,
rank securities, label returns, or run a backtest.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from universe_scanner.calendar import is_tse_business_day
from universe_scanner.clients.jquants import JQuantsApiVersion, JQuantsClient

ARCHIVE_SCHEMA_VERSION = "jquants_liquidity_research_archive_v1"
BARS_DATASET = "equities_bars_daily"
MASTER_DATASET = "equities_master_month_end"
BARS_FILENAME = "bars-daily-raw.jsonl"
MASTER_FILENAME = "master-month-end-raw.jsonl"
MANIFEST_FILENAME = "manifest.json"

PROVENANCE_KEYS = {
    "_roboinvest_record_type",
    "_roboinvest_archive_schema_version",
    "_roboinvest_dataset",
    "_roboinvest_fetch_id",
    "_roboinvest_target_date",
    "_roboinvest_source_received_at",
}
BARS_REQUIRED_FIELDS = {
    "Date",
    "Code",
    "Va",
    "AdjFactor",
    "AdjC",
    "AdjVo",
}
MASTER_REQUIRED_FIELDS = {
    "Date",
    "Code",
    "ProdCat",
    "Mkt",
    "S17",
    "S33",
    "ScaleCat",
}

logger = logging.getLogger("export-jquants-liquidity-research")


@dataclass(slots=True)
class _FetchDigest:
    dataset: str
    target_date: str
    source_received_at: str
    digest: Any = field(default_factory=hashlib.sha256)
    row_count: int = 0

    def __post_init__(self) -> None:
        self.digest.update(b"[")

    def update(self, row: dict[str, Any]) -> None:
        if self.row_count:
            self.digest.update(b",")
        self.digest.update(_canonical_json_bytes(strip_provenance(row)))
        self.row_count += 1

    def hexdigest(self) -> str:
        copy = self.digest.copy()
        copy.update(b"]")
        return copy.hexdigest()


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    source_row_count: int
    metadata_row_count: int
    completed_source_row_count: int
    completed_fetch_count: int
    completed_dates: frozenset[str]
    duplicate_completed_date_count: int


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(export_archive(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append only dates without a valid completion marker.",
    )
    parser.add_argument("--limit-bar-dates", type=int)
    parser.add_argument("--limit-master-dates", type=int)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of dated API responses to fetch concurrently.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("JQUANTS_API_BASE", "https://api.jquants.com/v2"),
    )
    parser.add_argument(
        "--api-version",
        choices=("v2",),
        default=os.getenv("JQUANTS_API_VERSION", "v2"),
        help="Only v2 exposes the fields required by this archive.",
    )
    return parser


async def export_archive(args: argparse.Namespace) -> int:
    validate_args(args)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    bars_path = output_dir / BARS_FILENAME
    master_path = output_dir / MASTER_FILENAME
    _ensure_safe_append(bars_path, resume=args.resume)
    _ensure_safe_append(master_path, resume=args.resume)

    completed_bar_dates = inspect_archive(bars_path).completed_dates if args.resume else set()
    completed_master_dates = inspect_archive(master_path).completed_dates if args.resume else set()
    bar_dates = [
        item
        for item in iter_dates(args.start_date, args.end_date)
        if is_tse_business_day(item) and item.isoformat() not in completed_bar_dates
    ]
    master_dates = [
        item
        for item in month_end_business_dates(args.start_date, args.end_date)
        if item.isoformat() not in completed_master_dates
    ]
    if args.limit_bar_dates is not None:
        bar_dates = bar_dates[: args.limit_bar_dates]
    if args.limit_master_dates is not None:
        master_dates = master_dates[: args.limit_master_dates]

    logger.info(
        "liquidity archive: bars_missing=%d master_missing=%d output=%s resume=%s",
        len(bar_dates),
        len(master_dates),
        output_dir,
        args.resume,
    )
    client = JQuantsClient(
        api_key=os.getenv("JQUANTS_API_KEY"),
        refresh_token=os.getenv("JQUANTS_REFRESH_TOKEN"),
        api_version=JQuantsApiVersion.V2,
        base_url=args.api_base,
        timeout_seconds=args.timeout_seconds,
    )
    if bar_dates or master_dates:
        async with client as jquants:
            await export_dated_responses(
                path=bars_path,
                target_dates=bar_dates,
                dataset=BARS_DATASET,
                fetch=lambda target: jquants.daily_quotes(target_date=target),
                required_fields=BARS_REQUIRED_FIELDS,
                concurrency=args.concurrency,
                sleep_seconds=args.sleep_seconds,
            )
            await export_dated_responses(
                path=master_path,
                target_dates=master_dates,
                dataset=MASTER_DATASET,
                fetch=lambda target: jquants.listed_info(as_of=target),
                required_fields=MASTER_REQUIRED_FIELDS,
                concurrency=args.concurrency,
                sleep_seconds=args.sleep_seconds,
            )

    manifest = build_manifest(
        output_dir=output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        api_base=args.api_base,
        api_version=args.api_version,
    )
    write_manifest_atomic(output_dir / MANIFEST_FILENAME, manifest)
    logger.info(
        "liquidity archive: complete bars_dates=%d master_dates=%d manifest=%s",
        manifest["files"][BARS_FILENAME]["unique_completed_date_count"],
        manifest["files"][MASTER_FILENAME]["unique_completed_date_count"],
        output_dir / MANIFEST_FILENAME,
    )
    return 0


def validate_args(args: argparse.Namespace) -> None:
    if args.start_date > args.end_date:
        raise ValueError("--start-date must be <= --end-date")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be positive")
    if args.sleep_seconds < 0:
        raise ValueError("--sleep-seconds must be non-negative")
    for name in ("limit_bar_dates", "limit_master_dates"):
        value = getattr(args, name)
        if value is not None and value < 0:
            raise ValueError(f"--{name.replace('_', '-')} must be non-negative")
    if args.api_version != "v2":
        raise ValueError("liquidity research archive requires J-Quants v2")


async def export_dated_responses(
    *,
    path: Path,
    target_dates: Sequence[date],
    dataset: str,
    fetch: Any,
    required_fields: set[str],
    concurrency: int,
    sleep_seconds: float,
) -> None:
    if not target_dates:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        completed = 0
        for start in range(0, len(target_dates), concurrency):
            batch_dates = target_dates[start : start + concurrency]
            results = await asyncio.gather(
                *(fetch_dated_response(fetch, item) for item in batch_dates)
            )
            for target_date, rows, source_received_at in results:
                validate_response_fields(
                    rows,
                    required_fields=required_fields,
                    dataset=dataset,
                    target_date=target_date,
                )
                write_fetch_records(
                    output,
                    rows=rows,
                    dataset=dataset,
                    target_date=target_date,
                    source_received_at=source_received_at,
                )
                completed += 1
                logger.info(
                    "liquidity archive: dataset=%s date=%s rows=%d progress=%d/%d",
                    dataset,
                    target_date,
                    len(rows),
                    completed,
                    len(target_dates),
                )
            output.flush()
            os.fsync(output.fileno())
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)


async def fetch_dated_response(
    fetch: Any, target_date: date
) -> tuple[date, list[dict[str, Any]], datetime]:
    rows: list[dict[str, Any]] = await fetch(target_date)
    return target_date, rows, datetime.now(UTC)


def validate_response_fields(
    rows: Sequence[dict[str, Any]],
    *,
    required_fields: set[str],
    dataset: str,
    target_date: date,
) -> None:
    for index, row in enumerate(rows):
        missing = sorted(required_fields - row.keys())
        if missing:
            raise ValueError(
                f"{dataset} response {target_date} row {index} missing fields: {missing}"
            )


def write_fetch_records(
    output: TextIO,
    *,
    rows: Sequence[dict[str, Any]],
    dataset: str,
    target_date: date,
    source_received_at: datetime,
    fetch_id: str | None = None,
) -> dict[str, Any]:
    if source_received_at.tzinfo is None:
        raise ValueError("source_received_at must be timezone-aware")
    fetch_id = uuid.uuid4().hex if fetch_id is None else fetch_id
    receipt = source_received_at.astimezone(UTC).isoformat()
    for row in rows:
        tagged = {
            **row,
            "_roboinvest_record_type": "source",
            "_roboinvest_archive_schema_version": ARCHIVE_SCHEMA_VERSION,
            "_roboinvest_dataset": dataset,
            "_roboinvest_fetch_id": fetch_id,
            "_roboinvest_target_date": target_date.isoformat(),
            "_roboinvest_source_received_at": receipt,
        }
        output.write(json.dumps(tagged, ensure_ascii=False, sort_keys=True) + "\n")
    marker = fetch_metadata_record(
        rows=rows,
        dataset=dataset,
        target_date=target_date,
        source_received_at=source_received_at,
        fetch_id=fetch_id,
    )
    output.write(json.dumps(marker, ensure_ascii=False, sort_keys=True) + "\n")
    return marker


def fetch_metadata_record(
    *,
    rows: Sequence[dict[str, Any]],
    dataset: str,
    target_date: date,
    source_received_at: datetime,
    fetch_id: str,
) -> dict[str, Any]:
    if source_received_at.tzinfo is None:
        raise ValueError("source_received_at must be timezone-aware")
    return {
        "_roboinvest_record_type": "fetch_metadata",
        "_roboinvest_archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "_roboinvest_dataset": dataset,
        "_roboinvest_fetch_id": fetch_id,
        "_roboinvest_target_date": target_date.isoformat(),
        "_roboinvest_source_received_at": source_received_at.astimezone(UTC).isoformat(),
        "_roboinvest_row_count": len(rows),
        "_roboinvest_source_payload_sha256": source_payload_sha256(rows),
    }


def source_payload_sha256(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, row in enumerate(rows):
        if index:
            digest.update(b",")
        digest.update(_canonical_json_bytes(strip_provenance(row)))
    digest.update(b"]")
    return digest.hexdigest()


def strip_provenance(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in PROVENANCE_KEYS}


def inspect_archive(path: Path) -> ArchiveInspection:
    if not path.exists() or path.stat().st_size == 0:
        return ArchiveInspection(0, 0, 0, 0, frozenset(), 0)
    states: dict[str, _FetchDigest] = {}
    completed_dates: set[str] = set()
    completed_date_counts: dict[str, int] = {}
    source_row_count = 0
    metadata_row_count = 0
    completed_source_rows = 0
    completed_fetches = 0
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            record_type = row.get("_roboinvest_record_type")
            if record_type == "source":
                source_row_count += 1
                _update_fetch_digest(states, row, path=path, line_number=line_number)
                continue
            if record_type != "fetch_metadata":
                raise ValueError(f"unknown record type at {path}:{line_number}")
            metadata_row_count += 1
            fetch_id = str(row.get("_roboinvest_fetch_id") or "")
            state = states.pop(fetch_id, None)
            valid_count = _non_negative_int(row.get("_roboinvest_row_count"))
            if state is None:
                state = _FetchDigest(
                    dataset=str(row.get("_roboinvest_dataset") or ""),
                    target_date=str(row.get("_roboinvest_target_date") or ""),
                    source_received_at=str(row.get("_roboinvest_source_received_at") or ""),
                )
            if not _marker_matches_state(row, state=state, row_count=valid_count):
                continue
            target = state.target_date
            completed_dates.add(target)
            completed_date_counts[target] = completed_date_counts.get(target, 0) + 1
            completed_source_rows += state.row_count
            completed_fetches += 1
    return ArchiveInspection(
        source_row_count=source_row_count,
        metadata_row_count=metadata_row_count,
        completed_source_row_count=completed_source_rows,
        completed_fetch_count=completed_fetches,
        completed_dates=frozenset(completed_dates),
        duplicate_completed_date_count=sum(
            count - 1 for count in completed_date_counts.values() if count > 1
        ),
    )


def _update_fetch_digest(
    states: dict[str, _FetchDigest],
    row: dict[str, Any],
    *,
    path: Path,
    line_number: int,
) -> None:
    fetch_id = str(row.get("_roboinvest_fetch_id") or "")
    dataset = str(row.get("_roboinvest_dataset") or "")
    target = str(row.get("_roboinvest_target_date") or "")
    receipt = str(row.get("_roboinvest_source_received_at") or "")
    if not fetch_id or not dataset or not target or not receipt:
        raise ValueError(f"source provenance missing at {path}:{line_number}")
    state = states.get(fetch_id)
    if state is None:
        state = _FetchDigest(dataset=dataset, target_date=target, source_received_at=receipt)
        states[fetch_id] = state
    elif (state.dataset, state.target_date, state.source_received_at) != (
        dataset,
        target,
        receipt,
    ):
        raise ValueError(f"fetch provenance drift at {path}:{line_number}")
    state.update(row)


def _marker_matches_state(
    marker: dict[str, Any],
    *,
    state: _FetchDigest,
    row_count: int | None,
) -> bool:
    return (
        marker.get("_roboinvest_archive_schema_version") == ARCHIVE_SCHEMA_VERSION
        and marker.get("_roboinvest_dataset") == state.dataset
        and marker.get("_roboinvest_target_date") == state.target_date
        and marker.get("_roboinvest_source_received_at") == state.source_received_at
        and row_count is not None
        and row_count == state.row_count
        and marker.get("_roboinvest_source_payload_sha256") == state.hexdigest()
    )


def build_manifest(
    *,
    output_dir: Path,
    start_date: date,
    end_date: date,
    api_base: str,
    api_version: str,
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for filename, dataset in (
        (BARS_FILENAME, BARS_DATASET),
        (MASTER_FILENAME, MASTER_DATASET),
    ):
        path = output_dir / filename
        inspection = inspect_archive(path)
        ordered_dates = sorted(inspection.completed_dates)
        files[filename] = {
            "dataset": dataset,
            "sha256": file_sha256(path),
            "byte_size": path.stat().st_size if path.exists() else 0,
            "source_row_count": inspection.source_row_count,
            "metadata_row_count": inspection.metadata_row_count,
            "completed_source_row_count": inspection.completed_source_row_count,
            "completed_fetch_count": inspection.completed_fetch_count,
            "unique_completed_date_count": len(ordered_dates),
            "duplicate_completed_date_count": inspection.duplicate_completed_date_count,
            "first_completed_date": ordered_dates[0] if ordered_dates else None,
            "last_completed_date": ordered_dates[-1] if ordered_dates else None,
        }
    return {
        "manifest_version": 1,
        "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
        "research_only": True,
        "paper_live_enabled": False,
        "source_fidelity": "raw_api_rows_with_response_receipt_provenance",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "jquants": {
            "api_version": api_version,
            "api_base": api_base,
            "declared_plan": os.getenv("JQUANTS_PLAN"),
        },
        "files": files,
    }


def write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def month_end_business_dates(start: date, end: date) -> list[date]:
    if start > end:
        return []
    out: list[date] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        next_month = _first_day_of_next_month(cursor)
        candidate = next_month - timedelta(days=1)
        while not is_tse_business_day(candidate):
            candidate -= timedelta(days=1)
        if start <= candidate <= end:
            out.append(candidate)
        cursor = next_month
    return out


def iter_dates(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def _first_day_of_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _ensure_safe_append(path: Path, *, resume: bool) -> None:
    if path.exists() and path.stat().st_size > 0 and not resume:
        raise FileExistsError(f"archive exists; pass --resume to append safely: {path}")


def _canonical_json_bytes(row: dict[str, Any]) -> bytes:
    return json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
