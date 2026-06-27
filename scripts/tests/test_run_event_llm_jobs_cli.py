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
    path = Path(__file__).resolve().parents[1] / "run-event-llm-jobs.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("run_event_llm_jobs", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_event_llm_jobs = _load_module()


class _BadJsonClient:
    async def complete(self, prompt: str) -> str:
        return "not json"


def _job(idx: int) -> EventAiJob:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC)
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
        model_id="fixture-event-labeler-v0",
        temperature=Decimal("0"),
        seed=None,
        created_at=at,
    )


def _openai_job(idx: int) -> EventAiJob:
    return _job(idx).model_copy(
        update={
            "model_provider": "openai_compatible",
            "model_id": "local-model",
        }
    )


def _write_jobs(path: Path, jobs: list[EventAiJob]) -> None:
    path.write_text(
        "\n".join(job.model_dump_json() for job in jobs) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_event_llm_runner_resumes_cached_labels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    manifest_path = tmp_path / "manifest.json"
    _write_jobs(jobs_path, [_job(idx) for idx in range(3)])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "fixture",
            "--output-labels",
            str(labels_path),
            "--output-failures",
            str(failures_path),
            "--output-manifest",
            str(manifest_path),
            "--max-jobs",
            "1",
        ],
    )
    assert run_event_llm_jobs.main() == 0
    assert len(_read_jsonl(labels_path)) == 1

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "fixture",
            "--output-labels",
            str(labels_path),
            "--output-failures",
            str(failures_path),
            "--output-manifest",
            str(manifest_path),
        ],
    )
    assert run_event_llm_jobs.main() == 0

    labels = _read_jsonl(labels_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(labels) == 3
    assert all(label["cache_key"] for label in labels)
    assert manifest["cached"] == 1
    assert manifest["completed"] == 2
    assert manifest["labels_total"] == 3


def test_event_llm_runner_no_resume_overwrites_labels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    manifest_path = tmp_path / "manifest.json"
    _write_jobs(jobs_path, [_job(idx) for idx in range(2)])
    labels_path.write_text('{"stale": true}\n', encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "fixture",
            "--output-labels",
            str(labels_path),
            "--output-failures",
            str(failures_path),
            "--output-manifest",
            str(manifest_path),
            "--no-resume",
            "--max-jobs",
            "1",
        ],
    )

    assert run_event_llm_jobs.main() == 0

    labels = _read_jsonl(labels_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(labels) == 1
    assert labels[0]["job_id"] == "job-0"
    assert labels[0]["prompt_version"] == "event_ai_v0"
    assert labels[0]["feature_schema_version"] == "event_research_v0"
    assert labels[0]["dataset_hash"] == "dataset-hash"
    assert labels[0]["split_manifest_hash"] == "split-manifest-hash"
    assert labels[0]["split_label"] == "train"
    assert labels[0]["temperature"] == "0"
    assert manifest["cached"] == 0
    assert manifest["completed"] == 1


def test_event_llm_runner_records_raw_parse_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    manifest_path = tmp_path / "manifest.json"
    _write_jobs(jobs_path, [_job(0)])
    monkeypatch.setattr(
        run_event_llm_jobs,
        "_build_client",
        lambda provider, jobs: _BadJsonClient(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "fixture",
            "--output-labels",
            str(labels_path),
            "--output-failures",
            str(failures_path),
            "--output-manifest",
            str(manifest_path),
        ],
    )

    assert run_event_llm_jobs.main() == 0

    failures = _read_jsonl(failures_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert failures[0]["error"] == "EventAiParseError: invalid json"
    assert failures[0]["raw_response"] == "not json"
    assert failures[0]["raw_response_length"] == 8
    assert failures[0]["dataset_hash"] == "dataset-hash"
    assert failures[0]["split_label"] == "train"
    assert manifest["failed"] == 1


def test_event_llm_runner_rejects_provider_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    _write_jobs(jobs_path, [_openai_job(0)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "fixture",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_event_llm_jobs.main()

    assert exc.value.code != 0


def test_event_llm_runner_preflights_local_llm_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs_path = tmp_path / "jobs.jsonl"
    _write_jobs(jobs_path, [_openai_job(0)])
    for name in (
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_MODEL",
        "LOCAL_LLM_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run-event-llm-jobs.py",
            "--jobs",
            str(jobs_path),
            "--provider",
            "openai_compatible",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        run_event_llm_jobs.main()

    assert "LOCAL_LLM_BASE_URL" in str(exc.value)
