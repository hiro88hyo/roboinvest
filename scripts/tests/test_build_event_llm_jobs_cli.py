from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import (
    EventRecord,
    EventSource,
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
)


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


def _event(
    idx: int,
    *,
    event_type: EventType = EventType.FORECAST_REVISION,
    event_subtype: str | None = None,
) -> EventRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return EventRecord(
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        source=EventSource.FIXTURE,
        raw_document_type="ForecastRevision"
        if event_type == EventType.FORECAST_REVISION
        else "DividendRevision",
        event_type=event_type,
        event_subtype=event_subtype,
        disclosed_date=at.date().isoformat(),
        disclosed_time="15:30:00",
        disclosed_at=at,
        data_available_at=at,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        raw_source_identifier=f"fixture-{idx}",
        fetched_at=at,
        raw={
            "ForecastEarningsPerShare": "125",
            "EPS": str(100 + idx),
            "BPS": str(1000 + idx),
            "DiscNo": f"disc-{idx}",
            "DocType": event_subtype or "ForecastRevision",
        },
    )


def _observation(
    idx: int,
    *,
    event_type: EventType = EventType.FORECAST_REVISION,
    event_subtype: str | None = None,
) -> ObservationRecord:
    event = _event(idx, event_type=event_type, event_subtype=event_subtype)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=event.event_id,
        event_cluster_id=event.event_cluster_id,
        symbol=event.symbol,
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        signal_date=event.signal_date,
        entry_date=event.entry_date,
        feature_cutoff_at=event.feature_cutoff_at,
        data_available_at=event.data_available_at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=event.raw_source_identifier,
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=idx, valid=True)
        ),
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
    assert all(row["dataset_hash"] for row in first_rows)
    assert all(row["split_manifest_hash"] for row in first_rows)
    assert {row["split_label"] for row in first_rows} <= {"train", "validation"}


def test_llm_job_builder_can_balanced_sample_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[EventRecord] = []
    observations: list[ObservationRecord] = []
    for idx in range(60):
        event_type = EventType.FORECAST_REVISION if idx % 2 == 0 else EventType.DIVIDEND_REVISION
        events.append(_event(idx, event_type=event_type))
        observations.append(_observation(idx, event_type=event_type))
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
            "--balanced-sample-size",
            "10",
            "--sample-seed",
            "11",
        ],
    )

    assert build_event_llm_jobs.main() == 0

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    event_types = [json.loads(row["prompt"])["event"]["event_type"] for row in rows]
    assert len(rows) == 10
    assert event_types.count(EventType.FORECAST_REVISION.value) == 5
    assert event_types.count(EventType.DIVIDEND_REVISION.value) == 5


def test_llm_job_builder_can_filter_event_type_and_subtype_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[EventRecord] = []
    observations: list[ObservationRecord] = []
    specs = (
        (EventType.EARNINGS_RESULT, "3QFinancialStatements_Consolidated_JP"),
        (EventType.EARNINGS_RESULT, "FYFinancialStatements_Consolidated_JP"),
        (EventType.EARNINGS_RESULT, "1QFinancialStatements_Consolidated_JP"),
        (EventType.FORECAST_REVISION, "EarnForecastRevision"),
    )
    for idx in range(80):
        event_type, event_subtype = specs[idx % len(specs)]
        events.append(_event(idx, event_type=event_type, event_subtype=event_subtype))
        observations.append(_observation(idx, event_type=event_type, event_subtype=event_subtype))
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
            "--event-type",
            EventType.EARNINGS_RESULT.value,
            "--event-subtype-prefix",
            "3Q",
            "--sample-size",
            "5",
            "--sample-seed",
            "13",
        ],
    )

    assert build_event_llm_jobs.main() == 0

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    prompt_events = [json.loads(row["prompt"])["event"] for row in rows]
    observations_by_event_id = {obs.event_id: obs for obs in observations}
    assert len(rows) == 5
    assert {event["event_type"] for event in prompt_events} == {EventType.EARNINGS_RESULT.value}
    assert {observations_by_event_id[row["event_id"]].event_subtype for row in rows} == {
        "3QFinancialStatements_Consolidated_JP"
    }


