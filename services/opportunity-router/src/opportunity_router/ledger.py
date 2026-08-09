"""Local append-only, hash-chained JSONL ledger for router decisions."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

from opportunity_router.integrity import canonical_json, canonical_sha256
from opportunity_router.models import RouterDecision


class LedgerIntegrityError(ValueError):
    """Raised when an existing ledger is malformed or fails its hash chain."""


class LedgerConflictError(ValueError):
    """Raised when an existing decision ID has different immutable content."""


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    sequence: int
    previous_record_sha256: str | None
    decision: dict[str, object]
    record_sha256: str

    def unhashed_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "previous_record_sha256": self.previous_record_sha256,
            "decision": self.decision,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.unhashed_dict(), "record_sha256": self.record_sha256}


def _parse_record(payload: object, line_number: int) -> LedgerRecord:
    if not isinstance(payload, dict):
        raise LedgerIntegrityError(f"ledger line {line_number} is not an object")
    sequence = payload.get("sequence")
    previous = payload.get("previous_record_sha256")
    decision = payload.get("decision")
    record_sha256 = payload.get("record_sha256")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise LedgerIntegrityError(f"ledger line {line_number} has invalid sequence")
    if previous is not None and not isinstance(previous, str):
        raise LedgerIntegrityError(f"ledger line {line_number} has invalid previous hash")
    if not isinstance(decision, dict):
        raise LedgerIntegrityError(f"ledger line {line_number} has invalid decision")
    if not isinstance(record_sha256, str):
        raise LedgerIntegrityError(f"ledger line {line_number} has invalid record hash")
    return LedgerRecord(
        sequence=sequence,
        previous_record_sha256=previous,
        decision=cast(dict[str, object], decision),
        record_sha256=record_sha256,
    )


def _read_records(handle: IO[str]) -> list[LedgerRecord]:
    handle.seek(0)
    records: list[LedgerRecord] = []
    previous_sha256: str | None = None
    seen_decision_ids: set[str] = set()
    for line_number, line in enumerate(handle, 1):
        if not line.strip():
            raise LedgerIntegrityError(f"blank ledger line at {line_number}")
        try:
            payload: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerIntegrityError(f"invalid JSON at ledger line {line_number}") from exc
        record = _parse_record(payload, line_number)
        if record.sequence != line_number:
            raise LedgerIntegrityError(f"ledger sequence mismatch at line {line_number}")
        if record.previous_record_sha256 != previous_sha256:
            raise LedgerIntegrityError(f"ledger chain mismatch at line {line_number}")
        if canonical_sha256(record.unhashed_dict()) != record.record_sha256:
            raise LedgerIntegrityError(f"ledger hash mismatch at line {line_number}")
        decision_id = record.decision.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            raise LedgerIntegrityError(f"ledger line {line_number} has invalid decision_id")
        if decision_id in seen_decision_ids:
            raise LedgerIntegrityError(f"duplicate decision_id at ledger line {line_number}")
        seen_decision_ids.add(decision_id)
        records.append(record)
        previous_sha256 = record.record_sha256
    return records


@dataclass(frozen=True, slots=True)
class DecisionLedger:
    """Append immutable decisions with idempotency and a SHA-256 chain."""

    path: Path

    def read(self) -> tuple[LedgerRecord, ...]:
        if not self.path.exists():
            return ()
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                return tuple(_read_records(handle))
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(self, decision: RouterDecision) -> LedgerRecord:
        return self.append_all((decision,))[0]

    def append_all(self, decisions: Iterable[RouterDecision]) -> tuple[LedgerRecord, ...]:
        pending = tuple(decisions)
        if not pending:
            return ()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                records = _read_records(handle)
                by_decision_id = {
                    str(record.decision.get("decision_id")): record for record in records
                }
                results: list[LedgerRecord] = []
                new_records: list[LedgerRecord] = []
                previous_sha256 = records[-1].record_sha256 if records else None
                next_sequence = len(records) + 1
                for decision in pending:
                    payload = decision.to_dict()
                    existing = by_decision_id.get(decision.decision_id)
                    if existing is not None:
                        if existing.decision != payload:
                            raise LedgerConflictError(
                                f"decision_id {decision.decision_id} has conflicting content"
                            )
                        results.append(existing)
                        continue
                    unhashed = {
                        "sequence": next_sequence,
                        "previous_record_sha256": previous_sha256,
                        "decision": payload,
                    }
                    record = LedgerRecord(
                        sequence=next_sequence,
                        previous_record_sha256=previous_sha256,
                        decision=payload,
                        record_sha256=canonical_sha256(unhashed),
                    )
                    by_decision_id[decision.decision_id] = record
                    new_records.append(record)
                    results.append(record)
                    previous_sha256 = record.record_sha256
                    next_sequence += 1

                if new_records:
                    handle.seek(0, os.SEEK_END)
                    for record in new_records:
                        handle.write(canonical_json(record.to_dict()))
                        handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return tuple(results)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
