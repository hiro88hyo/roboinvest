#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from event_research_common import EVALUATION_SPLITS, FEATURE_SCHEMA_VERSION
from strategy_ai.event.evaluator import feature_bundle_proxy_label
from trade_contracts.event_research import EventAiLabeledRecord, ObservationRecord

MODEL_ID = "feature_bundle_proxy_v0"
PROMPT_VERSION = "feature_bundle_proxy_v0"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic EventAiLabel proxy labels from feature bundles only."
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--event-ids-from-labels",
        type=Path,
        help="Optional labels.jsonl used only as an event_id allowlist.",
    )
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Observation split to emit. Default excludes locked OOS labels.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")

    event_id_allowlist = (
        _load_event_id_allowlist(args.event_ids_from_labels)
        if args.event_ids_from_labels is not None
        else None
    )
    split_counts: dict[str, int] = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emitted_count = 0
    with args.output.open("w", encoding="utf-8") as f:
        for row in _iter_jsonl(args.observations):
            split_label = str(row.get("split_label") or "unspecified")
            split_counts[split_label] = split_counts.get(split_label, 0) + 1
            if not _split_allows(split_label, args.split):
                continue
            if (
                event_id_allowlist is not None
                and str(row.get("event_id")) not in event_id_allowlist
            ):
                continue
            obs = ObservationRecord.model_validate(row)
            label = feature_bundle_proxy_label(obs)
            raw_response = label.model_dump_json()
            prompt_hash = _feature_bundle_hash(row)
            record = EventAiLabeledRecord(
                job_id=f"feature-proxy-{obs.event_id}",
                event_id=obs.event_id,
                prompt_version=PROMPT_VERSION,
                prompt_hash=prompt_hash,
                cache_key=f"{MODEL_ID}:{prompt_hash}",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                feature_cutoff_at=obs.feature_cutoff_at,
                dataset_hash=obs.dataset_hash,
                split_manifest_hash=obs.split_manifest_hash,
                split_label=obs.split_label,
                model_provider="deterministic_feature_proxy",
                model_id=MODEL_ID,
                temperature=Decimal("0"),
                seed=None,
                raw_response=raw_response,
                label=label,
                created_at=datetime.now(tz=UTC),
            )
            f.write(record.model_dump_json() + "\n")
            emitted_count += 1
    print(
        "event_ai_feature_proxy_labels "
        f"split={args.split} labels={emitted_count} "
        f"event_id_allowlist={None if event_id_allowlist is None else len(event_id_allowlist)} "
        f"split_counts={json.dumps(split_counts, sort_keys=True)} "
        f"output={args.output}"
    )
    return 0


def _split_allows(label: str, split: str) -> bool:
    if label == "unspecified":
        return True
    if split == "development":
        return label in {"train", "validation"}
    if split == "all":
        return label in {"train", "validation", "locked_oos"}
    if split == "locked-oos":
        return label == "locked_oos"
    return label == split


def _feature_bundle_hash(row: dict[str, Any]) -> str:
    payload = {
        "event_id": row.get("event_id"),
        "event_type": row.get("event_type"),
        "event_subtype": row.get("event_subtype"),
        "fundamental_features_v0": row.get("fundamental_features_v0"),
        "valuation_features_v0": row.get("valuation_features_v0"),
        "technical_context_v0": row.get("technical_context_v0"),
        "feature_cutoff_at": row.get("feature_cutoff_at"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line:
                yield json.loads(line)


def _load_event_id_allowlist(path: Path) -> set[str]:
    return {str(row["event_id"]) for row in _iter_jsonl(path)}


if __name__ == "__main__":
    raise SystemExit(main())
