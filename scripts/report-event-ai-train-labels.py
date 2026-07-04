#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from event_research_common import (
    entry_arm_allows,
    metrics_for_observations,
    read_jsonl,
)
from strategy_ai.event.evaluator import (
    ai_arm_allows,
    shuffle_labels_within_event_type,
)
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabel,
    EventAiLabeledRecord,
    ExitArm,
    ObservationRecord,
)

FIXED_SHORT_EXITS = (ExitArm.FIXED_2D, ExitArm.FIXED_5D, ExitArm.FIXED_10D)
TRAIN_MINIMUM_EFFECT_GATE_EXITS = (ExitArm.FIXED_2D, ExitArm.FIXED_5D)
TRAIN_MINIMUM_EFFECT_GATE_RULE_ARM = EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL
TRAIN_MINIMUM_EFFECT_GATE_AI_ARM = EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL
REPORT_ARMS = (
    EntryArm.EVENT_ONLY,
    EntryArm.EVENT_PLUS_FUNDAMENTAL,
    EntryArm.EVENT_PLUS_TECHNICAL,
    EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
    EntryArm.EVENT_PLUS_AI,
    EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL,
    EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
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
        description="Report train-only event AI label progress and short-exit diagnostics."
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--jobs", type=Path)
    parser.add_argument("--failures", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=("train",),
        default="train",
        help="Only train is supported; do not use partial labels for validation/locked OOS.",
    )
    args = parser.parse_args()

    labels = _load_labels(args.labels, split=args.split)
    jobs = _load_jobs(args.jobs, split=args.split) if args.jobs else {}
    train_event_ids = set(jobs.values()) if jobs else set(labels)
    labels = {event_id: label for event_id, label in labels.items() if event_id in train_event_ids}
    target_event_ids = set(labels) & train_event_ids
    labeled_observations = _load_target_observations(args.observations, target_event_ids)
    failures = _load_failures(args.failures) if args.failures else []

    rows = _evaluation_rows(labeled_observations, labels)
    shuffled_rows = _evaluation_rows(
        labeled_observations,
        shuffle_labels_within_event_type(labels, labeled_observations, seed=1),
        prefix="labels_shuffled_",
    )
    gate = _train_minimum_effect_gate(
        labeled_observations,
        labels,
        jobs=jobs if args.jobs else None,
    )
    result = {
        "summary": {
            "split": args.split,
            "report_scope": "train_only_partial_safe",
            "selected_train_observations": len(train_event_ids) if args.jobs else None,
            "labeled_train_observations": len(labeled_observations),
            "input_label_count": len(labels),
            "partial_train_report": True
            if not args.jobs
            else len(labeled_observations) < len(train_event_ids),
            "split_info": {
                "requested_split": args.split,
                "source": "jobs_jsonl" if args.jobs else "labels_jsonl_only",
                "train_job_count": len(jobs) if args.jobs else None,
                "train_event_id_count": len(train_event_ids),
            },
        },
        "job_progress": _job_progress(jobs, labels, failures) if args.jobs else None,
        "label_distribution": _label_distribution(labels.values()),
        "confidence_buckets": _confidence_bucket_distribution(labels.values()),
        "ai_selection": _ai_selection(labeled_observations, labels),
        "train_minimum_effect_gate": gate,
        "rows": rows,
        "placebos": {
            "labels_shuffled_within_event_type": shuffled_rows,
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(args.output_csv, rows, shuffled_rows, gate)
    train_count = len(train_event_ids) if args.jobs else "unknown"
    print(
        "event_ai_train_report "
        f"train_observations={train_count} labels={len(labels)} "
        f"matched={len(labeled_observations)} rows={len(rows)} "
        f"train_minimum_effect_gate={gate['status']} output={args.output_json}"
    )
    return 0


def _load_labels(path: Path, *, split: str) -> dict[str, EventAiLabel]:
    labels: dict[str, EventAiLabel] = {}
    for row in read_jsonl(path):
        record = EventAiLabeledRecord.model_validate(row)
        if record.split_label is not None and record.split_label != split:
            continue
        labels[record.event_id] = record.label
    return labels


def _load_jobs(path: Path | None, *, split: str) -> dict[str, str]:
    if path is None:
        return {}
    jobs: dict[str, str] = {}
    for row in read_jsonl(path):
        split_label = row.get("split_label")
        if split_label is not None and split_label != split:
            continue
        job_id = str(row.get("job_id") or "")
        event_id = str(row.get("event_id") or "")
        if job_id and event_id:
            jobs[job_id] = event_id
    return jobs


def _load_target_observations(
    path: Path,
    target_event_ids: set[str],
) -> list[ObservationRecord]:
    if not target_event_ids:
        return []
    observations: list[ObservationRecord] = []
    remaining = set(target_event_ids)
    for row in read_jsonl(path):
        event_id = str(row.get("event_id") or "")
        if event_id not in remaining:
            continue
        observations.append(ObservationRecord.model_validate(row))
        remaining.discard(event_id)
        if not remaining:
            break
    return observations


def _load_failures(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return list(read_jsonl(path))


def _job_progress(
    jobs: dict[str, str],
    labels: dict[str, EventAiLabel],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    job_ids = set(jobs)
    completed_event_ids = set(labels)
    completed_job_ids = {
        job_id for job_id, event_id in jobs.items() if event_id in completed_event_ids
    }
    failure_rows = [row for row in failures if str(row.get("job_id") or "") in job_ids]
    parse_failures = [
        row for row in failure_rows if "EventAiParseError" in str(row.get("error") or "")
    ]
    total_jobs = len(jobs)
    completed = len(completed_job_ids)
    return {
        "total_train_jobs": total_jobs,
        "completed_train_jobs": completed,
        "missing_train_jobs": max(total_jobs - completed, 0),
        "completion_rate": None if total_jobs == 0 else completed / total_jobs,
        "failure_records": len(failure_rows),
        "parse_failure_records": len(parse_failures),
        "parse_failure_rate": None if total_jobs == 0 else len(parse_failures) / total_jobs,
    }


def _label_distribution(labels: Any) -> dict[str, dict[str, int]]:
    materialized = list(labels)
    distributions: dict[str, dict[str, int]] = {}
    for field in LABEL_FIELDS:
        distributions[field] = dict(Counter(str(getattr(label, field)) for label in materialized))
    risk_flags: Counter[str] = Counter()
    for label in materialized:
        risk_flags.update(str(flag) for flag in label.risk_flags)
    distributions["risk_flags"] = dict(risk_flags)
    return distributions


def _confidence_bucket_distribution(labels: Any) -> dict[str, int]:
    buckets = Counter(_confidence_bucket(label.confidence) for label in labels)
    return {bucket: buckets.get(bucket, 0) for bucket in ("0.0..0.5", "0.5..0.7", "0.7..1.0")}


def _confidence_bucket(value: float) -> str:
    if value < 0.5:
        return "0.0..0.5"
    if value < 0.7:
        return "0.5..0.7"
    return "0.7..1.0"


def _ai_selection(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
) -> dict[str, Any]:
    passed: list[ObservationRecord] = []
    rejected: list[ObservationRecord] = []
    for obs in observations:
        if ai_arm_allows(obs, labels.get(obs.event_id), EntryArm.EVENT_PLUS_AI):
            passed.append(obs)
        else:
            rejected.append(obs)
    total = len(observations)
    return {
        "ai_pass": len(passed),
        "ai_reject": len(rejected),
        "ai_pass_rate": None if total == 0 else len(passed) / total,
        "ai_reject_rate": None if total == 0 else len(rejected) / total,
    }


def _train_minimum_effect_gate(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
    *,
    jobs: dict[str, str] | None,
) -> dict[str, Any]:
    """Train-only pre-registered gate for continuing the AI arm.

    The gate is intentionally unavailable for partial train labels. It compares
    rule-only `fundamental_and_technical` with the second-stage AI filter on
    the same train-only labeled cohort, and checks whether AI rejects genuinely
    bad rule-pass trades.
    """

    if not jobs:
        return {
            "status": "INSUFFICIENT_LABELS",
            "reason": "jobs_required_to_confirm_100pct_train_completion",
            "required_completion_rate": 1.0,
            "completion_rate": None,
            "candidate_exit": None,
            "exit_checks": [],
        }

    train_event_ids = set(jobs.values())
    completed_event_ids = set(labels) & train_event_ids
    matched_observation_event_ids = {obs.event_id for obs in observations}
    completion_rate = None if not jobs else len(completed_event_ids) / len(jobs)
    all_jobs_completed = all(event_id in labels for event_id in jobs.values())
    all_completed_labels_matched = completed_event_ids <= matched_observation_event_ids
    if not all_jobs_completed or not all_completed_labels_matched:
        return {
            "status": "INSUFFICIENT_LABELS",
            "reason": "train_labels_not_100pct_complete",
            "required_completion_rate": 1.0,
            "completion_rate": completion_rate,
            "train_jobs": len(jobs),
            "completed_train_jobs": sum(1 for event_id in jobs.values() if event_id in labels),
            "matched_labeled_observations": len(observations),
            "candidate_exit": None,
            "exit_checks": [],
        }

    rule_selected = _select_for_arm(observations, labels, TRAIN_MINIMUM_EFFECT_GATE_RULE_ARM)
    ai_selected = _select_for_arm(observations, labels, TRAIN_MINIMUM_EFFECT_GATE_AI_ARM)
    excluded = [
        obs
        for obs in rule_selected
        if not ai_arm_allows(obs, labels.get(obs.event_id), EntryArm.EVENT_PLUS_AI)
    ]

    checks: list[dict[str, Any]] = []
    passing_exits: list[dict[str, Any]] = []
    for exit_arm in TRAIN_MINIMUM_EFFECT_GATE_EXITS:
        rule_metrics = metrics_for_observations(
            rule_selected,
            exit_arm=exit_arm,
            include_bootstrap_ci=False,
        )
        ai_metrics = metrics_for_observations(
            ai_selected,
            exit_arm=exit_arm,
            include_bootstrap_ci=False,
        )
        excluded_metrics = metrics_for_observations(
            excluded,
            exit_arm=exit_arm,
            include_bootstrap_ci=False,
        )
        pf_improvement = _float_diff(
            ai_metrics.get("profit_factor"),
            rule_metrics.get("profit_factor"),
        )
        net_not_below_rule = _net_not_below_rule(ai_metrics, rule_metrics)
        excluded_pf_below_one = _pf_below_one(excluded_metrics.get("profit_factor"))
        check = {
            "exit_arm": exit_arm.value,
            "rule_only": _gate_metric_summary(rule_metrics),
            "rule_plus_ai": _gate_metric_summary(ai_metrics),
            "ai_rejected_rule_pass": _gate_metric_summary(excluded_metrics),
            "pf_improvement": pf_improvement,
            "pf_improvement_required": 0.10,
            "pf_improvement_pass": pf_improvement is not None and pf_improvement >= 0.10,
            "net_pnl_not_below_rule_pass": net_not_below_rule,
            "ai_rejected_pf_below_1_pass": excluded_pf_below_one,
        }
        check["pass"] = (
            check["pf_improvement_pass"]
            and check["net_pnl_not_below_rule_pass"]
            and check["ai_rejected_pf_below_1_pass"]
        )
        checks.append(check)
        if check["pass"]:
            passing_exits.append(check)

    if passing_exits:
        candidate = passing_exits[0]
        return {
            "status": "PASS",
            "reason": "fixed_2d_or_fixed_5d_meets_minimum_effect",
            "required_completion_rate": 1.0,
            "completion_rate": completion_rate,
            "candidate_exit": candidate["exit_arm"],
            "exit_checks": checks,
        }

    return {
        "status": "FAIL",
        "reason": "no_fixed_2d_or_fixed_5d_exit_meets_minimum_effect",
        "required_completion_rate": 1.0,
        "completion_rate": completion_rate,
        "candidate_exit": None,
        "exit_checks": checks,
    }


def _gate_metric_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": metrics.get("trade_count"),
        "net_pnl": metrics.get("net_pnl"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
    }


def _float_diff(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _net_not_below_rule(ai_metrics: dict[str, Any], rule_metrics: dict[str, Any]) -> bool:
    ai_net = ai_metrics.get("net_pnl")
    rule_net = rule_metrics.get("net_pnl")
    if ai_net is None or rule_net is None:
        return False
    return float(ai_net) >= float(rule_net)


def _pf_below_one(value: Any) -> bool:
    if value is None:
        return False
    return float(value) < 1.0


def _evaluation_rows(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for arm in REPORT_ARMS:
        selected = _select_for_arm(observations, labels, arm)
        for exit_arm in FIXED_SHORT_EXITS:
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


def _select_for_arm(
    observations: list[ObservationRecord],
    labels: dict[str, EventAiLabel],
    arm: EntryArm,
) -> list[ObservationRecord]:
    selected: list[ObservationRecord] = []
    for obs in observations:
        if arm.value.startswith("event_plus_ai"):
            if ai_arm_allows(obs, labels.get(obs.event_id), arm):
                selected.append(obs)
        elif entry_arm_allows(obs, arm):
            selected.append(obs)
    return selected


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    shuffled_rows: list[dict[str, Any]],
    gate: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "entry_arm",
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
        "train_minimum_effect_gate",
        "train_minimum_effect_gate_exit",
        "train_minimum_effect_gate_reason",
    )
    annotated_rows = [
        {
            **row,
            "train_minimum_effect_gate": gate.get("status"),
            "train_minimum_effect_gate_exit": gate.get("candidate_exit"),
            "train_minimum_effect_gate_reason": gate.get("reason"),
        }
        for row in [*rows, *shuffled_rows]
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(annotated_rows)


if __name__ == "__main__":
    raise SystemExit(main())
