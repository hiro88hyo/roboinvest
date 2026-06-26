#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    read_jsonl,
    select_observations_for_split,
)
from strategy_ai.event.prompt import FORBIDDEN_PROMPT_KEYS
from trade_contracts.event_research import EventAiJob, ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit event LLM jobs before model execution.")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--placebo-jobs", type=Path)
    parser.add_argument("--output", type=Path, default=Path("out/event-ai/job-audit.json"))
    parser.add_argument("--provider")
    parser.add_argument("--model-id")
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Expected job split. Default excludes purge windows and locked OOS.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    args = parser.parse_args()
    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")

    jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.jobs)]
    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    errors: list[str] = []
    warnings: list[str] = []
    selected, split_info = select_observations_for_split(observations, split=args.split)
    selected_event_ids = {obs.event_id for obs in selected}
    all_event_ids = {obs.event_id for obs in observations}
    _audit_jobs(
        jobs,
        selected_event_ids=selected_event_ids,
        all_event_ids=all_event_ids,
        provider=args.provider,
        model_id=args.model_id,
        errors=errors,
        warnings=warnings,
    )
    placebo_summary: dict[str, object] | None = None
    if args.placebo_jobs is not None:
        placebo_jobs = [EventAiJob.model_validate(row) for row in read_jsonl(args.placebo_jobs)]
        _audit_jobs(
            placebo_jobs,
            selected_event_ids=selected_event_ids,
            all_event_ids=all_event_ids,
            provider=args.provider,
            model_id=args.model_id,
            errors=errors,
            warnings=warnings,
            label="placebo",
        )
        placebo_summary = _audit_placebo_pairing(jobs, placebo_jobs, errors=errors)

    result = {
        "ok": not errors,
        "job_count": len(jobs),
        "provider_values": sorted({job.model_provider for job in jobs}),
        "model_id_values": sorted({job.model_id for job in jobs}),
        "prompt_version_values": sorted({job.prompt_version for job in jobs}),
        "split": split_info,
        "placebo": placebo_summary,
        "errors": errors,
        "warnings": warnings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        "event_llm_job_audit "
        f"ok={result['ok']} jobs={len(jobs)} errors={len(errors)} output={args.output}"
    )
    return 0 if not errors else 1


def _audit_jobs(
    jobs: list[EventAiJob],
    *,
    selected_event_ids: set[str],
    all_event_ids: set[str],
    provider: str | None,
    model_id: str | None,
    errors: list[str],
    warnings: list[str],
    label: str = "jobs",
) -> None:
    duplicate_job_ids = [
        item for item, count in Counter(job.job_id for job in jobs).items() if count > 1
    ]
    if duplicate_job_ids:
        errors.append(f"{label}: duplicate job_id values: {duplicate_job_ids[:5]}")
    if provider is not None:
        mismatches = sorted({job.model_provider for job in jobs if job.model_provider != provider})
        if mismatches:
            errors.append(f"{label}: provider mismatch: expected={provider!r} got={mismatches}")
    if model_id is not None:
        mismatches = sorted({job.model_id for job in jobs if job.model_id != model_id})
        if mismatches:
            errors.append(f"{label}: model_id mismatch: expected={model_id!r} got={mismatches}")
    model_ids = sorted({job.model_id for job in jobs})
    if len(model_ids) > 1:
        errors.append(f"{label}: multiple model_id values in one run: {model_ids}")
    temperatures = sorted({str(job.temperature) for job in jobs})
    if len(temperatures) > 1:
        errors.append(f"{label}: multiple temperature values in one run: {temperatures}")

    for idx, job in enumerate(jobs):
        prefix = f"{label}[{idx}] job_id={job.job_id}"
        if job.event_id not in all_event_ids:
            errors.append(f"{prefix}: event_id not found in observations: {job.event_id}")
        elif job.event_id not in selected_event_ids:
            errors.append(f"{prefix}: event_id is outside requested split: {job.event_id}")
        actual_hash = hashlib.sha256(job.prompt.encode("utf-8")).hexdigest()
        if actual_hash != job.prompt_hash:
            errors.append(f"{prefix}: prompt_hash mismatch")
        try:
            prompt_payload = json.loads(job.prompt)
        except json.JSONDecodeError as exc:
            errors.append(f"{prefix}: prompt is not valid JSON: {exc}")
            continue
        if not isinstance(prompt_payload, dict):
            errors.append(f"{prefix}: prompt JSON root is not an object")
            continue
        prompt_version = prompt_payload.get("prompt_version")
        if prompt_version != job.prompt_version:
            errors.append(
                f"{prefix}: prompt_version mismatch: "
                f"prompt={prompt_version!r} job={job.prompt_version!r}"
            )
        prompt_event = prompt_payload.get("event")
        if not isinstance(prompt_event, dict) or prompt_event.get("event_id") != job.event_id:
            errors.append(f"{prefix}: prompt event_id does not match job event_id")
        forbidden_keys = _find_forbidden_keys(prompt_payload)
        if forbidden_keys:
            errors.append(f"{prefix}: forbidden prompt keys: {sorted(forbidden_keys)[:10]}")
        if "labels" in prompt_payload:
            errors.append(f"{prefix}: prompt includes labels")
        if job.seed is None:
            warnings.append(f"{prefix}: seed is not set")


def _audit_placebo_pairing(
    jobs: list[EventAiJob],
    placebo_jobs: list[EventAiJob],
    *,
    errors: list[str],
) -> dict[str, object]:
    same_length = len(jobs) == len(placebo_jobs)
    if not same_length:
        errors.append(
            f"placebo: job count mismatch: baseline={len(jobs)} placebo={len(placebo_jobs)}"
        )
    compared = min(len(jobs), len(placebo_jobs))
    event_id_mismatches = [
        idx for idx in range(compared) if jobs[idx].event_id != placebo_jobs[idx].event_id
    ]
    if event_id_mismatches:
        errors.append(f"placebo: event_id order mismatch at indexes {event_id_mismatches[:10]}")
    changed_prompt_hashes = sum(
        1 for idx in range(compared) if jobs[idx].prompt_hash != placebo_jobs[idx].prompt_hash
    )
    if compared and changed_prompt_hashes == 0:
        errors.append("placebo: prompt hashes are identical; placebo did not change prompts")
    return {
        "job_count": len(placebo_jobs),
        "same_length": same_length,
        "event_id_order_mismatch_count": len(event_id_mismatches),
        "changed_prompt_hash_count": changed_prompt_hashes,
    }


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(forbidden in lowered for forbidden in FORBIDDEN_PROMPT_KEYS):
                found.add(str(key))
            found.update(_find_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_keys(item))
    return found


if __name__ == "__main__":
    raise SystemExit(main())
