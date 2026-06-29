from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import EventAiJob, EventAiLabel, EventAiLabeledRecord, EventType


def _load_module():
    path = Path(__file__).resolve().parents[1] / "audit-event-llm-label-progress.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("audit_event_llm_label_progress", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_event_llm_label_progress = _load_module()


def _job(idx: int, *, split: str = "train") -> EventAiJob:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    return EventAiJob(
        job_id=f"job-{idx}",
        event_id=f"event-{idx}",
        prompt_version="event_ai_v0",
        prompt_hash=f"hash-{idx}",
        prompt=f"prompt {idx}",
        feature_schema_version="event_research_v0",
        feature_cutoff_at=at,
        dataset_hash="dataset-hash",
        split_manifest_hash="split-manifest-hash",
        split_label=split,
        model_provider="fixture",
        model_id="fixture-model",
        temperature=Decimal("0"),
        seed=1,
        created_at=at,
    )


def _label(job: EventAiJob) -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.EARNINGS_RESULT,
        fundamental_direction="positive",
        fundamental_strength=2,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon="5d",
        risk_flags=[],
        confidence=0.7,
        rationale="fixture",
    )
    return EventAiLabeledRecord(
        job_id=job.job_id,
        event_id=job.event_id,
        prompt_hash=job.prompt_hash,
        model_provider=job.model_provider,
        model_id=job.model_id,
        raw_response=label.model_dump_json(),
        label=label,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(
            row.model_dump_json() if hasattr(row, "model_dump_json") else json.dumps(row)
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def test_event_llm_label_progress_reports_missing_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = [_job(0), _job(1), _job(2, split="validation")]
    jobs_path = tmp_path / "jobs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    output_path = tmp_path / "progress.json"
    _write_jsonl(jobs_path, jobs)
    _write_jsonl(labels_path, [_label(jobs[0])])
    _write_jsonl(failures_path, [{"job_id": "job-1", "error": "timeout"}])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-event-llm-label-progress.py",
            "--jobs",
            str(jobs_path),
            "--labels",
            str(labels_path),
            "--failures",
            str(failures_path),
            "--output",
            str(output_path),
        ],
    )

    assert audit_event_llm_label_progress.main() == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert report["completed_jobs"] == 1
    assert report["missing_jobs"] == 2
    assert report["by_split"]["train"]["failure_records"] == 1
    assert report["by_split"]["validation"]["missing_jobs"] == 1


def test_event_llm_label_progress_require_complete_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_path = tmp_path / "progress.json"
    _write_jsonl(jobs_path, [_job(0)])
    labels_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit-event-llm-label-progress.py",
            "--jobs",
            str(jobs_path),
            "--labels",
            str(labels_path),
            "--output",
            str(output_path),
            "--require-complete",
        ],
    )

    assert audit_event_llm_label_progress.main() == 1
