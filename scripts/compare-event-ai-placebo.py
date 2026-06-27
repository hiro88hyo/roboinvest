#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    EXIT_ARMS_FOR_REPORT,
    metrics_for_observations,
    read_jsonl,
)
from strategy_ai.event.evaluator import ai_arm_allows
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabel,
    EventAiLabeledRecord,
    ExitArm,
    ObservationRecord,
)

LABEL_FIELDS = (
    "fundamental_direction",
    "fundamental_strength",
    "revision_quality",
    "valuation_context",
    "technical_context",
    "expected_horizon",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare real event AI labels with an external placebo label run."
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--real-labels", type=Path, required=True)
    parser.add_argument("--placebo-labels", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--real-name", default="real")
    parser.add_argument("--placebo-name", default="placebo")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Use label split metadata when available.",
    )
    args = parser.parse_args()

    real_labels = _load_labels(args.real_labels, split=args.split)
    placebo_labels = _load_labels(args.placebo_labels, split=args.split)
    common_event_ids = set(real_labels) & set(placebo_labels)
    observations = _load_observations(args.observations, common_event_ids)
    rows = _comparison_rows(
        observations, real_labels, placebo_labels, args.real_name, args.placebo_name
    )
    result = {
        "summary": _summary(
            observations,
            real_labels,
            placebo_labels,
            common_event_ids,
            split=args.split,
            real_name=args.real_name,
            placebo_name=args.placebo_name,
        ),
        "rows": rows,
        "label_distribution": {
            args.real_name: _label_distribution(real_labels, common_event_ids),
            args.placebo_name: _label_distribution(placebo_labels, common_event_ids),
        },
        "distribution_warnings": _distribution_warnings(
            {
                args.real_name: real_labels,
                args.placebo_name: placebo_labels,
            },
            common_event_ids,
        ),
        "top_contributors": _top_contributors(
            observations,
            real_labels,
            placebo_labels,
            top_n=args.top_n,
            real_name=args.real_name,
            placebo_name=args.placebo_name,
        ),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    _write_csv(args.output_csv, rows)
    print(
        "event_ai_placebo_compare "
        f"split={args.split} common_labels={len(common_event_ids)} "
        f"observations={len(observations)} rows={len(rows)}"
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


def _load_observations(path: Path, event_ids: set[str]) -> list[ObservationRecord]:
    observations: list[ObservationRecord] = []
    for row in read_jsonl(path):
        if row.get("event_id") in event_ids:
            observations.append(ObservationRecord.model_validate(row))
    return observations


def _comparison_rows(
    observations: list[ObservationRecord],
    real_labels: dict[str, EventAiLabel],
    placebo_labels: dict[str, EventAiLabel],
    real_name: str,
    placebo_name: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[ObservationRecord]] = {
        f"{real_name}_ai_pass": [],
        f"{real_name}_ai_reject": [],
        f"{placebo_name}_ai_pass": [],
        f"{placebo_name}_ai_reject": [],
        "both_pass": [],
        f"{real_name}_only_pass": [],
        f"{placebo_name}_only_pass": [],
        "neither_pass": [],
    }
    for obs in observations:
        real_pass = _ai_pass(obs, real_labels)
        placebo_pass = _ai_pass(obs, placebo_labels)
        groups[f"{real_name}_ai_pass" if real_pass else f"{real_name}_ai_reject"].append(obs)
        groups[f"{placebo_name}_ai_pass" if placebo_pass else f"{placebo_name}_ai_reject"].append(
            obs
        )
        if real_pass and placebo_pass:
            groups["both_pass"].append(obs)
        elif real_pass:
            groups[f"{real_name}_only_pass"].append(obs)
        elif placebo_pass:
            groups[f"{placebo_name}_only_pass"].append(obs)
        else:
            groups["neither_pass"].append(obs)

    rows: list[dict[str, Any]] = []
    for cohort, items in groups.items():
        for exit_arm in EXIT_ARMS_FOR_REPORT:
            rows.append(
                {
                    "cohort": cohort,
                    "exit_arm": exit_arm.value,
                    **metrics_for_observations(
                        items,
                        exit_arm=exit_arm,
                        include_bootstrap_ci=False,
                    ),
                }
            )
    return rows


def _ai_pass(obs: ObservationRecord, labels: dict[str, EventAiLabel]) -> bool:
    return ai_arm_allows(obs, labels.get(obs.event_id), EntryArm.EVENT_PLUS_AI)


def _summary(
    observations: list[ObservationRecord],
    real_labels: dict[str, EventAiLabel],
    placebo_labels: dict[str, EventAiLabel],
    common_event_ids: set[str],
    *,
    split: str,
    real_name: str,
    placebo_name: str,
) -> dict[str, Any]:
    real_pass = {obs.event_id for obs in observations if _ai_pass(obs, real_labels)}
    placebo_pass = {obs.event_id for obs in observations if _ai_pass(obs, placebo_labels)}
    union = real_pass | placebo_pass
    return {
        "requested_split": split,
        f"{real_name}_label_count": len(real_labels),
        f"{placebo_name}_label_count": len(placebo_labels),
        "common_label_count": len(common_event_ids),
        f"missing_from_{real_name}": len(set(placebo_labels) - set(real_labels)),
        f"missing_from_{placebo_name}": len(set(real_labels) - set(placebo_labels)),
        "matched_observation_count": len(observations),
        f"{real_name}_pass_count": len(real_pass),
        f"{placebo_name}_pass_count": len(placebo_pass),
        "both_pass_count": len(real_pass & placebo_pass),
        f"{real_name}_only_pass_count": len(real_pass - placebo_pass),
        f"{placebo_name}_only_pass_count": len(placebo_pass - real_pass),
        "neither_pass_count": len(common_event_ids - union),
        "pass_jaccard": None if not union else len(real_pass & placebo_pass) / len(union),
        "evaluation_population": "common_label_target_observations",
    }


def _label_distribution(
    labels: dict[str, EventAiLabel],
    event_ids: set[str],
) -> dict[str, Any]:
    selected = [labels[event_id] for event_id in event_ids if event_id in labels]
    out: dict[str, Any] = {
        "label_count": len(selected),
        "confidence_buckets": dict(
            Counter(_confidence_bucket(label.confidence) for label in selected)
        ),
    }
    for field in LABEL_FIELDS:
        out[field] = dict(Counter(str(getattr(label, field)) for label in selected))
    return out


def _distribution_warnings(
    label_sets: dict[str, dict[str, EventAiLabel]],
    event_ids: set[str],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for name, labels in label_sets.items():
        confidence_counts = Counter(
            _confidence_bucket(labels[event_id].confidence)
            for event_id in event_ids
            if event_id in labels
        )
        total = sum(confidence_counts.values())
        non_empty = sum(1 for count in confidence_counts.values() if count > 0)
        max_bucket, max_count = (
            ("", 0) if not confidence_counts else confidence_counts.most_common(1)[0]
        )
        max_share = None if total == 0 else max_count / total
        if non_empty < 2 or (max_share is not None and max_share >= 0.80):
            warnings.append(
                {
                    "name": "confidence_distribution_collapsed",
                    "label_set": name,
                    "bucket": max_bucket,
                    "bucket_share": max_share,
                    "non_empty_bucket_count": non_empty,
                    "message": ("confidence is not discriminative enough for threshold selection"),
                }
            )
    return warnings


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "0.0..0.5"
    if value < 0.7:
        return "0.5..0.7"
    return "0.7..1.0"


def _top_contributors(
    observations: list[ObservationRecord],
    real_labels: dict[str, EventAiLabel],
    placebo_labels: dict[str, EventAiLabel],
    *,
    top_n: int,
    real_name: str,
    placebo_name: str,
) -> dict[str, Any]:
    cohorts: dict[str, list[ObservationRecord]] = {
        "both_pass": [],
        f"{real_name}_only_pass": [],
        f"{placebo_name}_only_pass": [],
    }
    for obs in observations:
        real_pass = _ai_pass(obs, real_labels)
        placebo_pass = _ai_pass(obs, placebo_labels)
        if real_pass and placebo_pass:
            cohorts["both_pass"].append(obs)
        elif real_pass:
            cohorts[f"{real_name}_only_pass"].append(obs)
        elif placebo_pass:
            cohorts[f"{placebo_name}_only_pass"].append(obs)

    return {
        cohort: {
            "top_positive_fixed20": [
                _contributor_row(obs, real_labels, placebo_labels, real_name, placebo_name)
                for obs in sorted(items, key=_fixed20_net_pnl, reverse=True)[:top_n]
            ],
            "top_negative_fixed20": [
                _contributor_row(obs, real_labels, placebo_labels, real_name, placebo_name)
                for obs in sorted(items, key=_fixed20_net_pnl)[:top_n]
            ],
        }
        for cohort, items in cohorts.items()
    }


def _contributor_row(
    obs: ObservationRecord,
    real_labels: dict[str, EventAiLabel],
    placebo_labels: dict[str, EventAiLabel],
    real_name: str,
    placebo_name: str,
) -> dict[str, Any]:
    return {
        "event_id": obs.event_id,
        "symbol": obs.symbol,
        "signal_date": obs.signal_date,
        "event_type": obs.event_type.value,
        "event_subtype": obs.event_subtype,
        "fixed20_return": _label_return(obs, ExitArm.FIXED_20D),
        "fixed20_net_pnl": float(_fixed20_net_pnl(obs)),
        real_name: _label_summary(real_labels.get(obs.event_id)),
        placebo_name: _label_summary(placebo_labels.get(obs.event_id)),
    }


def _label_summary(label: EventAiLabel | None) -> dict[str, Any] | None:
    if label is None:
        return None
    return {
        "fundamental_direction": label.fundamental_direction,
        "fundamental_strength": label.fundamental_strength,
        "expected_horizon": label.expected_horizon,
        "technical_context": label.technical_context,
        "confidence": label.confidence,
    }


def _fixed20_net_pnl(obs: ObservationRecord) -> Decimal:
    value = _as_decimal(_label_return(obs, ExitArm.FIXED_20D))
    if value is None:
        return Decimal("0")
    return Decimal("100000") * (value - Decimal("0.00298"))


def _label_return(obs: ObservationRecord, exit_arm: ExitArm) -> Any:
    key = {
        ExitArm.FIXED_2D: "forward_return_2d",
        ExitArm.FIXED_5D: "forward_return_5d",
        ExitArm.FIXED_10D: "forward_return_10d",
        ExitArm.FIXED_20D: "forward_return_20d",
        ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP: "catastrophic_stop_return_10d",
        ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP: "catastrophic_stop_return_20d",
    }[exit_arm]
    return obs.labels.get(key)


def _as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "cohort",
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
