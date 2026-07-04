#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from itertools import pairwise
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

EXIT_ARMS = (ExitArm.FIXED_10D, ExitArm.FIXED_20D)
LABEL_FIELDS = (
    "fundamental_direction",
    "fundamental_strength",
    "revision_quality",
    "valuation_context",
    "technical_context",
    "expected_horizon",
)
FEATURE_FIELDS = (
    ("profit_revision_pct", "fundamental_features_v0"),
    ("operating_profit_revision_pct", "fundamental_features_v0"),
    ("forecast_eps_revision_absolute", "fundamental_features_v0"),
    ("sales_revision_pct", "fundamental_features_v0"),
    ("forecast_per", "valuation_features_v0"),
    ("sector_relative_forecast_per", "valuation_features_v0"),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose fixed-cohort event AI smoke labels before larger LLM runs."
    )
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
            "evaluation_population": "label_target_observations",
        },
        "rows": rows,
        "findings": _findings(rows),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    _write_csv(args.output_csv, rows)
    print(
        "event_ai_smoke_diagnostics "
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
    label_ids = set(labels)
    observations: list[ObservationRecord] = []
    for row in read_jsonl(path):
        if row.get("event_id") in label_ids:
            observations.append(ObservationRecord.model_validate(row))
    return observations


def _diagnostic_rows(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category, groups in _groups(observations, labels).items():
        for value, items in sorted(groups.items()):
            for exit_arm in EXIT_ARMS:
                rows.append(
                    {
                        "category": category,
                        "value": value,
                        "exit_arm": exit_arm.value,
                        **metrics_for_observations(
                            items,
                            exit_arm=exit_arm,
                            include_bootstrap_ci=False,
                        ),
                    }
                )
    return rows


def _groups(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> dict[str, dict[str, list[ObservationRecord]]]:
    groups: dict[str, dict[str, list[ObservationRecord]]] = defaultdict(lambda: defaultdict(list))
    for obs in observations:
        label = labels[obs.event_id]
        ai_pass = ai_arm_allows(obs, label, EntryArm.EVENT_PLUS_AI)
        technical_pass = technical_veto_allows(obs)
        fundamental_pass = fundamental_rule_allows(obs)
        _add(groups, "population", "all_labeled", obs)
        _add(groups, "entry_selection", "ai_pass" if ai_pass else "ai_reject", obs)
        if technical_pass:
            _add(groups, "entry_selection", "technical_pass", obs)
        if fundamental_pass:
            _add(groups, "entry_selection", "fundamental_pass", obs)
        if ai_pass and technical_pass:
            _add(groups, "entry_selection", "ai_and_technical", obs)
        if ai_pass and not technical_pass:
            _add(groups, "entry_selection", "ai_without_technical", obs)
        if technical_pass and not ai_pass:
            _add(groups, "entry_selection", "technical_without_ai", obs)
        if ai_pass and technical_pass and fundamental_pass:
            _add(groups, "entry_selection", "ai_fundamental_technical", obs)

        _add(groups, "event_type", obs.event_type.value, obs)
        _add(groups, "event_subtype", str(obs.event_subtype), obs)
        _add(groups, "confidence_bucket", _confidence_bucket(label.confidence), obs)
        for field in LABEL_FIELDS:
            _add(groups, f"label_{field}", str(getattr(label, field)), obs)
        for field, group_name in FEATURE_FIELDS:
            _add(groups, f"feature_{field}", _feature_bucket(obs, group_name, field), obs)
    return {category: dict(values) for category, values in groups.items()}


def _add(
    groups: dict[str, dict[str, list[ObservationRecord]]],
    category: str,
    value: str,
    obs: ObservationRecord,
) -> None:
    groups[category][value].append(obs)


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "0.0..0.5"
    if value < 0.7:
        return "0.5..0.7"
    return "0.7..1.0"


def _feature_bucket(obs: ObservationRecord, group_name: str, field: str) -> str:
    group = getattr(obs, group_name)
    feature = getattr(group, field)
    if not getattr(feature, "valid", False):
        return "invalid_or_missing"
    value = _as_decimal(getattr(feature, "value", None))
    if value is None:
        return "invalid_or_missing"
    if value < 0:
        return "negative"
    if value > 0:
        return "positive"
    return "zero"


def _as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = {(row["category"], row["value"], row["exit_arm"]): row for row in rows}
    findings: list[dict[str, Any]] = []
    findings.append(
        _compare(
            index,
            name="ai_vs_technical_fixed20",
            left=("entry_selection", "ai_pass", "fixed_20d"),
            right=("entry_selection", "technical_pass", "fixed_20d"),
            expected="left_gt_right",
        )
    )
    findings.append(
        _compare(
            index,
            name="positive_vs_negative_label_fixed20",
            left=("label_fundamental_direction", "positive", "fixed_20d"),
            right=("label_fundamental_direction", "negative", "fixed_20d"),
            expected="left_gt_right",
        )
    )
    findings.append(
        _compare(
            index,
            name="strength3_vs_strength2_fixed20",
            left=("label_fundamental_strength", "3", "fixed_20d"),
            right=("label_fundamental_strength", "2", "fixed_20d"),
            expected="left_gt_right",
        )
    )
    findings.append(
        _compare(
            index,
            name="favorable_vs_extended_technical_label_fixed20",
            left=("label_technical_context", "favorable", "fixed_20d"),
            right=("label_technical_context", "extended", "fixed_20d"),
            expected="left_gt_right",
        )
    )
    findings.append(_confidence_monotonicity(index))
    return findings


def _compare(
    index: dict[tuple[str, str, str], dict[str, Any]],
    *,
    name: str,
    left: tuple[str, str, str],
    right: tuple[str, str, str],
    expected: str,
) -> dict[str, Any]:
    left_row = index.get(left, {})
    right_row = index.get(right, {})
    left_pnl = left_row.get("net_pnl")
    right_pnl = right_row.get("net_pnl")
    passed = left_pnl is not None and right_pnl is not None and float(left_pnl) > float(right_pnl)
    return {
        "name": name,
        "expected": expected,
        "passed": passed,
        "left": _finding_row(left_row),
        "right": _finding_row(right_row),
    }


def _confidence_monotonicity(
    index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    buckets = ("0.0..0.5", "0.5..0.7", "0.7..1.0")
    values = [
        index.get(("confidence_bucket", bucket, "fixed_20d"), {}).get("average_return")
        for bucket in buckets
    ]
    comparable = [value for value in values if value is not None]
    passed = len(comparable) == len(values) and all(
        float(left) <= float(right) for left, right in pairwise(values)
    )
    return {
        "name": "confidence_bucket_monotonic_fixed20",
        "expected": "average_return_non_decreasing",
        "passed": passed,
        "buckets": list(buckets),
        "average_returns": values,
    }


def _finding_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": row.get("trade_count"),
        "net_pnl": row.get("net_pnl"),
        "profit_factor": row.get("profit_factor"),
        "average_return": row.get("average_return"),
        "hit_rate": row.get("hit_rate"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "category",
        "value",
        "exit_arm",
        "event_count",
        "duplicate_trade_count",
        "trade_count",
        "net_pnl",
        "profit_factor",
        "max_drawdown",
        "average_return",
        "median_return",
        "hit_rate",
        "positive_month_ratio",
        "worst_month",
    )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
