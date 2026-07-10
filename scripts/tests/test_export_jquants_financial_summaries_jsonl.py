from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "export-jquants-financial-summaries-jsonl.py"
    spec = importlib.util.spec_from_file_location(
        "export_jquants_financial_summaries_jsonl",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load_module()


def test_iter_dates_includes_bounds() -> None:
    assert list(exporter.iter_dates(date(2026, 1, 1), date(2026, 1, 3))) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]


def test_read_existing_disclosed_dates_returns_unique_dates(tmp_path: Path) -> None:
    path = tmp_path / "fins.jsonl"
    path.write_text(
        '{"Code":"72030","DisclosedDate":"2026-01-05","DisclosureNumber":"a"}\n'
        '{"Code":"67580","DisclosedDate":"2026-01-05","DisclosureNumber":"b"}\n'
        '{"Code":"99840","Date":"2026-01-06","DisclosureNumber":"c"}\n'
        '{"Code":"31860","DiscDate":"2026-01-07","DiscNo":"d"}\n'
        '{"_roboinvest_record_type":"fetch_metadata",'
        '"_roboinvest_target_date":"2026-01-08"}\n',
        encoding="utf-8",
    )

    assert exporter.read_existing_disclosed_dates(path) == {
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    }


def test_read_existing_disclosed_dates_tolerates_missing_file(tmp_path: Path) -> None:
    assert exporter.read_existing_disclosed_dates(tmp_path / "missing.jsonl") == set()


def test_resume_does_not_treat_tagged_rows_without_completion_marker_as_complete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "interrupted.jsonl"
    path.write_text(
        '{"Code":"72030","DiscDate":"2026-01-21",'
        '"_roboinvest_fetched_at":"2026-01-21T07:00:00+00:00"}\n',
        encoding="utf-8",
    )

    assert exporter.read_existing_disclosed_dates(path) == set()


def test_resume_requires_valid_marker_timestamp_and_matching_row_count(tmp_path: Path) -> None:
    path = tmp_path / "completion-markers.jsonl"
    path.write_text(
        '{"Code":"72030","DiscDate":"2026-01-21",'
        '"_roboinvest_fetched_at":"2026-01-21T15:30:00+00:00"}\n'
        '{"_roboinvest_record_type":"fetch_metadata",'
        '"_roboinvest_target_date":"2026-01-21",'
        '"_roboinvest_fetched_at":"2026-01-21T15:30:00+00:00",'
        '"_roboinvest_row_count":1}\n'
        '{"_roboinvest_record_type":"fetch_metadata",'
        '"_roboinvest_target_date":"2026-01-22",'
        '"_roboinvest_fetched_at":"2026-01-22T15:30:00+00:00",'
        '"_roboinvest_row_count":0}\n'
        '{"Code":"99840","DiscDate":"2026-01-23",'
        '"_roboinvest_fetched_at":"2026-01-23T15:30:00+00:00"}\n'
        '{"_roboinvest_record_type":"fetch_metadata",'
        '"_roboinvest_target_date":"2026-01-23",'
        '"_roboinvest_fetched_at":"2026-01-23T15:30:00+00:00",'
        '"_roboinvest_row_count":2}\n',
        encoding="utf-8",
    )

    assert exporter.read_existing_disclosed_dates(path) == {
        "2026-01-21",
        "2026-01-22",
    }


def test_attach_fetch_metadata_copies_rows_with_utc_receipt_time() -> None:
    rows = [{"Code": "72030", "DiscDate": "2026-01-21"}]

    tagged = exporter.attach_fetch_metadata(
        rows,
        fetched_at=datetime(2026, 1, 21, 7, tzinfo=UTC),
    )

    assert tagged == [
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "_roboinvest_fetched_at": "2026-01-21T07:00:00+00:00",
        }
    ]
    assert "_roboinvest_fetched_at" not in rows[0]


def test_fetch_metadata_record_preserves_zero_row_fetch() -> None:
    record = exporter.fetch_metadata_record(
        target_date=date(2026, 1, 21),
        fetched_at=datetime(2026, 1, 21, 7, tzinfo=UTC),
        row_count=0,
    )

    assert record == {
        "_roboinvest_record_type": "fetch_metadata",
        "_roboinvest_target_date": "2026-01-21",
        "_roboinvest_fetched_at": "2026-01-21T07:00:00+00:00",
        "_roboinvest_row_count": 0,
    }
