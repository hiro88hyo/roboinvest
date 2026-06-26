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

FEATURE_GROUPS_FOR_NUMERICAL_PLACEBO = (
    "fundamental_features_v0",
    "valuation_features_v0",
    "technical_context_v0",
)


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
        "--placebo-mode",
        choices=["none", "numerical_fields_shuffled"],
        default="none",
        help="Build placebo prompts. Numerical shuffle preserves feature timing metadata.",
    )
    parser.add_argument("--placebo-seed", type=int, default=1)
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
    if args.placebo_mode == "numerical_fields_shuffled":
        observations = _shuffle_numerical_feature_values(
            observations,
            seed=args.placebo_seed,
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
        f"sample_size={args.sample_size or 'all'} placebo_mode={args.placebo_mode} "
        f"count={len(jobs)} output={args.output}"
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


def _shuffle_numerical_feature_values(
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> list[ObservationRecord]:
    rng = random.Random(seed)
    out = [obs.model_copy(deep=True) for obs in observations]
    event_types = sorted({obs.event_type for obs in out}, key=lambda item: item.value)
    for event_type in event_types:
        indexes = [idx for idx, obs in enumerate(out) if obs.event_type == event_type]
        if len(indexes) < 2:
            continue
        for group_name in FEATURE_GROUPS_FOR_NUMERICAL_PLACEBO:
            group = getattr(out[indexes[0]], group_name)
            for field_name in type(group).model_fields:
                feature = getattr(group, field_name)
                if not hasattr(feature, "model_copy") or not hasattr(feature, "value"):
                    continue
                values = [
                    getattr(getattr(out[idx], group_name), field_name).value for idx in indexes
                ]
                shuffled = values[:]
                rng.shuffle(shuffled)
                for idx, value in zip(indexes, shuffled, strict=True):
                    current_group = getattr(out[idx], group_name)
                    current_feature = getattr(current_group, field_name)
                    updated_feature = current_feature.model_copy(update={"value": value})
                    updated_group = current_group.model_copy(update={field_name: updated_feature})
                    out[idx] = out[idx].model_copy(update={group_name: updated_group})
    return out


if __name__ == "__main__":
    raise SystemExit(main())
