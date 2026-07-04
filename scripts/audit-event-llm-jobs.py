#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    FEATURE_SCHEMA_VERSION,
    PURGE_TRADING_DAYS,
    read_jsonl,
)
from strategy_ai.event.prompt import FORBIDDEN_PROMPT_KEYS
from trade_contracts.event_research import EventAiJob


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
    errors: list[str] = []
    warnings: list[str] = []
    split_info, selected_event_ids, all_event_ids = _stream_observation_split_info(
        args.observations,
        requested_split=args.split,
        event_ids={job.event_id for job in jobs},
    )
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


def _stream_observation_split_info(
    path: Path,
    *,
    requested_split: str,
    event_ids: set[str],
) -> tuple[dict[str, Any], set[str], set[str]]:
    manifest = _stream_split_manifest(path)
    split_counts: Counter[str] = Counter()
    selected_event_ids: set[str] = set()
    all_event_ids: set[str] = set()
    selected_symbols: set[str] = set()
    selected_count = 0
    requested_labels = _requested_split_labels(requested_split)
    for row in _iter_jsonl(path):
        split = _observation_split_label(row, manifest)
        split_counts[split] += 1
        event_id = str(row["event_id"])
        if event_id in event_ids:
            all_event_ids.add(event_id)
        if split in requested_labels:
            selected_count += 1
            selected_symbols.add(str(row["symbol"]))
            if event_id in event_ids:
                selected_event_ids.add(event_id)
    return (
        {
            "requested_split": requested_split,
            "selected_observation_count": selected_count,
            "selected_symbol_count": len(selected_symbols),
            "split_counts": dict(split_counts),
            "split_manifest": manifest,
        },
        selected_event_ids,
        all_event_ids,
    )


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _stream_split_manifest(path: Path) -> dict[str, Any]:
    dates: set[date] = set()
    symbols: set[str] = set()
    count = 0
    digest = hashlib.sha256()
    for row in _iter_jsonl(path):
        dates.add(date.fromisoformat(str(row["signal_date"])))
        symbols.add(str(row["symbol"]))
        count += 1
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    ordered_dates = sorted(dates)
    if not ordered_dates:
        return {}
    train_end = ordered_dates[int(len(ordered_dates) * 0.60)]
    validation_start = _shift_trading_date(ordered_dates, train_end, PURGE_TRADING_DAYS)
    validation_end = ordered_dates[int(len(ordered_dates) * 0.80)]
    locked_oos_start = _shift_trading_date(ordered_dates, validation_end, PURGE_TRADING_DAYS)
    return {
        "train_start": ordered_dates[0].isoformat(),
        "train_end": train_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
        "locked_oos_start": locked_oos_start.isoformat(),
        "locked_oos_end": ordered_dates[-1].isoformat(),
        "purge_days": PURGE_TRADING_DAYS,
        "dataset_hash": digest.hexdigest(),
        "split_observation_count": count,
        "split_symbol_count": len(symbols),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def _shift_trading_date(dates: list[date], start: date, offset: int) -> date:
    idx = dates.index(start)
    return dates[min(idx + offset, len(dates) - 1)]


def _requested_split_labels(split: str) -> set[str]:
    if split == "development":
        return {"train", "validation"}
    if split == "train":
        return {"train"}
    if split == "validation":
        return {"validation"}
    if split == "locked-oos":
        return {"locked_oos"}
    if split == "all":
        return {"train", "validation", "locked_oos"}
    raise ValueError(f"unsupported evaluation split: {split}")


def _observation_split_label(row: dict[str, Any], manifest: dict[str, Any]) -> str:
    signal_date = date.fromisoformat(str(row["signal_date"]))
    train_end = date.fromisoformat(manifest["train_end"])
    validation_start = date.fromisoformat(manifest["validation_start"])
    validation_end = date.fromisoformat(manifest["validation_end"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    raw_exit = row.get("labels", {}).get("exit_date_20d")
    exit_20d = None if raw_exit in (None, "") else date.fromisoformat(str(raw_exit))
    if signal_date <= train_end:
        if exit_20d is not None and exit_20d >= validation_start:
            return "purge_train_validation"
        return "train"
    if signal_date < validation_start:
        return "purge_train_validation"
    if signal_date <= validation_end:
        if exit_20d is not None and exit_20d >= locked_oos_start:
            return "purge_validation_locked_oos"
        return "validation"
    if signal_date < locked_oos_start:
        return "purge_validation_locked_oos"
    return "locked_oos"


if __name__ == "__main__":
    raise SystemExit(main())
