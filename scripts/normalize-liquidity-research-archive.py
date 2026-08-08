#!/usr/bin/env python3
"""Validate and normalize the immutable J-Quants liquidity research archive.

This command is intentionally outcome-blind.  It verifies the raw archive and
creates typed Parquet partitions, but it does not calculate liquidity factors,
forward returns, rankings, signals, labels, or portfolio results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

RAW_ARCHIVE_SCHEMA_VERSION = "jquants_liquidity_research_archive_v1"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
BARS_DATASET = "equities_bars_daily"
MASTER_DATASET = "equities_master_month_end"
BARS_FILENAME = "bars-daily-raw.jsonl"
MASTER_FILENAME = "master-month-end-raw.jsonl"
INPUT_MANIFEST_FILENAME = "manifest.json"
OUTPUT_MANIFEST_FILENAME = "normalized-manifest.json"
VALIDATION_REPORT_FILENAME = "validation-report.json"

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
    "O",
    "H",
    "L",
    "C",
    "Vo",
    "Va",
    "AdjFactor",
    "AdjO",
    "AdjH",
    "AdjL",
    "AdjC",
    "AdjVo",
    "UL",
    "LL",
}
MASTER_REQUIRED_FIELDS = {
    "Date",
    "Code",
    "CoName",
    "CoNameEn",
    "ProdCat",
    "Mkt",
    "MktNm",
    "Mrgn",
    "MrgnNm",
    "S17",
    "S17Nm",
    "S33",
    "S33Nm",
    "ScaleCat",
}

BAR_SCHEMA = {
    "date": pl.Date,
    "code": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "turnover_jpy": pl.Float64,
    "adjustment_factor": pl.Float64,
    "adjusted_open": pl.Float64,
    "adjusted_high": pl.Float64,
    "adjusted_low": pl.Float64,
    "adjusted_close": pl.Float64,
    "adjusted_volume": pl.Float64,
    "upper_limit_flag": pl.String,
    "lower_limit_flag": pl.String,
    "source_fetch_id": pl.String,
    "source_received_at": pl.Datetime(time_unit="us", time_zone="UTC"),
}
MASTER_SCHEMA = {
    "as_of_date": pl.Date,
    "code": pl.String,
    "company_name": pl.String,
    "company_name_en": pl.String,
    "product_category": pl.String,
    "market_code": pl.String,
    "market_name": pl.String,
    "margin_code": pl.String,
    "margin_name": pl.String,
    "sector17_code": pl.String,
    "sector17_name": pl.String,
    "sector33_code": pl.String,
    "sector33_name": pl.String,
    "scale_category": pl.String,
    "source_fetch_id": pl.String,
    "source_received_at": pl.Datetime(time_unit="us", time_zone="UTC"),
}


class ArchiveValidationError(ValueError):
    """Raised when the immutable raw archive does not satisfy its contract."""


@dataclass(frozen=True, slots=True)
class FetchBatch:
    target_date: date
    fetch_id: str
    source_received_at: datetime
    rows: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class StreamSummary:
    dataset: str
    source_row_count: int = 0
    metadata_row_count: int = 0
    completed_fetch_count: int = 0
    first_date: date | None = None
    last_date: date | None = None
    null_trading_row_count: int = 0
    null_turnover_row_count: int = 0
    completed_dates: set[date] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class Partition:
    path: str
    row_count: int
    first_date: str
    last_date: str
    sha256: str
    byte_size: int


def main() -> int:
    args = build_parser().parse_args()
    normalize_archive(input_dir=args.input_dir, output_dir=args.output_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def normalize_archive(*, input_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest_path = input_dir / INPUT_MANIFEST_FILENAME
    manifest = load_and_verify_input_manifest(manifest_path, input_dir=input_dir)
    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    temporary_dir.mkdir(parents=True)

    bars_summary = StreamSummary(dataset=BARS_DATASET)
    bars_partitions = normalize_bars(
        input_dir / BARS_FILENAME,
        output_dir=temporary_dir / "bars",
        summary=bars_summary,
    )
    master_summary = StreamSummary(dataset=MASTER_DATASET)
    master_partitions = normalize_master(
        input_dir / MASTER_FILENAME,
        output_dir=temporary_dir / "master",
        summary=master_summary,
    )
    verify_summary_against_manifest(
        summary=bars_summary,
        file_manifest=manifest["files"][BARS_FILENAME],
    )
    verify_summary_against_manifest(
        summary=master_summary,
        file_manifest=manifest["files"][MASTER_FILENAME],
    )

    validation = {
        "status": "PASS",
        "factor_or_outcome_computed": False,
        "input_manifest_sha256": file_sha256(manifest_path),
        "datasets": {
            BARS_DATASET: summary_record(bars_summary),
            MASTER_DATASET: summary_record(master_summary),
        },
    }
    write_json(temporary_dir / VALIDATION_REPORT_FILENAME, validation)
    output_manifest = build_output_manifest(
        input_dir=input_dir,
        input_manifest=manifest,
        input_manifest_sha256=file_sha256(manifest_path),
        bars_summary=bars_summary,
        master_summary=master_summary,
        bars_partitions=bars_partitions,
        master_partitions=master_partitions,
    )
    write_json(temporary_dir / OUTPUT_MANIFEST_FILENAME, output_manifest)
    temporary_dir.rename(output_dir)
    return output_manifest


def load_and_verify_input_manifest(path: Path, *, input_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArchiveValidationError(f"input manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArchiveValidationError(f"invalid input manifest JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ArchiveValidationError("input manifest must be a JSON object")
    if value.get("manifest_version") != 1:
        raise ArchiveValidationError("unsupported input manifest version")
    if value.get("archive_schema_version") != RAW_ARCHIVE_SCHEMA_VERSION:
        raise ArchiveValidationError("unexpected raw archive schema version")
    if value.get("research_only") is not True or value.get("paper_live_enabled") is not False:
        raise ArchiveValidationError("input archive is not research-only")
    files = value.get("files")
    if not isinstance(files, dict):
        raise ArchiveValidationError("input manifest files must be an object")
    for filename in (BARS_FILENAME, MASTER_FILENAME):
        record = files.get(filename)
        if not isinstance(record, dict):
            raise ArchiveValidationError(f"input manifest missing file record: {filename}")
        expected_hash = record.get("sha256")
        path_to_verify = input_dir / filename
        if not path_to_verify.is_file():
            raise ArchiveValidationError(f"input archive file not found: {path_to_verify}")
        actual_hash = file_sha256(path_to_verify)
        if expected_hash != actual_hash:
            raise ArchiveValidationError(
                f"input archive hash mismatch for {filename}: "
                f"expected={expected_hash} actual={actual_hash}"
            )
        if record.get("duplicate_completed_date_count") != 0:
            raise ArchiveValidationError(f"input manifest reports duplicate dates: {filename}")
    return value


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"normalized output already exists: {output_dir}")
    if temporary_dir.exists():
        raise FileExistsError(
            "incomplete normalized output exists; inspect it before moving it aside: "
            f"{temporary_dir}"
        )


def iter_validated_fetches(
    path: Path,
    *,
    dataset: str,
    summary: StreamSummary,
) -> Iterator[FetchBatch]:
    active_fetch_id: str | None = None
    active_rows: list[dict[str, Any]] = []
    active_target = ""
    active_receipt = ""
    previous_target: date | None = None
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                raise ArchiveValidationError(f"blank JSONL row at {path}:{line_number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ArchiveValidationError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ArchiveValidationError(f"non-object JSONL row at {path}:{line_number}")
            record_type = row.get("_roboinvest_record_type")
            if record_type == "source":
                fetch_id, target, receipt = validate_source_provenance(
                    row,
                    dataset=dataset,
                    path=path,
                    line_number=line_number,
                )
                if active_fetch_id is None:
                    active_fetch_id = fetch_id
                    active_target = target
                    active_receipt = receipt
                elif (fetch_id, target, receipt) != (
                    active_fetch_id,
                    active_target,
                    active_receipt,
                ):
                    raise ArchiveValidationError(
                        f"interleaved or incomplete fetch at {path}:{line_number}"
                    )
                active_rows.append(row)
                summary.source_row_count += 1
                continue
            if record_type != "fetch_metadata":
                raise ArchiveValidationError(f"unknown record type at {path}:{line_number}")
            summary.metadata_row_count += 1
            fetch_id = required_string(row, "_roboinvest_fetch_id", path, line_number)
            target = required_string(row, "_roboinvest_target_date", path, line_number)
            receipt = required_string(row, "_roboinvest_source_received_at", path, line_number)
            validate_common_provenance(row, dataset=dataset, path=path, line_number=line_number)
            if active_fetch_id is None:
                active_fetch_id = fetch_id
                active_target = target
                active_receipt = receipt
            if (fetch_id, target, receipt) != (
                active_fetch_id,
                active_target,
                active_receipt,
            ):
                raise ArchiveValidationError(
                    f"fetch marker provenance mismatch at {path}:{line_number}"
                )
            row_count = row.get("_roboinvest_row_count")
            if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
                raise ArchiveValidationError(f"invalid fetch row count at {path}:{line_number}")
            if row_count != len(active_rows):
                raise ArchiveValidationError(f"fetch row count mismatch at {path}:{line_number}")
            expected_digest = source_payload_sha256(active_rows)
            if row.get("_roboinvest_source_payload_sha256") != expected_digest:
                raise ArchiveValidationError(f"fetch payload hash mismatch at {path}:{line_number}")
            parsed_target = parse_date(target, path=path, line_number=line_number)
            parsed_receipt = parse_timestamp(receipt, path=path, line_number=line_number)
            if previous_target is not None and parsed_target <= previous_target:
                raise ArchiveValidationError(f"fetch dates are not strictly increasing: {path}")
            if parsed_target in summary.completed_dates:
                raise ArchiveValidationError(f"duplicate completed date in {path}: {parsed_target}")
            summary.completed_dates.add(parsed_target)
            summary.completed_fetch_count += 1
            summary.first_date = summary.first_date or parsed_target
            summary.last_date = parsed_target
            previous_target = parsed_target
            yield FetchBatch(
                target_date=parsed_target,
                fetch_id=fetch_id,
                source_received_at=parsed_receipt,
                rows=tuple(active_rows),
            )
            active_fetch_id = None
            active_rows = []
            active_target = ""
            active_receipt = ""
    if active_fetch_id is not None or active_rows:
        raise ArchiveValidationError(f"incomplete fetch at end of {path}")


def validate_source_provenance(
    row: dict[str, Any],
    *,
    dataset: str,
    path: Path,
    line_number: int,
) -> tuple[str, str, str]:
    validate_common_provenance(row, dataset=dataset, path=path, line_number=line_number)
    return (
        required_string(row, "_roboinvest_fetch_id", path, line_number),
        required_string(row, "_roboinvest_target_date", path, line_number),
        required_string(row, "_roboinvest_source_received_at", path, line_number),
    )


def validate_common_provenance(
    row: Mapping[str, Any],
    *,
    dataset: str,
    path: Path,
    line_number: int,
) -> None:
    if row.get("_roboinvest_archive_schema_version") != RAW_ARCHIVE_SCHEMA_VERSION:
        raise ArchiveValidationError(f"archive schema drift at {path}:{line_number}")
    if row.get("_roboinvest_dataset") != dataset:
        raise ArchiveValidationError(f"dataset drift at {path}:{line_number}")


def normalize_bars(
    path: Path,
    *,
    output_dir: Path,
    summary: StreamSummary,
) -> list[Partition]:
    output_dir.mkdir(parents=True)
    partitions: list[Partition] = []
    month_rows: list[dict[str, Any]] = []
    current_month = ""
    for batch in iter_validated_fetches(path, dataset=BARS_DATASET, summary=summary):
        batch_month = batch.target_date.strftime("%Y-%m")
        if current_month and batch_month != current_month:
            partitions.append(write_bar_partition(output_dir, current_month, month_rows))
            month_rows = []
        current_month = batch_month
        seen_codes: set[str] = set()
        for index, row in enumerate(batch.rows):
            normalized = normalize_bar_row(row, batch=batch, index=index, path=path)
            code = normalized["code"]
            if code in seen_codes:
                raise ArchiveValidationError(f"duplicate bar date/code: {batch.target_date}/{code}")
            seen_codes.add(code)
            if normalized["adjusted_close"] is None:
                summary.null_trading_row_count += 1
            if normalized["turnover_jpy"] is None:
                summary.null_turnover_row_count += 1
            month_rows.append(normalized)
    if current_month:
        partitions.append(write_bar_partition(output_dir, current_month, month_rows))
    return partitions


def normalize_bar_row(
    row: dict[str, Any],
    *,
    batch: FetchBatch,
    index: int,
    path: Path,
) -> dict[str, Any]:
    missing = sorted(BARS_REQUIRED_FIELDS - row.keys())
    if missing:
        raise ArchiveValidationError(
            f"bar row missing fields at {path} fetch={batch.fetch_id} row={index}: {missing}"
        )
    row_date = parse_date_value(row["Date"], field_name="Date")
    if row_date != batch.target_date:
        raise ArchiveValidationError(
            f"bar date differs from fetch target: {row_date} != {batch.target_date}"
        )
    code = validate_code(row["Code"])
    numeric = {
        key: optional_finite_number(row[key], field_name=key)
        for key in (
            "O",
            "H",
            "L",
            "C",
            "Vo",
            "Va",
            "AdjO",
            "AdjH",
            "AdjL",
            "AdjC",
            "AdjVo",
        )
    }
    factor = required_positive_number(row["AdjFactor"], field_name="AdjFactor")
    validate_ohlcv_group(numeric, prefix="", code=code, value_keys=("O", "H", "L", "C", "Vo"))
    validate_ohlcv_group(
        numeric,
        prefix="Adj",
        code=code,
        value_keys=("AdjO", "AdjH", "AdjL", "AdjC", "AdjVo"),
    )
    turnover = numeric["Va"]
    if turnover is not None and turnover <= 0:
        raise ArchiveValidationError(f"non-positive turnover for {row_date}/{code}")
    upper = validate_flag(row["UL"], field_name="UL")
    lower = validate_flag(row["LL"], field_name="LL")
    return {
        "date": row_date,
        "code": code,
        "open": numeric["O"],
        "high": numeric["H"],
        "low": numeric["L"],
        "close": numeric["C"],
        "volume": numeric["Vo"],
        "turnover_jpy": turnover,
        "adjustment_factor": factor,
        "adjusted_open": numeric["AdjO"],
        "adjusted_high": numeric["AdjH"],
        "adjusted_low": numeric["AdjL"],
        "adjusted_close": numeric["AdjC"],
        "adjusted_volume": numeric["AdjVo"],
        "upper_limit_flag": upper,
        "lower_limit_flag": lower,
        "source_fetch_id": batch.fetch_id,
        "source_received_at": batch.source_received_at,
    }


def validate_ohlcv_group(
    numeric: Mapping[str, float | None],
    *,
    prefix: str,
    code: str,
    value_keys: Sequence[str],
) -> None:
    values = [numeric[key] for key in value_keys]
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise ArchiveValidationError(f"partial-null {prefix or 'raw'} OHLCV for {code}")
    open_value, high_value, low_value, close_value, volume = values
    assert open_value is not None
    assert high_value is not None
    assert low_value is not None
    assert close_value is not None
    assert volume is not None
    if min(open_value, high_value, low_value, close_value, volume) <= 0:
        raise ArchiveValidationError(f"non-positive {prefix or 'raw'} OHLCV for {code}")
    if low_value > min(open_value, close_value) or high_value < max(open_value, close_value):
        raise ArchiveValidationError(f"invalid {prefix or 'raw'} OHLC range for {code}")


def write_bar_partition(output_dir: Path, month: str, rows: list[dict[str, Any]]) -> Partition:
    if not rows:
        raise ArchiveValidationError(f"empty bar partition: {month}")
    path = output_dir / f"bars-{month}.parquet"
    frame = pl.DataFrame(rows, schema=BAR_SCHEMA, strict=True)
    frame.write_parquet(path, compression="zstd", statistics=True)
    dates = frame.get_column("date")
    return partition_record(path, output_dir.parent, len(frame), min(dates), max(dates))


def normalize_master(
    path: Path,
    *,
    output_dir: Path,
    summary: StreamSummary,
) -> list[Partition]:
    output_dir.mkdir(parents=True)
    partitions: list[Partition] = []
    for batch in iter_validated_fetches(path, dataset=MASTER_DATASET, summary=summary):
        rows: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for index, row in enumerate(batch.rows):
            normalized = normalize_master_row(row, batch=batch, index=index, path=path)
            code = normalized["code"]
            if code in seen_codes:
                raise ArchiveValidationError(
                    f"duplicate master date/code: {batch.target_date}/{code}"
                )
            seen_codes.add(code)
            rows.append(normalized)
        partition_path = output_dir / f"master-{batch.target_date.isoformat()}.parquet"
        frame = pl.DataFrame(rows, schema=MASTER_SCHEMA, strict=True)
        frame.write_parquet(partition_path, compression="zstd", statistics=True)
        partitions.append(
            partition_record(
                partition_path,
                output_dir.parent,
                len(frame),
                batch.target_date,
                batch.target_date,
            )
        )
    return partitions


def normalize_master_row(
    row: dict[str, Any],
    *,
    batch: FetchBatch,
    index: int,
    path: Path,
) -> dict[str, Any]:
    missing = sorted(MASTER_REQUIRED_FIELDS - row.keys())
    if missing:
        raise ArchiveValidationError(
            f"master row missing fields at {path} fetch={batch.fetch_id} row={index}: {missing}"
        )
    row_date = parse_date_value(row["Date"], field_name="Date")
    if row_date != batch.target_date:
        raise ArchiveValidationError(
            f"master date differs from fetch target: {row_date} != {batch.target_date}"
        )
    return {
        "as_of_date": row_date,
        "code": validate_code(row["Code"]),
        "company_name": string_value(row["CoName"], field_name="CoName"),
        "company_name_en": string_value(row["CoNameEn"], field_name="CoNameEn"),
        "product_category": string_value(row["ProdCat"], field_name="ProdCat"),
        "market_code": string_value(row["Mkt"], field_name="Mkt"),
        "market_name": string_value(row["MktNm"], field_name="MktNm"),
        "margin_code": string_value(row["Mrgn"], field_name="Mrgn"),
        "margin_name": string_value(row["MrgnNm"], field_name="MrgnNm"),
        "sector17_code": string_value(row["S17"], field_name="S17"),
        "sector17_name": string_value(row["S17Nm"], field_name="S17Nm"),
        "sector33_code": string_value(row["S33"], field_name="S33"),
        "sector33_name": string_value(row["S33Nm"], field_name="S33Nm"),
        "scale_category": string_value(row["ScaleCat"], field_name="ScaleCat"),
        "source_fetch_id": batch.fetch_id,
        "source_received_at": batch.source_received_at,
    }


def verify_summary_against_manifest(
    *,
    summary: StreamSummary,
    file_manifest: Mapping[str, Any],
) -> None:
    expected = {
        "source_row_count": summary.source_row_count,
        "metadata_row_count": summary.metadata_row_count,
        "completed_fetch_count": summary.completed_fetch_count,
        "unique_completed_date_count": len(summary.completed_dates),
    }
    for key, actual in expected.items():
        if file_manifest.get(key) != actual:
            raise ArchiveValidationError(
                f"manifest {key} mismatch for {summary.dataset}: "
                f"expected={file_manifest.get(key)} actual={actual}"
            )
    first = summary.first_date.isoformat() if summary.first_date else None
    last = summary.last_date.isoformat() if summary.last_date else None
    if file_manifest.get("first_completed_date") != first:
        raise ArchiveValidationError(f"manifest first date mismatch for {summary.dataset}")
    if file_manifest.get("last_completed_date") != last:
        raise ArchiveValidationError(f"manifest last date mismatch for {summary.dataset}")


def summary_record(summary: StreamSummary) -> dict[str, Any]:
    return {
        "source_row_count": summary.source_row_count,
        "metadata_row_count": summary.metadata_row_count,
        "completed_fetch_count": summary.completed_fetch_count,
        "unique_completed_date_count": len(summary.completed_dates),
        "first_date": summary.first_date.isoformat() if summary.first_date else None,
        "last_date": summary.last_date.isoformat() if summary.last_date else None,
        "null_trading_row_count": summary.null_trading_row_count,
        "null_turnover_row_count": summary.null_turnover_row_count,
    }


def build_output_manifest(
    *,
    input_dir: Path,
    input_manifest: Mapping[str, Any],
    input_manifest_sha256: str,
    bars_summary: StreamSummary,
    master_summary: StreamSummary,
    bars_partitions: Sequence[Partition],
    master_partitions: Sequence[Partition],
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "normalizer_sha256": file_sha256(Path(__file__)),
        "research_only": True,
        "paper_live_enabled": False,
        "factor_or_outcome_computed": False,
        "input": {
            "directory": str(input_dir),
            "manifest_sha256": input_manifest_sha256,
            "archive_schema_version": input_manifest.get("archive_schema_version"),
            "requested_start_date": input_manifest.get("requested_start_date"),
            "requested_end_date": input_manifest.get("requested_end_date"),
            "files": {
                filename: {"sha256": record.get("sha256")}
                for filename, record in input_manifest["files"].items()
            },
        },
        "datasets": {
            BARS_DATASET: {
                **summary_record(bars_summary),
                "partition_count": len(bars_partitions),
                "partitions": [partition_to_dict(item) for item in bars_partitions],
            },
            MASTER_DATASET: {
                **summary_record(master_summary),
                "partition_count": len(master_partitions),
                "partitions": [partition_to_dict(item) for item in master_partitions],
            },
        },
    }


def partition_record(
    path: Path,
    root: Path,
    row_count: int,
    first_date: date,
    last_date: date,
) -> Partition:
    return Partition(
        path=str(path.relative_to(root)),
        row_count=row_count,
        first_date=first_date.isoformat(),
        last_date=last_date.isoformat(),
        sha256=file_sha256(path),
        byte_size=path.stat().st_size,
    )


def partition_to_dict(value: Partition) -> dict[str, Any]:
    return {
        "path": value.path,
        "row_count": value.row_count,
        "first_date": value.first_date,
        "last_date": value.last_date,
        "sha256": value.sha256,
        "byte_size": value.byte_size,
    }


def source_payload_sha256(rows: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, row in enumerate(rows):
        if index:
            digest.update(b",")
        stripped = {key: value for key, value in row.items() if key not in PROVENANCE_KEYS}
        digest.update(canonical_json_bytes(stripped))
    digest.update(b"]")
    return digest.hexdigest()


def required_string(row: Mapping[str, Any], key: str, path: Path, line_number: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ArchiveValidationError(f"missing {key} at {path}:{line_number}")
    return value


def parse_date(value: str, *, path: Path, line_number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArchiveValidationError(f"invalid date at {path}:{line_number}: {value}") from exc


def parse_date_value(value: Any, *, field_name: str) -> date:
    if not isinstance(value, str):
        raise ArchiveValidationError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArchiveValidationError(f"invalid {field_name}: {value}") from exc


def parse_timestamp(value: str, *, path: Path, line_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ArchiveValidationError(f"invalid timestamp at {path}:{line_number}") from exc
    if parsed.tzinfo is None:
        raise ArchiveValidationError(f"naive timestamp at {path}:{line_number}")
    return parsed.astimezone(UTC)


def validate_code(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 5 or not value.isascii() or not value.isalnum():
        raise ArchiveValidationError(f"invalid five-character issue code: {value!r}")
    return value.upper()


def optional_finite_number(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ArchiveValidationError(f"{field_name} must be numeric or null")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ArchiveValidationError(f"{field_name} must be finite")
    return parsed


def required_positive_number(value: Any, *, field_name: str) -> float:
    parsed = optional_finite_number(value, field_name=field_name)
    if parsed is None or parsed <= 0:
        raise ArchiveValidationError(f"{field_name} must be positive")
    return parsed


def validate_flag(value: Any, *, field_name: str) -> str:
    if value not in {"0", "1"}:
        raise ArchiveValidationError(f"{field_name} must be '0' or '1'")
    return str(value)


def string_value(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ArchiveValidationError(f"{field_name} must be a string")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
