#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
from decimal import Decimal
from pathlib import Path

from event_research_common import (
    EVALUATION_SPLITS,
    read_jsonl,
    select_observations_for_split,
    write_jsonl,
)
from strategy_ai.event.jobs import build_event_ai_job
from trade_contracts.event_research import EventRecord, ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic event LLM jobs.")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("out/event-ai/jobs.jsonl"))
    parser.add_argument("--model-provider", default="fixture")
    parser.add_argument("--model-id", default="fixture-event-labeler-v0")
    parser.add_argument("--temperature", default="0")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--sample-size",
        type=int,
        help="Deterministically sample this many observations after split filtering.",
    )
    parser.add_argument("--sample-seed", type=int, default=1)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Job split. Default excludes purge windows and locked OOS.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.sample_size is not None and args.sample_size < 0:
        parser.error("--sample-size must be non-negative")
    events = {row["event_id"]: EventRecord.model_validate(row) for row in read_jsonl(args.events)}
    all_observations = [
        ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)
    ]
    observations, split_info = select_observations_for_split(all_observations, split=args.split)
    observations = _sample_observations(
        observations,
        sample_size=args.sample_size,
        sample_seed=args.sample_seed,
    )
    jobs = [
        build_event_ai_job(
            event=events[obs.event_id],
            observation=obs,
            model_provider=args.model_provider,
            model_id=args.model_id,
            temperature=Decimal(args.temperature),
            seed=args.seed,
        )
        for obs in observations
        if obs.event_id in events
    ]
    write_jsonl(args.output, jobs)
    print(
        "event_llm_jobs "
        f"split={args.split} observations={split_info['selected_observation_count']} "
        f"sample_size={args.sample_size or 'all'} count={len(jobs)} output={args.output}"
    )
    return 0


def _sample_observations(
    observations: list[ObservationRecord],
    *,
    sample_size: int | None,
    sample_seed: int,
) -> list[ObservationRecord]:
    if sample_size is None:
        return observations
    if sample_size >= len(observations):
        return observations
    rng = random.Random(sample_seed)
    ordered = sorted(observations, key=lambda obs: (obs.signal_date, obs.event_id))
    indexes = sorted(rng.sample(range(len(ordered)), sample_size))
    return [ordered[idx] for idx in indexes]


if __name__ == "__main__":
    raise SystemExit(main())
