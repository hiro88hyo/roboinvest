from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from strategy_rule.event_paper._testing import make_event_artifact_payload

SCRIPT = Path(__file__).parents[1] / "finalize-event-forward-outcomes.py"


def _load_script() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("finalize_event_forward_outcomes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "artifact.json"
    payload = make_event_artifact_payload()
    payload["signal_date"] = "2026-07-10"
    payload["fetched_at"] = "2026-07-11T00:30:00+09:00"
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


def _source_ledger(module: ModuleType, tmp_path: Path, artifact: Path) -> Path:
    artifact_sha256 = module.file_sha256(artifact)
    row = {
        "schema_version": 1,
        "strategy_key": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
        "signal_date": "2026-07-10",
        "recorded_at": "2026-07-11T16:00:00+00:00",
        "artifact_path": str(artifact),
        "artifact_sha256": artifact_sha256,
        "source_received_at": "2026-07-11T00:30:00+09:00",
        "candidate_count": 1,
        "complete_candidate_count": 1,
        "execution_candidate_ids": ["cluster-7203:obs-7203"],
        "previous_record_sha256": None,
        "economic_outcome_status": "pending_forward_exit",
        "comparable_to_registered_backtest": False,
    }
    row["record_sha256"] = module.canonical_sha256(row)
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _rewrite_source(module: ModuleType, path: Path, **changes: object) -> None:
    row = json.loads(path.read_text(encoding="utf-8"))
    row.update(changes)
    row.pop("record_sha256")
    row["record_sha256"] = module.canonical_sha256(row)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _write_ohlcv(
    module: ModuleType,
    tmp_path: Path,
    *,
    missing_date: date | None = None,
    stop_date: date | None = None,
    gap_date: date | None = None,
) -> Path:
    path = tmp_path / "ohlcv.csv"
    sessions = module.required_session_dates(date(2026, 7, 13), 20)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        writer.writeheader()
        for index, session_date in enumerate(sessions):
            if session_date == missing_date:
                continue
            open_price = Decimal("85") if session_date == gap_date else Decimal("100")
            low_price = Decimal("89") if session_date == stop_date else Decimal("95")
            writer.writerow(
                {
                    "symbol": "7203",
                    "date": session_date.isoformat(),
                    "open": str(open_price),
                    "high": "112",
                    "low": str(low_price),
                    "close": "110" if index == len(sessions) - 1 else "101",
                    "volume": "1000",
                    "turnover": "100000",
                }
            )
    return path


def _finalize(
    module: ModuleType,
    tmp_path: Path,
    *,
    ohlcv: Path,
    finalized_at: datetime,
) -> tuple[dict[str, int], Path]:
    artifact = _artifact(tmp_path)
    source = _source_ledger(module, tmp_path, artifact)
    outcomes = tmp_path / "outcomes.jsonl"
    summary = module.finalize_due_outcomes(
        source_ledger_path=source,
        outcome_ledger_path=outcomes,
        ohlcv_path=ohlcv,
        finalized_at=finalized_at,
    )
    return summary, outcomes


def test_finalizes_fixed_twentieth_session_close_and_validates_chain(tmp_path: Path) -> None:
    module = _load_script()
    ohlcv = _write_ohlcv(module, tmp_path)
    fixed_exit = module.fixed_exit_date(date(2026, 7, 13), 20)

    summary, outcomes = _finalize(
        module,
        tmp_path,
        ohlcv=ohlcv,
        finalized_at=module.daily_bar_available_at(fixed_exit),
    )

    assert summary["finalized"] == 1
    rows = module.read_outcome_ledger(outcomes)
    assert len(rows) == 1
    row = rows[0]
    assert row["exit_reason"] == "fixed_20d_close"
    assert row["official_entry_open"] == "100"
    assert row["modeled_exit_price"] == "110"
    assert row["paper_execution_observed"] is False
    assert row["comparable_to_registered_backtest"] is True
    assert row["source_record_sha256"]
    assert row["execution_evidence_eligible"] is False
    assert Decimal(row["net_return_after_cost"]) == Decimal("0.096871")


def test_finalizes_intraday_catastrophic_stop_as_soon_as_bar_is_available(
    tmp_path: Path,
) -> None:
    module = _load_script()
    stop_date = module.required_session_dates(date(2026, 7, 13), 20)[2]
    ohlcv = _write_ohlcv(module, tmp_path, stop_date=stop_date)

    summary, outcomes = _finalize(
        module,
        tmp_path,
        ohlcv=ohlcv,
        finalized_at=module.daily_bar_available_at(stop_date),
    )

    assert summary["finalized"] == 1
    row = module.read_outcome_ledger(outcomes)[0]
    assert row["actual_exit_date"] == stop_date.isoformat()
    assert row["exit_reason"] == "catastrophic_stop"
    assert Decimal(row["modeled_exit_price"]) == Decimal("90")


def test_gap_through_stop_uses_official_open(tmp_path: Path) -> None:
    module = _load_script()
    gap_date = module.required_session_dates(date(2026, 7, 13), 20)[1]
    ohlcv = _write_ohlcv(module, tmp_path, gap_date=gap_date)

    _, outcomes = _finalize(
        module,
        tmp_path,
        ohlcv=ohlcv,
        finalized_at=module.daily_bar_available_at(gap_date),
    )

    row = module.read_outcome_ledger(outcomes)[0]
    assert row["exit_reason"] == "gap_through_catastrophic_stop"
    assert row["modeled_exit_price"] == "85"


def test_immature_candidate_remains_pending_without_creating_ledger(tmp_path: Path) -> None:
    module = _load_script()
    ohlcv = _write_ohlcv(module, tmp_path)
    entry_date = date(2026, 7, 13)

    summary, outcomes = _finalize(
        module,
        tmp_path,
        ohlcv=ohlcv,
        finalized_at=module.daily_bar_available_at(entry_date) + timedelta(days=1),
    )

    assert summary["finalized"] == 0
    assert summary["pending"] == 1
    assert not outcomes.exists()


def test_missing_completed_session_fails_closed(tmp_path: Path) -> None:
    module = _load_script()
    missing_date = module.required_session_dates(date(2026, 7, 13), 20)[1]
    ohlcv = _write_ohlcv(module, tmp_path, missing_date=missing_date)

    with pytest.raises(ValueError, match="missing completed outcome OHLCV"):
        _finalize(
            module,
            tmp_path,
            ohlcv=ohlcv,
            finalized_at=module.daily_bar_available_at(missing_date),
        )


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    module = _load_script()
    ohlcv = _write_ohlcv(module, tmp_path)
    artifact = _artifact(tmp_path)
    source = _source_ledger(module, tmp_path, artifact)
    outcomes = tmp_path / "outcomes.jsonl"
    as_of = module.daily_bar_available_at(module.fixed_exit_date(date(2026, 7, 13), 20))

    first = module.finalize_due_outcomes(
        source_ledger_path=source,
        outcome_ledger_path=outcomes,
        ohlcv_path=ohlcv,
        finalized_at=as_of,
    )
    second = module.finalize_due_outcomes(
        source_ledger_path=source,
        outcome_ledger_path=outcomes,
        ohlcv_path=ohlcv,
        finalized_at=as_of,
    )

    assert first["finalized"] == 1
    assert second["finalized"] == 0
    assert second["existing_outcomes"] == 1
    assert len(module.read_outcome_ledger(outcomes)) == 1


def test_source_binding_mismatch_fails_closed(tmp_path: Path) -> None:
    module = _load_script()
    artifact = _artifact(tmp_path)
    source = _source_ledger(module, tmp_path, artifact)
    _rewrite_source(module, source, candidate_count=2)

    with pytest.raises(ValueError, match="source candidate count mismatch"):
        module.finalize_due_outcomes(
            source_ledger_path=source,
            outcome_ledger_path=tmp_path / "outcomes.jsonl",
            ohlcv_path=_write_ohlcv(module, tmp_path),
            finalized_at=module.daily_bar_available_at(date(2026, 8, 31)),
        )


def test_zero_candidate_source_does_not_require_ohlcv(tmp_path: Path) -> None:
    module = _load_script()
    artifact = _artifact(tmp_path)
    source = _source_ledger(module, tmp_path, artifact)
    _rewrite_source(
        module,
        source,
        candidate_count=0,
        complete_candidate_count=0,
        execution_candidate_ids=[],
        economic_outcome_status="no_candidate_complete_artifact",
    )

    summary = module.finalize_due_outcomes(
        source_ledger_path=source,
        outcome_ledger_path=tmp_path / "outcomes.jsonl",
        ohlcv_path=tmp_path / "does-not-exist.csv",
        finalized_at=module.daily_bar_available_at(date(2026, 8, 31)),
    )

    assert summary == {
        "source_rows": 1,
        "existing_outcomes": 0,
        "finalized": 0,
        "pending": 0,
        "blocked_incomplete": 0,
    }
