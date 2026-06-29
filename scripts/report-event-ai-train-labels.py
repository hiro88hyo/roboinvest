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
    _write_csv(args.output_csv, rows, shuffled_rows)
    train_count = len(train_event_ids) if args.jobs else "unknown"
    print(
        "event_ai_train_report "
        f"train_observations={train_count} labels={len(labels)} "
        f"matched={len(labeled_observations)} rows={len(rows)} output={args.output_json}"
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
    )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        writer.writerows(shuffled_rows)


if __name__ == "__main__":
    raise SystemExit(main())
