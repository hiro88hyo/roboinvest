from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import EventRecord, EventSource, EventType, ObservationRecord


def _load_module():
    path = Path(__file__).resolve().parents[1] / "build-event-llm-jobs.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("build_event_llm_jobs", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_event_llm_jobs = _load_module()


def _event(idx: int) -> EventRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return EventRecord(
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        source=EventSource.FIXTURE,
        raw_document_type="ForecastRevision",
        event_type=EventType.FORECAST_REVISION,
        disclosed_date=at.date().isoformat(),
        disclosed_time="15:30:00",
        disclosed_at=at,
        data_available_at=at,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        raw_source_identifier=f"fixture-{idx}",
        fetched_at=at,
        raw={"ForecastEarningsPerShare": "125"},
    )


def _observation(idx: int) -> ObservationRecord:
    event = _event(idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=event.event_id,
        event_cluster_id=event.event_cluster_id,
        symbol=event.symbol,
        event_type=event.event_type,
        signal_date=event.signal_date,
        entry_date=event.entry_date,
        feature_cutoff_at=event.feature_cutoff_at,
        data_available_at=event.data_available_at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=event.raw_source_identifier,
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )


def test_llm_job_builder_defaults_to_development_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx) for idx in range(40)]
    observations = [_observation(idx) for idx in range(40)]
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    output_path = tmp_path / "jobs.jsonl"
    _write_jsonl(events_path, events)
    _write_jsonl(observations_path, observations)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-event-llm-jobs.py",
            "--events",
            str(events_path),
            "--observations",
            str(observations_path),
            "--output",
            str(output_path),
        ],
    )

    assert build_event_llm_jobs.main() == 0

    job_count = len(output_path.read_text(encoding="utf-8").splitlines())
    assert 0 < job_count < len(observations)


def test_llm_job_builder_can_sample_development_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx) for idx in range(40)]
    observations = [_observation(idx) for idx in range(40)]
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    first_output = tmp_path / "jobs-first.jsonl"
    second_output = tmp_path / "jobs-second.jsonl"
    _write_jsonl(events_path, events)
    _write_jsonl(observations_path, observations)

    for output_path in (first_output, second_output):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "build-event-llm-jobs.py",
                "--events",
                str(events_path),
                "--observations",
                str(observations_path),
                "--output",
                str(output_path),
                "--sample-size",
                "5",
                "--sample-seed",
                "7",
            ],
        )
        assert build_event_llm_jobs.main() == 0

    first_rows = [
        json.loads(line) for line in first_output.read_text(encoding="utf-8").splitlines()
    ]
    second_rows = [
        json.loads(line) for line in second_output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(first_rows) == 5
    assert [row["event_id"] for row in first_rows] == [row["event_id"] for row in second_rows]


def test_llm_job_builder_requires_locked_oos_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    _write_jsonl(events_path, [_event(idx) for idx in range(40)])
    _write_jsonl(observations_path, [_observation(idx) for idx in range(40)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-event-llm-jobs.py",
            "--events",
            str(events_path),
            "--observations",
            str(observations_path),
            "--split",
            "locked-oos",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        build_event_llm_jobs.main()

    assert exc.value.code == 2
