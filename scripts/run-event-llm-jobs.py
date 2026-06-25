#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

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
    return asyncio.run(_amain(parser.parse_args()))


async def _amain(args: argparse.Namespace) -> int:
    jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.jobs)]
    client = _build_client(args.provider)
    labels: list[EventAiLabeledRecord] = []
    failures: list[dict[str, object]] = []
    started = datetime.now(tz=UTC)
    for job in jobs:
        try:
            raw = await client.complete(job.prompt)
            label = parse_event_ai_label(raw)
            labels.append(
                EventAiLabeledRecord(
                    job_id=job.job_id,
                    event_id=job.event_id,
                    prompt_hash=job.prompt_hash,
                    model_provider=args.provider,
                    model_id=job.model_id,
                    raw_response=raw,
                    label=label,
                    created_at=datetime.now(tz=UTC),
                )
            )
        except (EventAiParseError, LLMError) as exc:
            failures.append(
                {
                    "job_id": job.job_id,
                    "event_id": job.event_id,
                    "prompt_hash": job.prompt_hash,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    write_jsonl(args.output_labels, labels)
    write_jsonl(args.output_failures, failures)
    ended = datetime.now(tz=UTC)
    manifest = {
        "git_commit": _git_commit(),
        "prompt_version": jobs[0].prompt_version if jobs else None,
        "model_id": jobs[0].model_id if jobs else None,
        "model_digest": None,
        "temperature": str(jobs[0].temperature) if jobs else None,
        "seed": jobs[0].seed if jobs else None,
        "completed": len(labels),
        "failed": len(failures),
        "cached": 0,
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
    print(f"event_llm_run completed={len(labels)} failed={len(failures)}")
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


if __name__ == "__main__":
    raise SystemExit(main())
