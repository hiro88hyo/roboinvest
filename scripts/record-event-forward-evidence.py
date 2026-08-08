"""Append one causal event artifact to the prospective forward-evidence ledger."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from event_forward_evidence import (
    canonical_sha256 as _canonical_sha256,
)
from event_forward_evidence import (
    read_source_ledger as read_ledger,
)
from strategy_rule.event_paper.artifact import EVENT_STRATEGY_KEY, load_event_paper_artifact

SCHEMA_VERSION = 1
FORWARD_START = date(2026, 7, 1)


def build_record(
    *, artifact_path: Path, previous_record_sha256: str | None, recorded_at: datetime
) -> dict[str, Any]:
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware")
    loaded = load_event_paper_artifact(artifact_path)
    artifact = loaded.artifact
    if artifact.signal_date < FORWARD_START:
        raise ValueError(f"forward evidence must start on or after {FORWARD_START}")
    candidate_ids = sorted(row.execution_candidate_id for row in artifact.candidates)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy_key": EVENT_STRATEGY_KEY,
        "signal_date": artifact.signal_date.isoformat(),
        "recorded_at": recorded_at.astimezone(UTC).isoformat(),
        "artifact_path": str(artifact_path),
        "artifact_sha256": loaded.sha256,
        "source_received_at": artifact.fetched_at.isoformat(),
        "candidate_count": len(artifact.candidates),
        "complete_candidate_count": sum(row.feature_data_complete for row in artifact.candidates),
        "execution_candidate_ids": candidate_ids,
        "previous_record_sha256": previous_record_sha256,
        "economic_outcome_status": (
            "pending_forward_exit" if artifact.candidates else "no_candidate_complete_artifact"
        ),
        "comparable_to_registered_backtest": False,
    }
    record["record_sha256"] = _canonical_sha256(record)
    return record


def append_record(
    *, ledger_path: Path, artifact_path: Path, recorded_at: datetime
) -> dict[str, Any]:
    rows = read_ledger(ledger_path)
    record = build_record(
        artifact_path=artifact_path,
        previous_record_sha256=rows[-1]["record_sha256"] if rows else None,
        recorded_at=recorded_at,
    )
    signal_date = date.fromisoformat(record["signal_date"])
    if rows and signal_date <= date.fromisoformat(rows[-1]["signal_date"]):
        raise ValueError("new signal_date must follow the current ledger tail")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--recorded-at", type=datetime.fromisoformat)
    args = parser.parse_args()
    recorded_at = args.recorded_at or datetime.now(UTC)
    record = append_record(
        ledger_path=args.ledger,
        artifact_path=args.artifact,
        recorded_at=recorded_at,
    )
    print(
        f"event_forward_evidence signal_date={record['signal_date']} "
        f"candidates={record['candidate_count']} hash={record['record_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
