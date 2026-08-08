#!/usr/bin/env python3
"""Report fail-closed 2026-09-30 readiness from prospective event evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from event_forward_evidence import read_outcome_ledger, read_source_ledger
from event_research_common import ROUND_TRIP_COST_RATE, daily_bar_available_at
from strategy_rule.event_paper.artifact import EVENT_STRATEGY_KEY, load_event_paper_artifact
from universe_scanner.calendar import is_tse_business_day, next_business_day

EVALUATION_START = date(2026, 7, 21)
DEADLINE = date(2026, 9, 30)
CAPITAL = Decimal("2000000")
PROFIT_FACTOR_GATE = Decimal("1.2")
MAX_DRAWDOWN_GATE = CAPITAL * Decimal("0.10")
MAX_HOLD_DAYS = 20
TOKYO = ZoneInfo("Asia/Tokyo")
DEFAULT_SOURCE_LEDGER = Path("out/event-forward-evidence/ledger.jsonl")
DEFAULT_OUTCOME_LEDGER = Path("out/event-forward-evidence/outcomes.jsonl")


def _portfolio_module() -> ModuleType:
    name = "simulate_event_portfolio_kill_switch_report"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name("simulate-event-portfolio.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load portfolio simulator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fixed_exit_date(entry_date: date, max_hold_days: int = MAX_HOLD_DAYS) -> date:
    current = entry_date
    for _ in range(max_hold_days):
        current = next_business_day(current)
    return current


def last_evaluable_signal_date() -> date:
    current = EVALUATION_START
    last: date | None = None
    while current <= DEADLINE:
        if is_tse_business_day(current):
            entry_date = next_business_day(current)
            if fixed_exit_date(entry_date) <= DEADLINE:
                last = current
        current += timedelta(days=1)
    if last is None:
        raise RuntimeError("evaluation window contains no eligible signal dates")
    return last


def expected_signal_dates(as_of: datetime) -> list[date]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    completed_calendar_date = as_of.astimezone(TOKYO).date() - timedelta(days=1)
    end = min(completed_calendar_date, last_evaluable_signal_date())
    current = EVALUATION_START
    rows: list[date] = []
    while current <= end:
        if is_tse_business_day(current):
            rows.append(current)
        current += timedelta(days=1)
    return rows


def deadline_at() -> datetime:
    return datetime.combine(DEADLINE, time(15, 30), tzinfo=TOKYO).astimezone(UTC)


def _validate_outcome_sources(
    source_by_hash: dict[str, dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> None:
    for outcome in outcomes:
        source = source_by_hash.get(str(outcome.get("source_record_sha256")))
        if source is None:
            raise ValueError(
                f"outcome references missing source: {outcome.get('source_record_sha256')}"
            )
        bindings = (
            ("source_signal_date", "signal_date"),
            ("source_artifact_sha256", "artifact_sha256"),
            ("strategy_key", "strategy_key"),
        )
        for outcome_key, source_key in bindings:
            if outcome.get(outcome_key) != source.get(source_key):
                raise ValueError(f"outcome/source {outcome_key} mismatch")
        if outcome.get("execution_candidate_id") not in source.get("execution_candidate_ids", []):
            raise ValueError("outcome candidate is absent from its source")


def build_report(
    *,
    source_ledger_path: Path,
    outcome_ledger_path: Path,
    as_of: datetime,
) -> dict[str, Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    source_rows = read_source_ledger(source_ledger_path)
    outcomes = read_outcome_ledger(outcome_ledger_path)
    source_by_hash = {str(row["record_sha256"]): row for row in source_rows}
    _validate_outcome_sources(source_by_hash, outcomes)
    for outcome in outcomes:
        finalized_at = datetime.fromisoformat(str(outcome["finalized_at"]))
        if finalized_at.tzinfo is None:
            raise ValueError("outcome finalized_at must be timezone-aware")
        if finalized_at.astimezone(UTC) > as_of:
            raise ValueError(f"outcome was finalized after as_of: {outcome['outcome_sha256']}")
        actual_exit_date = date.fromisoformat(str(outcome["actual_exit_date"]))
        if finalized_at.astimezone(UTC) < daily_bar_available_at(actual_exit_date):
            raise ValueError(f"outcome predates official exit bar: {outcome['outcome_sha256']}")
        if (
            outcome.get("evidence_class") != "registered_backtest_shadow"
            or outcome.get("paper_execution_observed") is not False
            or outcome.get("execution_evidence_eligible") is not False
        ):
            raise ValueError(f"outcome evidence class drifted: {outcome['outcome_sha256']}")

    expected_dates = expected_signal_dates(as_of)
    expected_set = set(expected_dates)
    evaluation_sources = {
        date.fromisoformat(str(row["signal_date"])): row
        for row in source_rows
        if date.fromisoformat(str(row["signal_date"])) in expected_set
    }
    missing_dates = sorted(expected_set - set(evaluation_sources))
    outcome_by_key = {
        (str(row["source_record_sha256"]), str(row["execution_candidate_id"])): row
        for row in outcomes
    }

    complete_candidates: list[tuple[dict[str, Any], Any]] = []
    incomplete_candidate_count = 0
    for signal_date in sorted(evaluation_sources):
        source = evaluation_sources[signal_date]
        if source.get("strategy_key") != EVENT_STRATEGY_KEY:
            raise ValueError(f"source strategy mismatch: {source['record_sha256']}")
        loaded = load_event_paper_artifact(Path(str(source["artifact_path"])))
        if loaded.sha256 != source.get("artifact_sha256"):
            raise ValueError(f"source artifact hash mismatch: {source['record_sha256']}")
        artifact = loaded.artifact
        if artifact.signal_date != signal_date:
            raise ValueError(f"source signal date mismatch: {source['record_sha256']}")
        if source.get("source_received_at") != artifact.fetched_at.isoformat():
            raise ValueError(f"source receipt mismatch: {source['record_sha256']}")
        if source.get("candidate_count") != len(artifact.candidates):
            raise ValueError(f"source candidate count mismatch: {source['record_sha256']}")
        if source.get("complete_candidate_count") != sum(
            candidate.feature_data_complete for candidate in artifact.candidates
        ):
            raise ValueError(f"source complete candidate mismatch: {source['record_sha256']}")
        artifact_ids = sorted(row.execution_candidate_id for row in artifact.candidates)
        if artifact_ids != list(source.get("execution_candidate_ids", [])):
            raise ValueError(f"source candidate IDs mismatch: {source['record_sha256']}")
        for candidate in artifact.candidates:
            if not candidate.feature_data_complete:
                incomplete_candidate_count += 1
                continue
            complete_candidates.append((source, candidate))

    missing_outcome_keys = [
        (str(source["record_sha256"]), candidate.execution_candidate_id)
        for source, candidate in complete_candidates
        if (str(source["record_sha256"]), candidate.execution_candidate_id) not in outcome_by_key
    ]
    for source, candidate in complete_candidates:
        key = (str(source["record_sha256"]), candidate.execution_candidate_id)
        outcome = outcome_by_key.get(key)
        if outcome is None:
            continue
        expected_fixed_exit = fixed_exit_date(candidate.entry_date, candidate.max_hold_days)
        actual_exit = date.fromisoformat(str(outcome["actual_exit_date"]))
        if outcome.get("symbol") != candidate.symbol:
            raise ValueError(f"outcome symbol mismatch: {outcome['outcome_sha256']}")
        if outcome.get("entry_date") != candidate.entry_date.isoformat():
            raise ValueError(f"outcome entry date mismatch: {outcome['outcome_sha256']}")
        if outcome.get("fixed_exit_date") != expected_fixed_exit.isoformat():
            raise ValueError(f"outcome fixed exit mismatch: {outcome['outcome_sha256']}")
        if not candidate.entry_date <= actual_exit <= expected_fixed_exit:
            raise ValueError(f"outcome actual exit is outside hold: {outcome['outcome_sha256']}")
        if Decimal(str(outcome.get("round_trip_cost_rate"))) != ROUND_TRIP_COST_RATE:
            raise ValueError(f"outcome cost drifted: {outcome['outcome_sha256']}")
        if (
            Decimal(str(outcome["official_entry_open"])) <= 0
            or Decimal(str(outcome["modeled_exit_price"])) <= 0
        ):
            raise ValueError(f"outcome contains non-positive price: {outcome['outcome_sha256']}")

    portfolio_summary: dict[str, Any] | None = None
    economic_condition = "NOT_DEMONSTRATED"
    if complete_candidates and not missing_dates and not missing_outcome_keys:
        portfolio = _portfolio_module()
        candidates = []
        for source, candidate in complete_candidates:
            outcome = outcome_by_key[
                (str(source["record_sha256"]), candidate.execution_candidate_id)
            ]
            candidates.append(
                portfolio.PortfolioCandidate(
                    observation_id=candidate.observation_id,
                    event_id=candidate.event_id,
                    symbol=candidate.symbol,
                    signal_date=candidate.signal_date,
                    entry_date=candidate.entry_date,
                    exit_date=date.fromisoformat(str(outcome["actual_exit_date"])),
                    entry_price=Decimal(str(outcome["official_entry_open"])),
                    exit_price=Decimal(str(outcome["modeled_exit_price"])),
                    sort_key=candidate.feature_cutoff_at.isoformat(),
                )
            )
        result = portfolio.simulate_portfolio(
            candidates,
            params=portfolio.PortfolioParams(capital=CAPITAL),
            selection_order="feature_time_symbol",
            spec=portfolio.CandidateSpec(
                candidate_id=EVENT_STRATEGY_KEY,
                exit_horizon=MAX_HOLD_DAYS,
                catastrophic_stop=True,
            ),
        )
        profit_factor_pass = result.opened_trade_count > 0 and (
            (result.profit_factor is not None and result.profit_factor > 1.2)
            or (result.profit_factor is None and result.total_pnl > 0)
        )
        drawdown_pass = Decimal(str(result.max_drawdown)) < MAX_DRAWDOWN_GATE
        economic_condition = "PASS" if profit_factor_pass and drawdown_pass else "FAIL"
        portfolio_summary = {
            "candidate_count": result.candidate_count,
            "opened_trade_count": result.opened_trade_count,
            "net_pnl": result.total_pnl,
            "profit_factor": (
                "Infinity"
                if result.profit_factor is None
                and result.opened_trade_count > 0
                and result.total_pnl > 0
                else result.profit_factor
            ),
            "max_drawdown": result.max_drawdown,
            "max_drawdown_ratio": float(Decimal(str(result.max_drawdown)) / CAPITAL),
            "profit_factor_gate_passed": profit_factor_pass,
            "drawdown_gate_passed": drawdown_pass,
        }

    coverage_complete = not missing_dates
    outcomes_complete = not missing_outcome_keys and incomplete_candidate_count == 0
    deadline_reached = as_of >= deadline_at()
    if not deadline_reached:
        project_status = "PENDING_UNTIL_DEADLINE"
    elif coverage_complete and outcomes_complete and economic_condition == "PASS":
        project_status = "ECONOMIC_CONDITION_MET_NOT_ACTIVATION"
    else:
        project_status = "KILL_SWITCH_TRIGGERED"

    return {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "last_evaluable_signal_date": last_evaluable_signal_date().isoformat(),
        "deadline": DEADLINE.isoformat(),
        "deadline_at": deadline_at().isoformat(),
        "deadline_reached": deadline_reached,
        "capital": str(CAPITAL),
        "profit_factor_gate": str(PROFIT_FACTOR_GATE),
        "max_drawdown_gate": str(MAX_DRAWDOWN_GATE),
        "expected_signal_date_count_to_as_of": len(expected_dates),
        "recorded_signal_date_count": len(evaluation_sources),
        "missing_signal_dates": [value.isoformat() for value in missing_dates],
        "source_coverage_complete": coverage_complete,
        "complete_candidate_count": len(complete_candidates),
        "incomplete_candidate_count": incomplete_candidate_count,
        "finalized_candidate_count": len(complete_candidates) - len(missing_outcome_keys),
        "missing_outcome_count": len(missing_outcome_keys),
        "outcomes_complete": outcomes_complete,
        "portfolio": portfolio_summary,
        "economic_condition": economic_condition,
        "project_status": project_status,
        "paper_execution_observed": False,
        "activation_authorized": False,
        "sample_adequacy_note": (
            "The project contract fixes PF and drawdown but no numeric minimum; "
            "trade count and period coverage must still be reported and must not be hidden."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_SOURCE_LEDGER)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOME_LEDGER)
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    report = build_report(
        source_ledger_path=args.ledger,
        outcome_ledger_path=args.outcomes,
        as_of=args.as_of or datetime.now(UTC),
    )
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
