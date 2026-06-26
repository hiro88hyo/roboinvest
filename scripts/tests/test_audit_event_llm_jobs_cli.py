from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_ai.event.jobs import build_event_ai_job
from trade_contracts.event_research import (
    EventRecord,
    EventSource,
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_research_common import select_observations_for_split


def _load_module():
    path = Path(__file__).resolve().parents[1] / "audit-event-llm-jobs.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("audit_event_llm_jobs", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_event_llm_jobs = _load_module()


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
    )


def _observation(idx: int, *, value_offset: int = 0) -> ObservationRecord:
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
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=idx + value_offset, valid=True)
        ),
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )


def test_audit_event_llm_jobs_accepts_development_jobs_and_placebo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event(idx) for idx in range(40)]
    observations = [_observation(idx) for idx in range(40)]
    selected, _ = select_observations_for_split(
        observations,
        split="development",
    )
    selected = selected[:5]
    events_by_id = {event.event_id: event for event in events}
    jobs = [
        build_event_ai_job(
            event=events_by_id[obs.event_id],
            observation=obs,
            model_provider="fixture",
            model_id="fixture-event-labeler-v0",
        )
        for obs in selected
    ]
    placebo_jobs = [
        build_event_ai_job(
            event=events_by_id[obs.event_id],
            observation=_observation(int(obs.event_id.removeprefix("event-")), value_offset=100),
            model_provider="fixture",
            model_id="fixture-event-labeler-v0",
        )
        for obs in selected
    ]
    observations_path = tmp_path / "observations.jsonl"
    jobs_path = tmp_path / "jobs.jsonl"
    placebo_path = tmp_path / "placebo-jobs.jsonl"
    output_path = tmp_path / "audit.json"
    _write_jsonl(observations_path, observations)
    _write_jsonl(jobs_path, jobs)
    _write_jsonl(placebo_path, placebo_jobs)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--observations",
            str(observations_path),
            "--placebo-jobs",
            str(placebo_path),
            "--output",
            str(output_path),
            "--provider",
            "fixture",
            "--model-id",
            "fixture-event-labeler-v0",
        ],
    )

    assert audit_event_llm_jobs.main() == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["job_count"] == 5
    assert report["placebo"]["changed_prompt_hash_count"] == 5


def test_audit_event_llm_jobs_rejects_prompt_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = [_observation(idx) for idx in range(40)]
    selected, _ = select_observations_for_split(
        observations,
        split="development",
    )
    obs = selected[0]
    job = build_event_ai_job(
        event=_event(int(obs.event_id.removeprefix("event-"))),
        observation=obs,
        model_provider="fixture",
        model_id="fixture-event-labeler-v0",
    ).model_copy(update={"prompt_hash": "bad"})
    observations_path = tmp_path / "observations.jsonl"
    jobs_path = tmp_path / "jobs.jsonl"
    output_path = tmp_path / "audit.json"
    _write_jsonl(observations_path, observations)
    _write_jsonl(jobs_path, [job])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--observations",
            str(observations_path),
            "--output",
            str(output_path),
        ],
    )

    assert audit_event_llm_jobs.main() == 1

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert any("prompt_hash mismatch" in error for error in report["errors"])
