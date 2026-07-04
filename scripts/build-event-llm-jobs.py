#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path

from event_research_common import (
    EVALUATION_SPLITS,
    observation_split_label,
    read_jsonl,
    read_split_manifest,
    select_observations_for_split,
    write_jsonl,
)
from strategy_ai.event.jobs import build_event_ai_job
from trade_contracts.event_research import EventRecord, EventType, ObservationRecord

FEATURE_GROUPS_FOR_NUMERICAL_PLACEBO = (
    "fundamental_features_v0",
    "valuation_features_v0",
    "technical_context_v0",
)
OFFICIAL_NUMERIC_SUMMARY_PLACEBO_FIELDS = (
    "FEPS",
    "FOP",
    "FNP",
    "FSales",
    "FDivAnn",
    "EPS",
    "BPS",
    "CurFYEn",
    "NxtFYEn",
    "DocType",
    "DiscDate",
    "DiscTime",
    "DiscNo",
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
    parser.add_argument(
        "--balanced-sample-size",
        type=int,
        help="Deterministically sample this many observations balanced by event_type.",
    )
    parser.add_argument("--sample-seed", type=int, default=1)
    parser.add_argument(
        "--event-type",
        action="append",
        choices=[item.value for item in EventType],
        help="Include only this event type. May be repeated.",
    )
    parser.add_argument(
        "--event-subtype",
        action="append",
        help="Include only this exact event_subtype. May be repeated.",
    )
    parser.add_argument(
        "--event-subtype-prefix",
        action="append",
        help="Include event_subtype values starting with this prefix. May be repeated.",
    )
    parser.add_argument(
        "--placebo-mode",
        choices=[
            "none",
            "numerical_fields_shuffled",
            "bundle_shuffled",
            "official_numeric_summary_shuffled",
            "feature_and_official_numeric_shuffled",
        ],
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
    parser.add_argument(
        "--split-manifest",
        type=Path,
        help="Freeze train/validation/locked OOS boundaries from an existing manifest JSON.",
    )
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.sample_size is not None and args.sample_size < 0:
        parser.error("--sample-size must be non-negative")
    if args.balanced_sample_size is not None and args.balanced_sample_size < 0:
        parser.error("--balanced-sample-size must be non-negative")
    if args.sample_size is not None and args.balanced_sample_size is not None:
        parser.error("--sample-size and --balanced-sample-size cannot be used together")
    events = {row["event_id"]: EventRecord.model_validate(row) for row in read_jsonl(args.events)}
    all_observations = [
        ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)
    ]
    observations, split_info = select_observations_for_split(
        all_observations,
        split=args.split,
        fixed_split_manifest=read_split_manifest(args.split_manifest)
        if args.split_manifest
        else None,
    )
    observations = _filter_observations(
        observations,
        event_types=set(args.event_type or []),
        event_subtypes=set(args.event_subtype or []),
        event_subtype_prefixes=tuple(args.event_subtype_prefix or ()),
    )
    filtered_observation_count = len(observations)
    split_manifest = split_info.get("split_manifest", {})
    split_manifest_hash = _stable_hash(split_manifest) if split_manifest else None
    dataset_hash = split_manifest.get("dataset_hash") if split_manifest else None
    if args.balanced_sample_size is not None:
        observations = _balanced_sample_observations(
            observations,
            sample_size=args.balanced_sample_size,
            sample_seed=args.sample_seed,
        )
    else:
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
    if args.placebo_mode == "bundle_shuffled":
        observations = _shuffle_feature_bundles(
            observations,
            seed=args.placebo_seed,
        )
    if args.placebo_mode == "official_numeric_summary_shuffled":
        events = _shuffle_official_numeric_summary_bundles(
            events,
            observations,
            seed=args.placebo_seed,
        )
    if args.placebo_mode == "feature_and_official_numeric_shuffled":
        observations = _shuffle_feature_bundles(
            observations,
            seed=args.placebo_seed,
        )
        events = _shuffle_official_numeric_summary_bundles(
            events,
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
        ).model_copy(
            update={
                "dataset_hash": dataset_hash,
                "split_manifest_hash": split_manifest_hash,
                "split_label": None
                if not split_manifest
                else observation_split_label(obs, split_manifest),
            }
        )
        for obs in observations
        if obs.event_id in events
    ]
    write_jsonl(args.output, jobs)
    print(
        "event_llm_jobs "
        f"split={args.split} observations={split_info['selected_observation_count']} "
        f"filtered_observations={filtered_observation_count} "
        f"sampled_observations={len(observations)} "
        f"sample_size={args.sample_size or args.balanced_sample_size or 'all'} "
        f"balanced={args.balanced_sample_size is not None} placebo_mode={args.placebo_mode} "
        f"count={len(jobs)} output={args.output}"
    )
    return 0


