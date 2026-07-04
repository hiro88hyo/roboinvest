#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from event_research_common import read_jsonl, write_jsonl
from strategy_ai.config import StrategyAiSettings
from strategy_ai.event.cache import event_ai_cache_key
from strategy_ai.event.parser import EventAiParseError, parse_event_ai_label
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.fixture import FixtureLLMClient
from strategy_ai.llm.openai_compatible import OpenAICompatibleClient
from trade_contracts.event_research import EventAiJob, EventAiLabeledRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Run event LLM jobs.")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--provider", choices=["fixture", "openai_compatible"], default="fixture")
    parser.add_argument("--output-labels", type=Path, default=Path("out/event-ai/labels.jsonl"))
    parser.add_argument("--output-failures", type=Path, default=Path("out/event-ai/failures.jsonl"))
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("out/event-ai/run-manifest.json"),
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        help="Run at most this many uncached jobs. Useful for local LLM smoke tests.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        help="Bounded LLM request concurrency. Defaults to LOCAL_LLM_MAX_CONCURRENCY.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing labels and overwrite outputs from scratch.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip /v1/models preflight for openai_compatible provider.",
    )
    args = parser.parse_args()
    if args.max_jobs is not None and args.max_jobs < 0:
        parser.error("--max-jobs must be non-negative")
    if args.max_concurrency is not None and args.max_concurrency < 1:
        parser.error("--max-concurrency must be >= 1")
    return asyncio.run(_amain(args))


async def _amain(args: argparse.Namespace) -> int:
    jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.jobs)]
    _validate_jobs(jobs, args.provider)
    resume = not args.no_resume
    labels, cached_keys = _load_existing_labels(
        args.output_labels,
        jobs=jobs,
        provider=args.provider,
        resume=resume,
    )
    failures: list[dict[str, object]] = []
    if not resume:
        write_jsonl(args.output_labels, [])
    args.output_failures.parent.mkdir(parents=True, exist_ok=True)
    args.output_failures.write_text("", encoding="utf-8")
    started = datetime.now(tz=UTC)
    cached = 0
    pending_jobs: list[EventAiJob] = []
    for job in jobs:
        cache_key = _job_cache_key(job, args.provider)
        if cache_key in cached_keys:
            cached += 1
            continue
        if args.max_jobs is not None and len(pending_jobs) >= args.max_jobs:
            break
        pending_jobs.append(job)

    client = _build_client(args.provider, pending_jobs) if pending_jobs else None
    preflight: dict[str, object] = {"enabled": False}
    completed = 0
    max_concurrency = _max_concurrency(args.provider, args.max_concurrency)
    if pending_jobs and client is None:
        raise LLMError("LLM client is not configured")
    if pending_jobs and args.provider == "openai_compatible" and not args.skip_preflight:
        preflight = await _preflight_openai_compatible(client)
    semaphore = asyncio.Semaphore(max_concurrency)
    tasks = [
        asyncio.create_task(
            _run_one_job(
                job,
                provider=args.provider,
                client=client,
                semaphore=semaphore,
            )
        )
        for job in pending_jobs
    ]
    for task in asyncio.as_completed(tasks):
        record, failure = await task
        if record is not None:
            labels.append(record)
            cached_keys.add(record.cache_key)
            _append_jsonl(args.output_labels, record)
            completed += 1
        if failure is not None:
            failures.append(failure)
            _append_jsonl(args.output_failures, failure)
    ended = datetime.now(tz=UTC)
    manifest = {
        "git_commit": _git_commit(),
        "prompt_version": jobs[0].prompt_version if jobs else None,
        "model_id": jobs[0].model_id if jobs else None,
        "model_digest": None,
        "temperature": str(jobs[0].temperature) if jobs else None,
        "seed": jobs[0].seed if jobs else None,
        "total_jobs": len(jobs),
        "attempted": len(pending_jobs),
        "completed": completed,
        "failed": len(failures),
        "cached": cached,
        "labels_total": len(labels),
        "resume_enabled": resume,
        "max_jobs": args.max_jobs,
        "max_concurrency": max_concurrency,
        "preflight": preflight,
        "start_time": started.isoformat(),
        "end_time": ended.isoformat(),
        "cache_key_example": None
        if not jobs
        else event_ai_cache_key(
            prompt_hash=jobs[0].prompt_hash,
            model_provider=args.provider,
            model_id=jobs[0].model_id,
            temperature=jobs[0].temperature,
            seed=jobs[0].seed,
        ),
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(
        "event_llm_run "
        f"attempted={len(pending_jobs)} completed={completed} failed={len(failures)} "
        f"cached={cached} labels_total={len(labels)}"
    )
    return 0


async def _preflight_openai_compatible(client: Any) -> dict[str, object]:
    started = datetime.now(tz=UTC)
    try:
        result = await client.preflight()
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"openai-compatible preflight failed: {exc}") from exc
    return {
        "enabled": True,
        **result,
        "checked_at": started.isoformat(),
    }


