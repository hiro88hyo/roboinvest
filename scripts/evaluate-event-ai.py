#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from event_research_common import (
    EVALUATION_SPLITS,
    EXIT_ARMS_FOR_REPORT,
    entry_arm_allows,
    metrics_for_observations,
    read_jsonl,
    select_observations_for_split,
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
    all_observations = [
        ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)
    ]
    observations, split_info = select_observations_for_split(all_observations, split=args.split)
    labeled = [EventAiLabeledRecord.model_validate(row) for row in read_jsonl(args.labels)]
    labels = {row.event_id: row.label for row in labeled}
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
            {
                "name": "numerical_fields_shuffled",
                "reason": "labels.jsonl evaluation input does not carry feature bundle values",
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
        f"split={args.split} observations={len(observations)} "
        f"rows={len(rows)} output={args.output_dir}"
    )
    return 0


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
