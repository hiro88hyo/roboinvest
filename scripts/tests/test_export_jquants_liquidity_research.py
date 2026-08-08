from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "export-jquants-liquidity-research.py"
    spec = importlib.util.spec_from_file_location(
        "export_jquants_liquidity_research",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load_module()


def _bar_row() -> dict[str, object]:
    return {
        "Date": "2022-03-31",
        "Code": "72030",
        "O": 2100.0,
        "H": 2150.0,
        "L": 2080.0,
        "C": 2140.0,
        "Vo": 1000,
        "Va": 2_140_000.0,
        "AdjFactor": 1.0,
        "AdjC": 2140.0,
        "AdjVo": 1000.0,
    }


def test_month_end_business_dates_uses_completed_month_ends() -> None:
    assert exporter.month_end_business_dates(
        date(2026, 1, 1),
        date(2026, 3, 31),
    ) == [
        date(2026, 1, 30),
        date(2026, 2, 27),
        date(2026, 3, 31),
    ]


def test_month_end_business_dates_omits_incomplete_final_month() -> None:
    assert exporter.month_end_business_dates(
        date(2026, 1, 10),
        date(2026, 2, 10),
    ) == [date(2026, 1, 30)]


def test_source_payload_sha256_is_order_sensitive_and_ignores_provenance() -> None:
    first = _bar_row()
    tagged = {
        **first,
        "_roboinvest_record_type": "source",
        "_roboinvest_fetch_id": "fetch-1",
    }
    second = {**first, "Code": "67580"}

    assert exporter.source_payload_sha256([first]) == exporter.source_payload_sha256([tagged])
    assert exporter.source_payload_sha256([first, second]) != exporter.source_payload_sha256(
        [second, first]
    )


def test_write_and_inspect_completed_fetch(tmp_path: Path) -> None:
    path = tmp_path / exporter.BARS_FILENAME
    receipt = datetime(2026, 8, 8, 4, 30, tzinfo=UTC)
    with path.open("w", encoding="utf-8") as output:
        marker = exporter.write_fetch_records(
            output,
            rows=[_bar_row()],
            dataset=exporter.BARS_DATASET,
            target_date=date(2022, 3, 31),
            source_received_at=receipt,
            fetch_id="fetch-1",
        )

    inspection = exporter.inspect_archive(path)
    assert inspection.completed_dates == frozenset({"2022-03-31"})
    assert inspection.source_row_count == 1
    assert inspection.metadata_row_count == 1
    assert inspection.completed_source_row_count == 1
    assert inspection.completed_fetch_count == 1
    assert inspection.duplicate_completed_date_count == 0
    assert marker["_roboinvest_source_payload_sha256"] == exporter.source_payload_sha256(
        [_bar_row()]
    )


def test_incomplete_fetch_is_not_resumed_as_complete(tmp_path: Path) -> None:
    path = tmp_path / exporter.BARS_FILENAME
    source = {
        **_bar_row(),
        "_roboinvest_record_type": "source",
        "_roboinvest_archive_schema_version": exporter.ARCHIVE_SCHEMA_VERSION,
        "_roboinvest_dataset": exporter.BARS_DATASET,
        "_roboinvest_fetch_id": "fetch-1",
        "_roboinvest_target_date": "2022-03-31",
        "_roboinvest_source_received_at": "2026-08-08T04:30:00+00:00",
    }
    path.write_text(json.dumps(source) + "\n", encoding="utf-8")

    inspection = exporter.inspect_archive(path)
    assert inspection.source_row_count == 1
    assert inspection.completed_dates == frozenset()


def test_bad_payload_hash_does_not_complete_fetch(tmp_path: Path) -> None:
    path = tmp_path / exporter.BARS_FILENAME
    output = io.StringIO()
    exporter.write_fetch_records(
        output,
        rows=[_bar_row()],
        dataset=exporter.BARS_DATASET,
        target_date=date(2022, 3, 31),
        source_received_at=datetime(2026, 8, 8, 4, 30, tzinfo=UTC),
        fetch_id="fetch-1",
    )
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    rows[-1]["_roboinvest_source_payload_sha256"] = "0" * 64
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    assert exporter.inspect_archive(path).completed_dates == frozenset()


def test_zero_row_fetch_can_complete(tmp_path: Path) -> None:
    path = tmp_path / exporter.MASTER_FILENAME
    with path.open("w", encoding="utf-8") as output:
        exporter.write_fetch_records(
            output,
            rows=[],
            dataset=exporter.MASTER_DATASET,
            target_date=date(2022, 3, 31),
            source_received_at=datetime(2026, 8, 8, 4, 30, tzinfo=UTC),
            fetch_id="fetch-empty",
        )

    inspection = exporter.inspect_archive(path)
    assert inspection.completed_dates == frozenset({"2022-03-31"})
    assert inspection.completed_source_row_count == 0


def test_validate_response_fields_fails_closed() -> None:
    row = _bar_row()
    row.pop("AdjFactor")

    with pytest.raises(ValueError, match="AdjFactor"):
        exporter.validate_response_fields(
            [row],
            required_fields=exporter.BARS_REQUIRED_FIELDS,
            dataset=exporter.BARS_DATASET,
            target_date=date(2022, 3, 31),
        )


def test_existing_archive_requires_resume(tmp_path: Path) -> None:
    path = tmp_path / exporter.BARS_FILENAME
    path.write_text("occupied\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--resume"):
        exporter._ensure_safe_append(path, resume=False)
    exporter._ensure_safe_append(path, resume=True)


def test_build_manifest_binds_file_hashes_and_dates(tmp_path: Path) -> None:
    bars_path = tmp_path / exporter.BARS_FILENAME
    with bars_path.open("w", encoding="utf-8") as output:
        exporter.write_fetch_records(
            output,
            rows=[_bar_row()],
            dataset=exporter.BARS_DATASET,
            target_date=date(2022, 3, 31),
            source_received_at=datetime(2026, 8, 8, 4, 30, tzinfo=UTC),
            fetch_id="fetch-1",
        )

    manifest = exporter.build_manifest(
        output_dir=tmp_path,
        start_date=date(2022, 3, 31),
        end_date=date(2022, 3, 31),
        api_base="https://api.jquants.com/v2",
        api_version="v2",
    )

    bars = manifest["files"][exporter.BARS_FILENAME]
    assert manifest["research_only"] is True
    assert manifest["paper_live_enabled"] is False
    assert bars["sha256"] == exporter.file_sha256(bars_path)
    assert bars["unique_completed_date_count"] == 1
    assert bars["first_completed_date"] == "2022-03-31"
    assert manifest["files"][exporter.MASTER_FILENAME]["byte_size"] == 0
