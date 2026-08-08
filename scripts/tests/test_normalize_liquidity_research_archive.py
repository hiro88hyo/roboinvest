from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "normalize-liquidity-research-archive.py"
    spec = importlib.util.spec_from_file_location(
        "normalize_liquidity_research_archive",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


normalizer = _load_module()


def _bar_row(*, target: str = "2022-03-31", code: str = "72030") -> dict[str, object]:
    return {
        "Date": target,
        "Code": code,
        "O": 2100.0,
        "H": 2150.0,
        "L": 2080.0,
        "C": 2140.0,
        "Vo": 1000.0,
        "Va": 2_140_000.0,
        "AdjFactor": 1.0,
        "AdjO": 2100.0,
        "AdjH": 2150.0,
        "AdjL": 2080.0,
        "AdjC": 2140.0,
        "AdjVo": 1000.0,
        "UL": "0",
        "LL": "0",
    }


def _null_bar_row(*, target: str, code: str) -> dict[str, object]:
    row = _bar_row(target=target, code=code)
    for key in ("O", "H", "L", "C", "Vo", "Va", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo"):
        row[key] = None
    return row


def _master_row(*, target: str = "2022-03-31", code: str = "72030") -> dict[str, object]:
    return {
        "Date": target,
        "Code": code,
        "CoName": "トヨタ自動車",
        "CoNameEn": "TOYOTA MOTOR CORPORATION",
        "ProdCat": "011",
        "Mkt": "0111",
        "MktNm": "プライム",
        "Mrgn": "2",
        "MrgnNm": "貸借",
        "S17": "3",
        "S17Nm": "自動車・輸送機",
        "S33": "3700",
        "S33Nm": "輸送用機器",
        "ScaleCat": "TOPIX Core30",
    }


def _tagged_rows(
    rows: list[dict[str, object]],
    *,
    dataset: str,
    target: str,
    fetch_id: str,
) -> list[dict[str, object]]:
    receipt = "2026-08-08T05:00:00+00:00"
    tagged = [
        {
            **row,
            "_roboinvest_record_type": "source",
            "_roboinvest_archive_schema_version": normalizer.RAW_ARCHIVE_SCHEMA_VERSION,
            "_roboinvest_dataset": dataset,
            "_roboinvest_fetch_id": fetch_id,
            "_roboinvest_target_date": target,
            "_roboinvest_source_received_at": receipt,
        }
        for row in rows
    ]
    marker = {
        "_roboinvest_record_type": "fetch_metadata",
        "_roboinvest_archive_schema_version": normalizer.RAW_ARCHIVE_SCHEMA_VERSION,
        "_roboinvest_dataset": dataset,
        "_roboinvest_fetch_id": fetch_id,
        "_roboinvest_target_date": target,
        "_roboinvest_source_received_at": receipt,
        "_roboinvest_row_count": len(rows),
        "_roboinvest_source_payload_sha256": normalizer.source_payload_sha256(tagged),
    }
    return [*tagged, marker]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _file_record(
    path: Path,
    *,
    dataset: str,
    source_rows: int,
    dates: list[str],
) -> dict[str, object]:
    return {
        "dataset": dataset,
        "sha256": normalizer.file_sha256(path),
        "source_row_count": source_rows,
        "metadata_row_count": len(dates),
        "completed_source_row_count": source_rows,
        "completed_fetch_count": len(dates),
        "unique_completed_date_count": len(dates),
        "duplicate_completed_date_count": 0,
        "first_completed_date": dates[0] if dates else None,
        "last_completed_date": dates[-1] if dates else None,
    }


def _make_archive(tmp_path: Path) -> Path:
    input_dir = tmp_path / "raw"
    input_dir.mkdir()
    bars = [
        *_tagged_rows(
            [_bar_row(target="2022-03-30")],
            dataset=normalizer.BARS_DATASET,
            target="2022-03-30",
            fetch_id="bars-1",
        ),
        *_tagged_rows(
            [_bar_row(), _null_bar_row(target="2022-03-31", code="130A0")],
            dataset=normalizer.BARS_DATASET,
            target="2022-03-31",
            fetch_id="bars-2",
        ),
    ]
    masters = _tagged_rows(
        [_master_row(), _master_row(code="130A0")],
        dataset=normalizer.MASTER_DATASET,
        target="2022-03-31",
        fetch_id="master-1",
    )
    bars_path = input_dir / normalizer.BARS_FILENAME
    master_path = input_dir / normalizer.MASTER_FILENAME
    _write_jsonl(bars_path, bars)
    _write_jsonl(master_path, masters)
    manifest = {
        "manifest_version": 1,
        "archive_schema_version": normalizer.RAW_ARCHIVE_SCHEMA_VERSION,
        "research_only": True,
        "paper_live_enabled": False,
        "requested_start_date": "2022-03-30",
        "requested_end_date": "2022-03-31",
        "files": {
            normalizer.BARS_FILENAME: _file_record(
                bars_path,
                dataset=normalizer.BARS_DATASET,
                source_rows=3,
                dates=["2022-03-30", "2022-03-31"],
            ),
            normalizer.MASTER_FILENAME: _file_record(
                master_path,
                dataset=normalizer.MASTER_DATASET,
                source_rows=2,
                dates=["2022-03-31"],
            ),
        },
    }
    (input_dir / normalizer.INPUT_MANIFEST_FILENAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return input_dir


def _refresh_manifest_hash(input_dir: Path, filename: str) -> None:
    manifest_path = input_dir / normalizer.INPUT_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][filename]["sha256"] = normalizer.file_sha256(input_dir / filename)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_normalize_archive_writes_typed_research_only_partitions(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    output_dir = tmp_path / "normalized"

    manifest = normalizer.normalize_archive(input_dir=input_dir, output_dir=output_dir)

    assert manifest["research_only"] is True
    assert manifest["paper_live_enabled"] is False
    assert manifest["factor_or_outcome_computed"] is False
    bars = manifest["datasets"][normalizer.BARS_DATASET]
    assert bars["source_row_count"] == 3
    assert bars["null_trading_row_count"] == 1
    assert bars["partition_count"] == 1
    frame = pl.read_parquet(output_dir / bars["partitions"][0]["path"])
    assert frame.schema["date"] == pl.Date
    assert frame.schema["adjusted_close"] == pl.Float64
    assert frame.get_column("code").to_list() == ["72030", "72030", "130A0"]
    assert frame.get_column("adjusted_close").null_count() == 1
    validation = json.loads(
        (output_dir / normalizer.VALIDATION_REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert validation["status"] == "PASS"
    assert validation["factor_or_outcome_computed"] is False


def test_input_manifest_hash_mismatch_fails_before_output(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    with (input_dir / normalizer.BARS_FILENAME).open("a", encoding="utf-8") as output:
        output.write("{}\n")

    with pytest.raises(normalizer.ArchiveValidationError, match="hash mismatch"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")

    assert not (tmp_path / "normalized").exists()


def test_payload_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    rows[-1]["_roboinvest_source_payload_sha256"] = "0" * 64
    _write_jsonl(bars_path, rows)
    _refresh_manifest_hash(input_dir, normalizer.BARS_FILENAME)

    with pytest.raises(normalizer.ArchiveValidationError, match="payload hash mismatch"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_source_date_must_equal_fetch_target(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["Date"] = "2022-03-29"
    rows[1]["_roboinvest_source_payload_sha256"] = normalizer.source_payload_sha256([rows[0]])
    _write_jsonl(bars_path, rows)
    _refresh_manifest_hash(input_dir, normalizer.BARS_FILENAME)

    with pytest.raises(normalizer.ArchiveValidationError, match="differs from fetch target"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_duplicate_date_code_is_rejected(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    second_fetch_source = rows[2]
    duplicate = dict(second_fetch_source)
    rows.insert(3, duplicate)
    marker = rows[5]
    marker["_roboinvest_row_count"] = 3
    marker["_roboinvest_source_payload_sha256"] = normalizer.source_payload_sha256(rows[2:5])
    _write_jsonl(bars_path, rows)
    manifest_path = input_dir / normalizer.INPUT_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["files"][normalizer.BARS_FILENAME]
    record["sha256"] = normalizer.file_sha256(bars_path)
    record["source_row_count"] = 4
    record["completed_source_row_count"] = 4
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(normalizer.ArchiveValidationError, match="duplicate bar date/code"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_partial_null_ohlcv_is_rejected(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["AdjC"] = None
    rows[1]["_roboinvest_source_payload_sha256"] = normalizer.source_payload_sha256([rows[0]])
    _write_jsonl(bars_path, rows)
    _refresh_manifest_hash(input_dir, normalizer.BARS_FILENAME)

    with pytest.raises(normalizer.ArchiveValidationError, match="partial-null Adj OHLCV"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_alphanumeric_issue_code_is_preserved() -> None:
    assert normalizer.validate_code("130A0") == "130A0"


def test_naive_receipt_timestamp_is_rejected(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["_roboinvest_source_received_at"] = "2026-08-08T05:00:00"
    rows[1]["_roboinvest_source_received_at"] = "2026-08-08T05:00:00"
    rows[1]["_roboinvest_source_payload_sha256"] = normalizer.source_payload_sha256([rows[0]])
    _write_jsonl(bars_path, rows)
    _refresh_manifest_hash(input_dir, normalizer.BARS_FILENAME)

    with pytest.raises(normalizer.ArchiveValidationError, match="naive timestamp"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    output_dir = tmp_path / "normalized"
    output_dir.mkdir()
    sentinel = output_dir / "owned-by-user.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=output_dir)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_marker_dates_must_be_strictly_increasing(tmp_path: Path) -> None:
    input_dir = _make_archive(tmp_path)
    bars_path = input_dir / normalizer.BARS_FILENAME
    rows = [json.loads(line) for line in bars_path.read_text(encoding="utf-8").splitlines()]
    first_fetch = rows[:2]
    rows = [*rows, *first_fetch]
    _write_jsonl(bars_path, rows)
    manifest_path = input_dir / normalizer.INPUT_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["files"][normalizer.BARS_FILENAME]
    record["sha256"] = normalizer.file_sha256(bars_path)
    record["source_row_count"] = 4
    record["metadata_row_count"] = 3
    record["completed_source_row_count"] = 4
    record["completed_fetch_count"] = 3
    record["unique_completed_date_count"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(normalizer.ArchiveValidationError, match="strictly increasing"):
        normalizer.normalize_archive(input_dir=input_dir, output_dir=tmp_path / "normalized")


def test_timestamp_is_normalized_to_utc() -> None:
    parsed = normalizer.parse_timestamp(
        "2026-08-08T14:00:00+09:00",
        path=Path("archive.jsonl"),
        line_number=1,
    )
    assert parsed == datetime(2026, 8, 8, 5, 0, tzinfo=UTC)


def test_partition_record_has_stable_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "normalized.tmp"
    path = root / "bars" / "bars-2022-03.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture")

    record = normalizer.partition_record(
        path,
        root,
        1,
        date(2022, 3, 31),
        date(2022, 3, 31),
    )

    assert record.path == "bars/bars-2022-03.parquet"
    assert record.sha256 == normalizer.file_sha256(path)
