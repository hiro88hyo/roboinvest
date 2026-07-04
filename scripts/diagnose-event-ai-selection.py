#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from event_research_common import EVALUATION_SPLITS, metrics_for_observations, read_jsonl
from strategy_ai.event.evaluator import (
    ai_arm_allows,
    fundamental_rule_allows,
    technical_veto_allows,
)
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabel,
    EventAiLabeledRecord,
    ExitArm,
    ObservationRecord,
)

EXIT_ARMS = (
    ExitArm.FIXED_2D,
    ExitArm.FIXED_5D,
    ExitArm.FIXED_10D,
    ExitArm.FIXED_20D,
    ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP,
    ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose event AI label selection behavior.")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Use label split metadata when available.",
    )
    args = parser.parse_args()

    labels = _load_labels(args.labels, split=args.split)
    observations = _load_label_target_observations(args.observations, labels)
    rows = _diagnostic_rows(observations, labels)
    result = {
        "summary": {
            "requested_split": args.split,
            "input_label_count": len(labels),
            "matched_observation_count": len(observations),
            "event_type_counts": dict(Counter(obs.event_type.value for obs in observations)),
            "label_direction_counts": dict(
                Counter(label.fundamental_direction for label in labels.values())
            ),
            "label_strength_counts": dict(
                Counter(str(label.fundamental_strength) for label in labels.values())
            ),
            "label_horizon_counts": dict(
                Counter(label.expected_horizon for label in labels.values())
            ),
        },
        "rows": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    _write_csv(args.output_csv, rows)
    print(
        "event_ai_selection_diagnostics "
        f"labels={len(labels)} observations={len(observations)} rows={len(rows)}"
    )
    return 0


def _load_labels(path: Path, *, split: str) -> dict[str, EventAiLabel]:
    labels: dict[str, EventAiLabel] = {}
    for row in read_jsonl(path):
        record = EventAiLabeledRecord.model_validate(row)
        if record.split_label is not None and not _split_allows(record.split_label, split):
            continue
        labels[record.event_id] = record.label
    return labels


def _split_allows(label: str, split: str) -> bool:
    if split == "all":
        return True
    if split == "development":
        return label in {"train", "validation"}
    if split == "locked-oos":
        return label == "locked_oos"
    return label == split


def _load_label_target_observations(
    path: Path,
    labels: dict[str, EventAiLabel],
) -> list[ObservationRecord]:
    observations: list[ObservationRecord] = []
    label_ids = set(labels)
    for row in read_jsonl(path):
        if row.get("event_id") in label_ids:
            observations.append(ObservationRecord.model_validate(row))
    return observations


def _diagnostic_rows(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups = _groups(observations, labels)
    for group_name, items in groups.items():
        for event_type in ["all", *sorted({obs.event_type.value for obs in observations})]:
            event_items = (
                items
                if event_type == "all"
                else [obs for obs in items if obs.event_type.value == event_type]
            )
            for exit_arm in EXIT_ARMS:
                out.append(
                    {
                        "group": group_name,
                        "event_type": event_type,
                        "exit_arm": exit_arm.value,
                        **metrics_for_observations(
                            event_items,
                            exit_arm=exit_arm,
                            include_bootstrap_ci=False,
                        ),
                    }
                )
    return out


def _groups(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> dict[str, list[ObservationRecord]]:
    groups: dict[str, list[ObservationRecord]] = {
        "all_labeled": [],
        "ai_pass": [],
        "ai_reject": [],
        "fundamental_pass": [],
        "technical_pass": [],
        "ai_and_fundamental": [],
        "ai_and_technical": [],
        "fundamental_and_technical": [],
        "ai_fundamental_and_technical": [],
        "technical_without_ai": [],
        "ai_without_technical": [],
    }
    for obs in observations:
        label = labels.get(obs.event_id)
        ai_pass = ai_arm_allows(obs, label, EntryArm.EVENT_PLUS_AI)
        fundamental_pass = fundamental_rule_allows(obs)
        technical_pass = technical_veto_allows(obs)
        groups["all_labeled"].append(obs)
        groups["ai_pass" if ai_pass else "ai_reject"].append(obs)
        if fundamental_pass:
            groups["fundamental_pass"].append(obs)
        if technical_pass:
            groups["technical_pass"].append(obs)
        if ai_pass and fundamental_pass:
            groups["ai_and_fundamental"].append(obs)
        if ai_pass and technical_pass:
            groups["ai_and_technical"].append(obs)
        if fundamental_pass and technical_pass:
            groups["fundamental_and_technical"].append(obs)
        if ai_pass and fundamental_pass and technical_pass:
            groups["ai_fundamental_and_technical"].append(obs)
        if technical_pass and not ai_pass:
            groups["technical_without_ai"].append(obs)
        if ai_pass and not technical_pass:
            groups["ai_without_technical"].append(obs)
    return groups


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "group",
        "event_type",
        "exit_arm",
        "trade_count",
        "net_pnl",
        "profit_factor",
        "max_drawdown",
        "average_return",
        "median_return",
        "hit_rate",
    )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