def test_llm_job_builder_can_shuffle_numerical_feature_placebo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx) for idx in range(40)]
    observations = [_observation(idx) for idx in range(40)]
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    baseline_output = tmp_path / "jobs-baseline.jsonl"
    placebo_output = tmp_path / "jobs-placebo.jsonl"
    _write_jsonl(events_path, events)
    _write_jsonl(observations_path, observations)

    for output_path, extra_args in (
        (baseline_output, []),
        (
            placebo_output,
            [
                "--placebo-mode",
                "numerical_fields_shuffled",
                "--placebo-seed",
                "3",
            ],
        ),
    ):
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
                *extra_args,
            ],
        )
        assert build_event_llm_jobs.main() == 0

    baseline_rows = [
        json.loads(line) for line in baseline_output.read_text(encoding="utf-8").splitlines()
    ]
    placebo_rows = [
        json.loads(line) for line in placebo_output.read_text(encoding="utf-8").splitlines()
    ]
    baseline_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in baseline_rows
    ]
    placebo_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in placebo_rows
    ]
    assert [row["event_id"] for row in baseline_rows] == [row["event_id"] for row in placebo_rows]
    assert sorted(baseline_values) == sorted(placebo_values)
    assert baseline_values != placebo_values


def test_llm_job_builder_can_shuffle_official_numeric_summary_placebo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx, event_type=EventType.EARNINGS_RESULT) for idx in range(40)]
    observations = [_observation(idx, event_type=EventType.EARNINGS_RESULT) for idx in range(40)]
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    baseline_output = tmp_path / "jobs-baseline.jsonl"
    placebo_output = tmp_path / "jobs-placebo.jsonl"
    _write_jsonl(events_path, events)
    _write_jsonl(observations_path, observations)

    for output_path, extra_args in (
        (baseline_output, []),
        (
            placebo_output,
            [
                "--placebo-mode",
                "official_numeric_summary_shuffled",
                "--placebo-seed",
                "5",
            ],
        ),
    ):
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
                *extra_args,
            ],
        )
        assert build_event_llm_jobs.main() == 0

    baseline_rows = [
        json.loads(line) for line in baseline_output.read_text(encoding="utf-8").splitlines()
    ]
    placebo_rows = [
        json.loads(line) for line in placebo_output.read_text(encoding="utf-8").splitlines()
    ]
    baseline_eps = [
        json.loads(row["prompt"])["official_numeric_summary"]["EPS"] for row in baseline_rows
    ]
    placebo_eps = [
        json.loads(row["prompt"])["official_numeric_summary"]["EPS"] for row in placebo_rows
    ]
    baseline_feature_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in baseline_rows
    ]
    placebo_feature_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in placebo_rows
    ]
    assert [row["event_id"] for row in baseline_rows] == [row["event_id"] for row in placebo_rows]
    assert sorted(baseline_eps) == sorted(placebo_eps)
    assert baseline_eps != placebo_eps
    assert baseline_feature_values == placebo_feature_values


def test_llm_job_builder_can_shuffle_feature_and_official_numeric_placebo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx, event_type=EventType.EARNINGS_RESULT) for idx in range(40)]
    observations = [_observation(idx, event_type=EventType.EARNINGS_RESULT) for idx in range(40)]
    events_path = tmp_path / "events.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    baseline_output = tmp_path / "jobs-baseline.jsonl"
    placebo_output = tmp_path / "jobs-placebo.jsonl"
    _write_jsonl(events_path, events)
    _write_jsonl(observations_path, observations)

    for output_path, extra_args in (
        (baseline_output, []),
        (
            placebo_output,
            [
                "--placebo-mode",
                "feature_and_official_numeric_shuffled",
                "--placebo-seed",
                "7",
            ],
        ),
    ):
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
                *extra_args,
            ],
        )
        assert build_event_llm_jobs.main() == 0

    baseline_rows = [
        json.loads(line) for line in baseline_output.read_text(encoding="utf-8").splitlines()
    ]
    placebo_rows = [
        json.loads(line) for line in placebo_output.read_text(encoding="utf-8").splitlines()
    ]
    baseline_eps = [
        json.loads(row["prompt"])["official_numeric_summary"]["EPS"] for row in baseline_rows
    ]
    placebo_eps = [
        json.loads(row["prompt"])["official_numeric_summary"]["EPS"] for row in placebo_rows
    ]
    baseline_feature_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in baseline_rows
    ]
    placebo_feature_values = [
        json.loads(row["prompt"])["fundamental_features_v0"]["profit_revision_pct"]["value"]
        for row in placebo_rows
    ]
    assert [row["event_id"] for row in baseline_rows] == [row["event_id"] for row in placebo_rows]
    assert sorted(baseline_eps) == sorted(placebo_eps)
    assert sorted(baseline_feature_values) == sorted(placebo_feature_values)
    assert baseline_eps != placebo_eps
    assert baseline_feature_values != placebo_feature_values


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
