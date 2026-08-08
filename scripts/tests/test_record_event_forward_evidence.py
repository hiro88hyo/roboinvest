from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from strategy_rule.event_paper._testing import make_event_artifact_payload

SCRIPT = Path(__file__).parents[1] / "record-event-forward-evidence.py"


def _load_script() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("record_event_forward_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(tmp_path: Path, *, no_candidates: bool = False) -> Path:
    path = tmp_path / "artifact.json"
    payload = make_event_artifact_payload()
    if no_candidates:
        payload["candidates"] = []
        payload["summary"]["candidate_count"] = 0
    payload["signal_date"] = "2026-07-10"
    payload["fetched_at"] = "2026-07-11T22:00:00+09:00"
    for candidate in payload["candidates"]:
        candidate["signal_date"] = "2026-07-10"
        candidate["entry_date"] = "2026-07-13"
        candidate["feature_cutoff_at"] = "2026-07-10T06:30:00+00:00"
        candidate["data_available_at"] = "2026-07-10T06:30:00+00:00"
        candidate["source_received_at"] = payload["fetched_at"]
        candidate["required_ohlcv_session_date"] = "2026-07-10"
        candidate["valuation_reference_bar_date"] = "2026-07-10"
        candidate["valuation_reference_available_at"] = "2026-07-10T06:30:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_append_builds_and_validates_hash_chain(tmp_path: Path) -> None:
    module = _load_script()
    ledger = tmp_path / "ledger.jsonl"
    record = module.append_record(
        ledger_path=ledger,
        artifact_path=_artifact(tmp_path),
        recorded_at=datetime(2026, 7, 12, tzinfo=UTC),
    )

    rows = module.read_ledger(ledger)
    assert rows == [record]
    assert record["signal_date"] == "2026-07-10"
    assert record["economic_outcome_status"] == "pending_forward_exit"
    assert record["comparable_to_registered_backtest"] is False


def test_ledger_rejects_tampering(tmp_path: Path) -> None:
    module = _load_script()
    ledger = tmp_path / "ledger.jsonl"
    module.append_record(
        ledger_path=ledger,
        artifact_path=_artifact(tmp_path),
        recorded_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    row = json.loads(ledger.read_text(encoding="utf-8"))
    row["candidate_count"] = 999
    ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        module.read_ledger(ledger)


def test_ledger_rejects_duplicate_signal_date(tmp_path: Path) -> None:
    module = _load_script()
    ledger = tmp_path / "ledger.jsonl"
    artifact = _artifact(tmp_path)
    module.append_record(
        ledger_path=ledger,
        artifact_path=artifact,
        recorded_at=datetime(2026, 7, 12, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="must follow"):
        module.append_record(
            ledger_path=ledger,
            artifact_path=artifact,
            recorded_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_complete_zero_candidate_artifact_is_not_marked_as_pending_exit(
    tmp_path: Path,
) -> None:
    module = _load_script()
    record = module.append_record(
        ledger_path=tmp_path / "ledger.jsonl",
        artifact_path=_artifact(tmp_path, no_candidates=True),
        recorded_at=datetime(2026, 7, 12, tzinfo=UTC),
    )

    assert record["candidate_count"] == 0
    assert record["economic_outcome_status"] == "no_candidate_complete_artifact"
