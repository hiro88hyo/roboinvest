from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import EventAiJob


def _load_module():
    path = Path(__file__).resolve().parents[1] / "build-event-llm-retry-jobs.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("build_event_llm_retry_jobs", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_event_llm_retry_jobs = _load_module()


def _job(idx: int) -> EventAiJob:
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
        split_label="train",
        model_provider="fixture",
        model_id="fixture-model",
        temperature=Decimal("0"),
        seed=1,
        created_at=at,
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


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_retry_jobs_filters_failures_by_error_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    output_path = tmp_path / "retry.jsonl"
    _write_jsonl(jobs_path, [_job(idx) for idx in range(3)])
    _write_jsonl(
        failures_path,
        [
            {"job_id": "job-0", "error": "EventAiParseError: invalid json"},
            {"job_id": "job-1", "error": "timeout"},
            {"job_id": "job-0", "error": "EventAiParseError: invalid json"},
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-event-llm-retry-jobs.py",
            "--jobs",
            str(jobs_path),
            "--failures",
            str(failures_path),
            "--output",
            str(output_path),
            "--error-contains",
            "EventAiParseError",
        ],
    )

    assert build_event_llm_retry_jobs.main() == 0

    rows = _read_jsonl(output_path)
    assert [row["job_id"] for row in rows] == ["job-0"]


def test_retry_jobs_returns_nonzero_when_original_job_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    output_path = tmp_path / "retry.jsonl"
    _write_jsonl(jobs_path, [_job(0)])
    _write_jsonl(failures_path, [{"job_id": "job-missing", "error": "timeout"}])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-event-llm-retry-jobs.py",
            "--jobs",
            str(jobs_path),
            "--failures",
            str(failures_path),
            "--output",
            str(output_path),
            "--include-all-failures",
        ],
    )

    assert build_event_llm_retry_jobs.main() == 1
    assert output_path.read_text(encoding="utf-8") == ""
