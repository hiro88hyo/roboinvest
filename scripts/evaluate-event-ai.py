#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from event_research_common import (
    EXIT_ARMS_FOR_REPORT,
    entry_arm_allows,
    metrics_for_observations,
    read_jsonl,
)
from strategy_ai.event.evaluator import ai_arm_allows, shuffle_labels_within_event_type
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabeledRecord,
    ExitArm,
    ObservationRecord,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate event AI labels and placebo baselines.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("out/event-ai"))
    args = parser.parse_args()

    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    labeled = [EventAiLabeledRecord.model_validate(row) for row in read_jsonl(args.labels)]
    labels = {row.event_id: row.label for row in labeled}
    rows = _evaluate_ai_rows(observations, labels)
    placebo = _evaluate_ai_rows(
        observations,
        shuffle_labels_within_event_type(labels, observations, seed=1),
        prefix="shuffled_label_",
    )
    result = {
        "rows": rows,
        "placebo": placebo,
        "confidence_buckets": confidence_buckets(observations, labels),
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
    print(f"event_ai_eval rows={len(rows)} output={args.output_dir}")
    return 0


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
                    **metrics_for_observations(selected, exit_arm=exit_arm),
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
            **metrics_for_observations(items, exit_arm=ExitArm.FIXED_10D),
        }
        for key, items in buckets.items()
    ]


if __name__ == "__main__":
    raise SystemExit(main())
