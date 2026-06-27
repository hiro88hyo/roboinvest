#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    EXIT_ARMS_FOR_REPORT,
    FEATURE_SCHEMA_VERSION,
    PURGE_TRADING_DAYS,
    entry_arm_allows,
    metrics_for_observations,
    read_jsonl,
)
from strategy_ai.event.evaluator import (
    ai_arm_allows,
    random_threshold_labels_within_event_type,
    shuffle_confidence_within_event_type,
    shuffle_labels_within_event_type,
)
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabel,
    EventAiLabeledRecord,
    ExitArm,
    ObservationRecord,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate event AI labels and placebo baselines.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-ai"))
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Evaluation split. Default excludes locked OOS details.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    labeled = [EventAiLabeledRecord.model_validate(row) for row in read_jsonl(args.labels)]
    labels = {row.event_id: row.label for row in labeled}
    observations, split_info = _load_labeled_observations_for_split(
        args.observations,
        label_event_ids=set(labels),
        split=args.split,
    )
    rows = _evaluate_ai_rows(observations, labels)
    placebos = _evaluate_placebos(observations, labels)
    result = {
        "rows": rows,
        "placebo": placebos["labels_shuffled_within_event_type"],
        "placebos": placebos,
        "confidence_buckets": confidence_buckets(observations, labels),
        "evaluation_split": split_info,
        "unavailable_placebos": [
            {
                "name": "event_title_shuffled",
                "reason": "labels.jsonl evaluation input does not carry event title/text fields",
            },
        ],
        "external_placebos": [
            {
                "name": "numerical_fields_shuffled",
                "how_to_generate": (
                    "build-event-llm-jobs.py --placebo-mode numerical_fields_shuffled"
                ),
            },
            {
                "name": "bundle_shuffled",
                "how_to_generate": "build-event-llm-jobs.py --placebo-mode bundle_shuffled",
            },
            {
                "name": "feature_bundle_proxy_v0",
                "how_to_generate": "build-event-ai-feature-proxy-labels.py",
            },
        ],
        "ai_value_minimum_conditions": {
            "must_beat_event_only": True,
            "must_beat_rule_only": True,
            "must_beat_shuffled_labels": True,
            "must_beat_same_symbol_random_date": True,
            "confidence_monotonicity_required": True,
            "oos_reproduction_required": True,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "event-ai-report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        "event_ai_eval "
        f"split={args.split} labeled_observations={len(observations)} "
        f"rows={len(rows)} output={args.output_dir}"
    )
    return 0


def _load_labeled_observations_for_split(
    path: Path,
    *,
    label_event_ids: set[str],
    split: str,
) -> tuple[list[ObservationRecord], dict[str, Any]]:
    manifest = _split_manifest_from_jsonl(path)
    if not manifest:
        return [], {"requested_split": split, "selected_observation_count": 0}

    selected_labels = _requested_split_labels(split)
    observations: list[ObservationRecord] = []
    counts: dict[str, int] = defaultdict(int)
    selected_observation_count = 0
    selected_symbols: set[str] = set()
    label_target_observation_count = 0

    for row in read_jsonl(path):
        split_label = _raw_observation_split(row, manifest)
        counts[split_label] += 1
        is_selected_split = split == "all" or split_label in selected_labels
        if is_selected_split:
            selected_observation_count += 1
            selected_symbols.add(str(row.get("symbol", "")))
        if is_selected_split and row.get("event_id") in label_event_ids:
            label_target_observation_count += 1
            observations.append(ObservationRecord.model_validate(row))

    return observations, {
        "requested_split": split,
        "selected_observation_count": selected_observation_count,
        "selected_symbol_count": len(selected_symbols),
        "labeled_observation_count": label_target_observation_count,
        "input_label_count": len(label_event_ids),
        "split_counts": dict(counts),
        "split_manifest": manifest,
        "evaluation_population": "label_target_observations",
    }


def _split_manifest_from_jsonl(path: Path) -> dict[str, Any]:
    dates: set[date] = set()
    symbols: set[str] = set()
    count = 0
    digest = hashlib.sha256()
    for row in read_jsonl(path):
        count += 1
        dates.add(date.fromisoformat(str(row["signal_date"])))
        symbols.add(str(row.get("symbol", "")))
        digest.update(
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    if not dates:
        return {}
    ordered_dates = sorted(dates)
    train_end = ordered_dates[int(len(ordered_dates) * 0.60)]
    validation_start = _shift_trading_date(ordered_dates, train_end, PURGE_TRADING_DAYS)
    validation_end = ordered_dates[int(len(ordered_dates) * 0.80)]
    oos_start = _shift_trading_date(ordered_dates, validation_end, PURGE_TRADING_DAYS)
    return {
        "train_start": ordered_dates[0].isoformat(),
        "train_end": train_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
        "locked_oos_start": oos_start.isoformat(),
        "locked_oos_end": ordered_dates[-1].isoformat(),
        "purge_days": PURGE_TRADING_DAYS,
        "dataset_hash": digest.hexdigest(),
        "dataset_hash_algorithm": "jsonl_stream_sha256_v1",
        "split_observation_count": count,
        "split_symbol_count": len(symbols),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def _requested_split_labels(split: str) -> set[str]:
    if split == "development":
        return {"train", "validation"}
    if split == "train":
        return {"train"}
    if split == "validation":
        return {"validation"}
    if split == "locked-oos":
        return {"locked_oos"}
    if split == "all":
        return {"train", "validation", "locked_oos"}
    raise ValueError(f"unsupported evaluation split: {split}")


def _raw_observation_split(row: dict[str, Any], manifest: dict[str, Any]) -> str:
    signal_date = date.fromisoformat(str(row["signal_date"]))
    train_end = date.fromisoformat(manifest["train_end"])
    validation_start = date.fromisoformat(manifest["validation_start"])
    validation_end = date.fromisoformat(manifest["validation_end"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    exit_20d = _raw_label_exit_date(row, 20)
    if signal_date <= train_end:
        if exit_20d is not None and exit_20d >= validation_start:
            return "purge_train_validation"
        return "train"
    if signal_date < validation_start:
        return "purge_train_validation"
    if signal_date <= validation_end:
        if exit_20d is not None and exit_20d >= locked_oos_start:
            return "purge_validation_locked_oos"
        return "validation"
    if signal_date < locked_oos_start:
        return "purge_validation_locked_oos"
    return "locked_oos"


def _raw_label_exit_date(row: dict[str, Any], horizon: int) -> date | None:
    value = row.get("labels", {}).get(f"exit_date_{horizon}d")
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


def _shift_trading_date(dates: list[date], start: date, days: int) -> date:
    idx = dates.index(start)
    return dates[min(idx + days, len(dates) - 1)]


def _evaluate_placebos(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> dict[str, list[dict[str, object]]]:
    return {
        "labels_shuffled_within_event_type": _evaluate_ai_rows(
            observations,
            shuffle_labels_within_event_type(labels, observations, seed=1),
            prefix="labels_shuffled_",
        ),
        "confidence_shuffled_within_event_type": _evaluate_ai_rows(
            observations,
            shuffle_confidence_within_event_type(labels, observations, seed=1),
            prefix="confidence_shuffled_",
        ),
        "random_threshold_within_event_type": _evaluate_ai_rows(
            observations,
            random_threshold_labels_within_event_type(labels, observations, seed=1),
            prefix="random_threshold_",
        ),
    }


def _evaluate_ai_rows(
    observations: list[ObservationRecord],
    labels: dict[str, object],
    *,
    prefix: str = "",
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    arms = (
        EntryArm.EVENT_ONLY,
        EntryArm.EVENT_PLUS_FUNDAMENTAL,
        EntryArm.EVENT_PLUS_TECHNICAL,
        EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
        EntryArm.EVENT_PLUS_AI,
        EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL,
        EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
    )
    for arm in arms:
        selected: list[ObservationRecord] = []
        for obs in observations:
            if arm.value.startswith("event_plus_ai"):
                if ai_arm_allows(obs, labels.get(obs.event_id), arm):  # type: ignore[arg-type]
                    selected.append(obs)
            elif entry_arm_allows(obs, arm):
                selected.append(obs)
        for exit_arm in EXIT_ARMS_FOR_REPORT:
            rows.append(
                {
                    "entry_arm": prefix + arm.value,
                    "exit_arm": exit_arm.value,
                    **metrics_for_observations(
                        selected,
                        exit_arm=exit_arm,
                        include_bootstrap_ci=False,
                    ),
                }
            )
    return rows


def confidence_buckets(
    observations: list[ObservationRecord],
    labels: dict[str, object],
) -> list[dict[str, object]]:
    buckets = {
        "0.0..0.5": [],
        "0.5..0.7": [],
        "0.7..1.0": [],
    }
    for obs in observations:
        label = labels.get(obs.event_id)
        confidence = None if label is None else getattr(label, "confidence", None)
        if confidence is None:
            continue
        if confidence < 0.5:
            buckets["0.0..0.5"].append(obs)
        elif confidence < 0.7:
            buckets["0.5..0.7"].append(obs)
        else:
            buckets["0.7..1.0"].append(obs)
    return [
        {
            "bucket": key,
            **metrics_for_observations(
                items,
                exit_arm=ExitArm.FIXED_10D,
                include_bootstrap_ci=False,
            ),
        }
        for key, items in buckets.items()
    ]


if __name__ == "__main__":
    raise SystemExit(main())