def _filter_observations(
    observations: list[ObservationRecord],
    *,
    event_types: set[str],
    event_subtypes: set[str],
    event_subtype_prefixes: tuple[str, ...],
) -> list[ObservationRecord]:
    out: list[ObservationRecord] = []
    for obs in observations:
        if event_types and obs.event_type.value not in event_types:
            continue
        subtype = obs.event_subtype or ""
        if event_subtypes and subtype not in event_subtypes:
            continue
        if event_subtype_prefixes and not subtype.startswith(event_subtype_prefixes):
            continue
        out.append(obs)
    return out


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


def _balanced_sample_observations(
    observations: list[ObservationRecord],
    *,
    sample_size: int,
    sample_seed: int,
) -> list[ObservationRecord]:
    if sample_size >= len(observations):
        return observations
    rng = random.Random(sample_seed)
    by_type: dict[str, list[ObservationRecord]] = {}
    for obs in observations:
        by_type.setdefault(obs.event_type.value, []).append(obs)
    event_types = sorted(by_type)
    base = sample_size // len(event_types) if event_types else 0
    remainder = sample_size % len(event_types) if event_types else 0
    selected: list[ObservationRecord] = []
    for idx, event_type in enumerate(event_types):
        pool = sorted(by_type[event_type], key=lambda obs: (obs.signal_date, obs.event_id))
        take = min(len(pool), base + (1 if idx < remainder else 0))
        if take:
            selected.extend(rng.sample(pool, take))
    if len(selected) < sample_size:
        selected_ids = {obs.observation_id for obs in selected}
        remaining = [obs for obs in observations if obs.observation_id not in selected_ids]
        selected.extend(rng.sample(remaining, min(sample_size - len(selected), len(remaining))))
    return sorted(selected, key=lambda obs: (obs.signal_date, obs.event_id))


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


def _shuffle_feature_bundles(
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> list[ObservationRecord]:
    rng = random.Random(seed)
    donors = [obs.model_copy(deep=True) for obs in observations]
    out = [obs.model_copy(deep=True) for obs in observations]
    event_types = sorted({obs.event_type for obs in out}, key=lambda item: item.value)
    for event_type in event_types:
        indexes = [idx for idx, obs in enumerate(out) if obs.event_type == event_type]
        if len(indexes) < 2:
            continue
        donor_indexes = indexes[:]
        rng.shuffle(donor_indexes)
        for idx, donor_idx in zip(indexes, donor_indexes, strict=True):
            donor = donors[donor_idx]
            out[idx] = out[idx].model_copy(
                update={
                    "fundamental_features_v0": donor.fundamental_features_v0,
                    "valuation_features_v0": donor.valuation_features_v0,
                    "technical_context_v0": donor.technical_context_v0,
                }
            )
    return out


def _shuffle_official_numeric_summary_bundles(
    events: dict[str, EventRecord],
    observations: list[ObservationRecord],
    *,
    seed: int,
) -> dict[str, EventRecord]:
    rng = random.Random(seed)
    out = dict(events)
    event_types = sorted({obs.event_type for obs in observations}, key=lambda item: item.value)
    for event_type in event_types:
        event_ids = [
            obs.event_id
            for obs in observations
            if obs.event_type == event_type and obs.event_id in events
        ]
        if len(event_ids) < 2:
            continue
        donor_ids = event_ids[:]
        rng.shuffle(donor_ids)
        for event_id, donor_id in zip(event_ids, donor_ids, strict=True):
            target = out[event_id]
            donor = events[donor_id]
            updated_raw = dict(target.raw)
            for field in OFFICIAL_NUMERIC_SUMMARY_PLACEBO_FIELDS:
                if field in donor.raw:
                    updated_raw[field] = donor.raw[field]
                else:
                    updated_raw.pop(field, None)
            out[event_id] = target.model_copy(update={"raw": updated_raw})
    return out


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
