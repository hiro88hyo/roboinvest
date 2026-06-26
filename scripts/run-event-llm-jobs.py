#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from event_research_common import read_jsonl, write_jsonl
from strategy_ai.config import StrategyAiSettings
from strategy_ai.event.cache import event_ai_cache_key
from strategy_ai.event.parser import EventAiParseError, parse_event_ai_label
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.factory import build_llm_client
from strategy_ai.llm.fixture import FixtureLLMClient
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
        "--no-resume",
        action="store_true",
        help="Ignore existing labels and overwrite outputs from scratch.",
    )
    args = parser.parse_args()
    if args.max_jobs is not None and args.max_jobs < 0:
        parser.error("--max-jobs must be non-negative")
    return asyncio.run(_amain(args))


async def _amain(args: argparse.Namespace) -> int:
    jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.jobs)]
    client = _build_client(args.provider)
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
    attempted = 0
    completed = 0
    cached = 0
    for job in jobs:
        cache_key = _job_cache_key(job, args.provider)
        if cache_key in cached_keys:
            cached += 1
            continue
        if args.max_jobs is not None and attempted >= args.max_jobs:
            break
        attempted += 1
        try:
            raw = await client.complete(job.prompt)
            label = parse_event_ai_label(raw)
            record = EventAiLabeledRecord(
                job_id=job.job_id,
                event_id=job.event_id,
                prompt_hash=job.prompt_hash,
                cache_key=cache_key,
                model_provider=args.provider,
                model_id=job.model_id,
                raw_response=raw,
                label=label,
                created_at=datetime.now(tz=UTC),
            )
            labels.append(record)
            cached_keys.add(cache_key)
            _append_jsonl(args.output_labels, record)
            completed += 1
        except (EventAiParseError, LLMError) as exc:
            failure = {
                "job_id": job.job_id,
                "event_id": job.event_id,
                "prompt_hash": job.prompt_hash,
                "cache_key": cache_key,
                "error": f"{type(exc).__name__}: {exc}",
            }
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
        "attempted": attempted,
        "completed": completed,
        "failed": len(failures),
        "cached": cached,
        "labels_total": len(labels),
        "resume_enabled": resume,
        "max_jobs": args.max_jobs,
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
        f"attempted={attempted} completed={completed} failed={len(failures)} "
        f"cached={cached} labels_total={len(labels)}"
    )
    return 0


def _build_client(provider: str):
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
    settings = StrategyAiSettings(_env_file=None, llm_provider="openai_compatible")
    return build_llm_client(settings)


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
