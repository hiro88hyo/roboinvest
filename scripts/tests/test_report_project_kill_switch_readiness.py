from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

from strategy_rule.event_paper._testing import make_event_artifact_payload

SCRIPT = Path(__file__).parents[1] / "report-project-kill-switch-readiness.py"


def _load_script() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location(
        "report_project_kill_switch_readiness",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifact(tmp_path: Path, *, no_candidates: bool) -> Path:
    path = tmp_path / "artifact.json"
    payload = make_event_artifact_payload()
    payload["signal_date"] = "2026-07-21"
    payload["fetched_at"] = "2026-07-22T00:30:00+09:00"
    if no_candidates:
        payload["candidates"] = []
        payload["summary"]["candidate_count"] = 0
    for candidate in payload["candidates"]:
        candidate["signal_date"] = "2026-07-21"
        candidate["entry_date"] = "2026-07-22"
        candidate["feature_cutoff_at"] = "2026-07-21T06:30:00+00:00"
        candidate["data_available_at"] = "2026-07-21T06:30:00+00:00"
        candidate["source_received_at"] = payload["fetched_at"]
        candidate["required_ohlcv_session_date"] = "2026-07-21"
        candidate["valuation_reference_bar_date"] = "2026-07-21"
        candidate["valuation_reference_available_at"] = "2026-07-21T06:30:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _source(tmp_path: Path, *, no_candidates: bool) -> tuple[Path, dict]:
    from event_forward_evidence import canonical_sha256, file_sha256

    artifact = _artifact(tmp_path, no_candidates=no_candidates)
    candidate_ids = [] if no_candidates else ["cluster-7203:obs-7203"]
    row = {
        "schema_version": 1,
        "strategy_key": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
        "signal_date": "2026-07-21",
        "recorded_at": "2026-07-21T16:00:00+00:00",
        "artifact_path": str(artifact),
        "artifact_sha256": file_sha256(artifact),
        "source_received_at": "2026-07-22T00:30:00+09:00",
        "candidate_count": len(candidate_ids),
        "complete_candidate_count": len(candidate_ids),
        "execution_candidate_ids": candidate_ids,
        "previous_record_sha256": None,
        "economic_outcome_status": (
            "no_candidate_complete_artifact" if no_candidates else "pending_forward_exit"
        ),
        "comparable_to_registered_backtest": False,
    }
    row["record_sha256"] = canonical_sha256(row)
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path, row


def _stopped_outcome(tmp_path: Path, source: dict) -> Path:
    from event_forward_evidence import canonical_sha256

    row = {
        "schema_version": 1,
        "source_record_sha256": source["record_sha256"],
        "source_signal_date": source["signal_date"],
        "source_artifact_sha256": source["artifact_sha256"],
        "strategy_key": source["strategy_key"],
        "execution_candidate_id": "cluster-7203:obs-7203",
        "symbol": "7203",
        "entry_date": "2026-07-22",
        "fixed_exit_date": "2026-08-20",
        "actual_exit_date": "2026-07-22",
        "exit_reason": "catastrophic_stop",
        "official_entry_open": "100",
        "stop_price": "90",
        "modeled_exit_price": "90",
        "official_exit_bar_open": "100",
        "official_exit_bar_high": "101",
        "official_exit_bar_low": "89",
        "official_exit_bar_close": "91",
        "gross_return": "-0.1",
        "round_trip_cost_rate": "0.00298",
        "net_return_after_cost": "-0.102831",
        "ohlcv_path": "fixture.csv",
        "ohlcv_sha256": "fixture",
        "finalized_at": "2026-07-22T06:30:00+00:00",
        "outcome_status": "finalized_registered_backtest_shadow",
        "evidence_class": "registered_backtest_shadow",
        "official_entry_reconciled": True,
        "official_exit_reconciled": True,
        "paper_execution_observed": False,
        "execution_evidence_eligible": False,
        "comparable_to_registered_backtest": True,
        "previous_outcome_sha256": None,
    }
    row["outcome_sha256"] = canonical_sha256(row)
    path = tmp_path / "outcomes.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def test_evaluation_window_has_27_signal_dates() -> None:
    module = _load_script()

    assert module.last_evaluable_signal_date() == date(2026, 8, 27)
    assert len(module.expected_signal_dates(module.deadline_at())) == 27


def test_next_day_boundary_starts_expected_coverage() -> None:
    module = _load_script()

    before = datetime(2026, 7, 21, 14, 59, 59, tzinfo=UTC)
    at_start = datetime(2026, 7, 21, 15, 0, 0, tzinfo=UTC)

    assert module.expected_signal_dates(before) == []
    assert module.expected_signal_dates(at_start) == [date(2026, 7, 21)]


def test_zero_candidate_day_is_complete_but_not_economic_evidence(tmp_path: Path) -> None:
    module = _load_script()
    ledger, _ = _source(tmp_path, no_candidates=True)

    report = module.build_report(
        source_ledger_path=ledger,
        outcome_ledger_path=tmp_path / "outcomes.jsonl",
        as_of=datetime(2026, 7, 21, 16, 0, tzinfo=UTC),
    )

    assert report["source_coverage_complete"] is True
    assert report["complete_candidate_count"] == 0
    assert report["economic_condition"] == "NOT_DEMONSTRATED"
    assert report["project_status"] == "PENDING_UNTIL_DEADLINE"


def test_missing_deadline_coverage_triggers_kill_switch(tmp_path: Path) -> None:
    module = _load_script()

    report = module.build_report(
        source_ledger_path=tmp_path / "missing-ledger.jsonl",
        outcome_ledger_path=tmp_path / "missing-outcomes.jsonl",
        as_of=module.deadline_at(),
    )

    assert report["expected_signal_date_count_to_as_of"] == 27
    assert report["recorded_signal_date_count"] == 0
    assert report["project_status"] == "KILL_SWITCH_TRIGGERED"


def test_complete_stopped_candidate_replays_frozen_portfolio(tmp_path: Path) -> None:
    module = _load_script()
    ledger, source = _source(tmp_path, no_candidates=False)
    outcomes = _stopped_outcome(tmp_path, source)

    report = module.build_report(
        source_ledger_path=ledger,
        outcome_ledger_path=outcomes,
        as_of=datetime(2026, 7, 22, 6, 30, tzinfo=UTC),
    )

    assert report["source_coverage_complete"] is True
    assert report["outcomes_complete"] is True
    assert report["portfolio"]["opened_trade_count"] == 1
    assert report["portfolio"]["profit_factor"] == 0.0
    assert report["economic_condition"] == "FAIL"
    assert report["project_status"] == "PENDING_UNTIL_DEADLINE"
