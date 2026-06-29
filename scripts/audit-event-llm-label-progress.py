#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from event_research_common import read_jsonl
from trade_contracts.event_research import EventAiJob, EventAiLabeledRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit event LLM label progress by split.")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--failures", type=Path)
    parser.add_argument("--output", type=Path, default=Path("out/event-ai/label-progress.json"))
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit non-zero when any job is missing a successful label.",
    )
    args = parser.parse_args()

    jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.jobs)]
    labels = (
        [EventAiLabeledRecord.model_validate(row) for row in read_jsonl(args.labels)]
        if args.labels.exists()
        else []
    )
    failures = (
        read_jsonl(args.failures) if args.failures is not None and args.failures.exists() else []
    )
    result = progress_report(jobs, labels, failures)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        "event_llm_label_progress "
        f"complete={result['complete']} completed={result['completed_jobs']} "
        f"missing={result['missing_jobs']} output={args.output}"
    )
    return 1 if args.require_complete and not result["complete"] else 0


def progress_report(
    jobs: list[EventAiJob],
    labels: list[EventAiLabeledRecord],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    jobs_by_key = {_job_key(job): job for job in jobs}
    label_keys = {_label_key(label) for label in labels}
    completed_keys = label_keys & set(jobs_by_key)
    missing_keys = set(jobs_by_key) - completed_keys
    failure_job_ids = Counter(str(row.get("job_id")) for row in failures if row.get("job_id"))
    by_split: dict[str, dict[str, Any]] = {}
    for split in sorted({job.split_label for job in jobs}):
        split_jobs = {key for key, job in jobs_by_key.items() if job.split_label == split}
        split_completed = split_jobs & completed_keys
        split_missing = split_jobs - completed_keys
        by_split[split] = {
            "total_jobs": len(split_jobs),
            "completed_jobs": len(split_completed),
            "missing_jobs": len(split_missing),
            "completion_rate": None if not split_jobs else len(split_completed) / len(split_jobs),
            "failure_records": sum(
                failure_job_ids.get(jobs_by_key[key].job_id, 0) for key in split_jobs
            ),
        }
    missing_examples = [
        {
            "job_id": jobs_by_key[key].job_id,
            "event_id": jobs_by_key[key].event_id,
            "split_label": jobs_by_key[key].split_label,
        }
        for key in sorted(missing_keys)[:20]
    ]
    completed = len(completed_keys)
    total = len(jobs_by_key)
    return {
        "complete": completed == total,
        "total_jobs": total,
        "completed_jobs": completed,
        "missing_jobs": total - completed,
        "completion_rate": None if total == 0 else completed / total,
        "label_count": len(labels),
        "label_matched_job_count": completed,
        "label_unmatched_job_count": len(label_keys - set(jobs_by_key)),
        "failure_record_count": len(failures),
        "by_split": by_split,
        "missing_examples": missing_examples,
    }


def _job_key(job: EventAiJob) -> tuple[str, str, str, str]:
    return (job.job_id, job.prompt_hash, job.model_provider, job.model_id)


def _label_key(label: EventAiLabeledRecord) -> tuple[str, str, str, str]:
    return (label.job_id, label.prompt_hash, label.model_provider, label.model_id)


if __name__ == "__main__":
    raise SystemExit(main())
