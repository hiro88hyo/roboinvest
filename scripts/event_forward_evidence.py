"""Shared integrity helpers for prospective event forward-evidence ledgers."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


def canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"ledger line {line_number} is not an object")
        rows.append(payload)
    validate_source_chain(rows)
    return rows


def validate_source_chain(rows: list[dict[str, Any]]) -> None:
    previous_sha256: str | None = None
    previous_date: date | None = None
    seen_dates: set[date] = set()
    for index, row in enumerate(rows, 1):
        record_sha256 = row.get("record_sha256")
        if not isinstance(record_sha256, str):
            raise ValueError(f"ledger row {index} has no record_sha256")
        unhashed = {key: value for key, value in row.items() if key != "record_sha256"}
        if canonical_sha256(unhashed) != record_sha256:
            raise ValueError(f"ledger row {index} hash mismatch")
        if row.get("previous_record_sha256") != previous_sha256:
            raise ValueError(f"ledger row {index} chain mismatch")
        signal_date = date.fromisoformat(str(row.get("signal_date")))
        if signal_date in seen_dates:
            raise ValueError(f"duplicate signal_date in ledger: {signal_date}")
        if previous_date is not None and signal_date <= previous_date:
            raise ValueError("ledger signal dates must be strictly increasing")
        seen_dates.add(signal_date)
        previous_date = signal_date
        previous_sha256 = record_sha256


def read_outcome_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"outcome line {line_number} is not an object")
        rows.append(payload)
    validate_outcome_chain(rows)
    return rows


def validate_outcome_chain(rows: list[dict[str, Any]]) -> None:
    previous_sha256: str | None = None
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, 1):
        outcome_sha256 = row.get("outcome_sha256")
        if not isinstance(outcome_sha256, str):
            raise ValueError(f"outcome row {index} has no outcome_sha256")
        unhashed = {key: value for key, value in row.items() if key != "outcome_sha256"}
        if canonical_sha256(unhashed) != outcome_sha256:
            raise ValueError(f"outcome row {index} hash mismatch")
        if row.get("previous_outcome_sha256") != previous_sha256:
            raise ValueError(f"outcome row {index} chain mismatch")
        key = (str(row.get("source_record_sha256")), str(row.get("execution_candidate_id")))
        if key in seen:
            raise ValueError(f"duplicate finalized outcome: {key[0]}:{key[1]}")
        seen.add(key)
        previous_sha256 = outcome_sha256
