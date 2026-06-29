#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from event_research_common import read_jsonl
from trade_contracts.event_research import EventAiJob


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a retry jobs.jsonl from failed event LLM job records."
    )
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--failures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--error-contains",
        action="append",
        default=[],
        help="Only include failures whose error contains this text. May be repeated.",
    )
    parser.add_argument(
        "--include-all-failures",
        action="store_true",
        help="Include every failed job regardless of error text.",
    )
    args = parser.parse_args()

    failed_job_ids = _failed_job_ids(
        args.failures,
        error_contains=args.error_contains,
        include_all=args.include_all_failures,
    )
    jobs, missing = _retry_jobs(args.jobs, failed_job_ids)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for job in jobs:
            f.write(job.model_dump_json() + "\n")

    print(
        "event_llm_retry_jobs "
        f"failures={len(failed_job_ids)} retry_jobs={len(jobs)} "
        f"missing_original_jobs={len(missing)} output={args.output}"
    )
    return 0 if not missing else 1


def _failed_job_ids(
    path: Path,
    *,
    error_contains: list[str],
    include_all: bool,
) -> set[str]:
    out: set[str] = set()
    for row in read_jsonl(path):
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        if include_all or not error_contains or _error_matches(row, error_contains):
            out.add(job_id)
    return out


def _error_matches(row: dict[str, Any], needles: list[str]) -> bool:
    error = str(row.get("error") or "")
    return any(needle in error for needle in needles)


def _retry_jobs(path: Path, failed_job_ids: set[str]) -> tuple[list[EventAiJob], set[str]]:
    jobs: list[EventAiJob] = []
    remaining = set(failed_job_ids)
    seen: set[str] = set()
    for row in read_jsonl(path):
        job_id = str(row.get("job_id") or "")
        if job_id not in failed_job_ids or job_id in seen:
            continue
        seen.add(job_id)
        remaining.discard(job_id)
        jobs.append(EventAiJob.model_validate(row))
    return jobs, remaining


if __name__ == "__main__":
    raise SystemExit(main())