async def _run_one_job(
    job: EventAiJob,
    *,
    provider: str,
    client: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[EventAiLabeledRecord | None, dict[str, object] | None]:
    cache_key = _job_cache_key(job, provider)
    try:
        async with semaphore:
            raw = await client.complete(job.prompt)
    except LLMError as exc:
        return (
            None,
            _failure_record(
                job,
                cache_key=cache_key,
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
    try:
        label = parse_event_ai_label(raw)
        return (
            EventAiLabeledRecord(
                job_id=job.job_id,
                event_id=job.event_id,
                prompt_version=job.prompt_version,
                prompt_hash=job.prompt_hash,
                cache_key=cache_key,
                feature_schema_version=job.feature_schema_version,
                feature_cutoff_at=job.feature_cutoff_at,
                dataset_hash=job.dataset_hash,
                split_manifest_hash=job.split_manifest_hash,
                split_label=job.split_label,
                model_provider=provider,
                model_id=job.model_id,
                temperature=job.temperature,
                seed=job.seed,
                raw_response=raw,
                label=label,
                created_at=datetime.now(tz=UTC),
            ),
            None,
        )
    except EventAiParseError as exc:
        return (
            None,
            _failure_record(
                job,
                cache_key=cache_key,
                error=f"{type(exc).__name__}: {exc}",
                raw_response=raw,
            ),
        )


def _failure_record(
    job: EventAiJob,
    *,
    cache_key: str,
    error: str,
    raw_response: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "job_id": job.job_id,
        "event_id": job.event_id,
        "prompt_version": job.prompt_version,
        "prompt_hash": job.prompt_hash,
        "cache_key": cache_key,
        "feature_schema_version": job.feature_schema_version,
        "feature_cutoff_at": job.feature_cutoff_at.isoformat(),
        "dataset_hash": job.dataset_hash,
        "split_manifest_hash": job.split_manifest_hash,
        "split_label": job.split_label,
        "model_provider": job.model_provider,
        "model_id": job.model_id,
        "temperature": str(job.temperature),
        "seed": job.seed,
        "error": error,
    }
    if raw_response is not None:
        record["raw_response"] = raw_response
        record["raw_response_length"] = len(raw_response)
    return record


def _max_concurrency(provider: str, override: int | None) -> int:
    if override is not None:
        return override
    if provider == "fixture":
        return 1
    settings = StrategyAiSettings(_env_file=None)
    return max(settings.local_llm_max_concurrency, 1)


def _build_client(provider: str, jobs: list[EventAiJob]):
    if provider == "fixture":
        responses = (
            json.dumps(
                {
                    "event_type": "forecast_revision",
                    "fundamental_direction": "positive",
                    "fundamental_strength": 2,
                    "revision_quality": "medium",
                    "valuation_context": "fair",
                    "technical_context": "neutral",
                    "expected_horizon": "10d",
                    "risk_flags": [],
                    "confidence": 0.7,
                    "rationale": "fixture deterministic label",
                }
            ),
        )
        return FixtureLLMClient(responses=responses)
    settings = StrategyAiSettings(
        _env_file=None,
        llm_provider="openai_compatible",
        ai_temperature=jobs[0].temperature if jobs else Decimal("0"),
    )
    missing = [
        name
        for name, value in (
            ("LOCAL_LLM_BASE_URL", settings.local_llm_base_url),
            ("LOCAL_LLM_MODEL", settings.local_llm_model),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"missing required local LLM env: {', '.join(missing)}")
    if jobs and settings.local_llm_model != jobs[0].model_id:
        raise SystemExit(
            "LOCAL_LLM_MODEL must match jobs model_id: "
            f"{settings.local_llm_model!r} != {jobs[0].model_id!r}"
        )
    return OpenAICompatibleClient(
        base_url=settings.local_llm_base_url,
        api_key=settings.local_llm_api_key,
        model=settings.local_llm_model,
        timeout_seconds=settings.local_llm_timeout_seconds,
        temperature=jobs[0].temperature if jobs else Decimal("0"),
        seed=jobs[0].seed if jobs else None,
        max_output_tokens=settings.local_llm_max_output_tokens,
        max_concurrency=settings.local_llm_max_concurrency,
    )


def _validate_jobs(jobs: list[EventAiJob], provider: str) -> None:
    provider_mismatches = sorted(
        {job.model_provider for job in jobs if job.model_provider != provider}
    )
    if provider_mismatches:
        raise SystemExit(
            f"--provider {provider!r} does not match job model_provider values: "
            f"{provider_mismatches}"
        )
    model_ids = sorted({job.model_id for job in jobs})
    if len(model_ids) > 1:
        raise SystemExit(f"all jobs in one run must use one model_id: {model_ids}")
    temperatures = sorted({str(job.temperature) for job in jobs})
    if len(temperatures) > 1:
        raise SystemExit(f"all jobs in one run must use one temperature: {temperatures}")


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _job_cache_key(job: EventAiJob, provider: str) -> str:
    return event_ai_cache_key(
        prompt_hash=job.prompt_hash,
        model_provider=provider,
        model_id=job.model_id,
        temperature=job.temperature,
        seed=job.seed,
    )


def _load_existing_labels(
    path: Path,
    *,
    jobs: list[EventAiJob],
    provider: str,
    resume: bool,
) -> tuple[list[EventAiLabeledRecord], set[str]]:
    if not resume or not path.exists():
        return [], set()
    jobs_by_legacy_key = {
        (job.job_id, job.prompt_hash, provider, job.model_id): _job_cache_key(job, provider)
        for job in jobs
    }
    labels: list[EventAiLabeledRecord] = []
    cache_keys: set[str] = set()
    for row in read_jsonl(path):
        label = EventAiLabeledRecord.model_validate(row)
        cache_key = label.cache_key or jobs_by_legacy_key.get(
            (label.job_id, label.prompt_hash, label.model_provider, label.model_id)
        )
        if cache_key is None:
            continue
        labels.append(label.model_copy(update={"cache_key": cache_key}))
        cache_keys.add(cache_key)
    return labels, cache_keys


def _append_jsonl(path: Path, row: EventAiLabeledRecord | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(row, "model_dump_json"):
        line = row.model_dump_json()
    else:
        line = json.dumps(row, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
