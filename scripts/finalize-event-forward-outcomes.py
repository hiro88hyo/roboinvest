#!/usr/bin/env python3
"""Append due registered-backtest shadow outcomes to a separate hash-chain ledger."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from event_forward_evidence import (
    canonical_sha256,
    file_sha256,
    read_outcome_ledger,
    read_source_ledger,
)
from event_research_common import (
    ROUND_TRIP_COST_RATE,
    OhlcvRow,
    daily_bar_available_at,
    read_ohlcv_csv,
)
from strategy_rule.event_paper.artifact import (
    EVENT_STRATEGY_KEY,
    EventPaperCandidate,
    load_event_paper_artifact,
)
from universe_scanner.calendar import next_business_day

OUTCOME_SCHEMA_VERSION = 1
DEFAULT_SOURCE_LEDGER = Path("out/event-forward-evidence/ledger.jsonl")
DEFAULT_OUTCOME_LEDGER = Path("out/event-forward-evidence/outcomes.jsonl")
DEFAULT_OHLCV = Path("data/reference/daily_ohlcv_20210625_20260624_bydate.csv")
STOP_LOSS_PCT = Decimal("0.10")
COST_PER_SIDE_RATE = ROUND_TRIP_COST_RATE / Decimal("2")


def fixed_exit_date(entry_date: date, max_hold_days: int) -> date:
    current = entry_date
    for _ in range(max_hold_days):
        current = next_business_day(current)
    return current


def required_session_dates(entry_date: date, max_hold_days: int) -> list[date]:
    dates = [entry_date]
    while len(dates) <= max_hold_days:
        dates.append(next_business_day(dates[-1]))
    return dates


def _bars_by_symbol_date(rows: list[OhlcvRow]) -> dict[str, dict[date, OhlcvRow]]:
    by_symbol: dict[str, dict[date, OhlcvRow]] = defaultdict(dict)
    for row in rows:
        if row.date in by_symbol[row.symbol]:
            raise ValueError(f"duplicate OHLCV row: {row.symbol}:{row.date}")
        by_symbol[row.symbol][row.date] = row
    return by_symbol


def _outcome_prices(
    *,
    symbol: str,
    entry_date: date,
    max_hold_days: int,
    bars_by_date: dict[date, OhlcvRow],
    finalized_at: datetime,
) -> dict[str, Any] | None:
    if finalized_at.tzinfo is None:
        raise ValueError("finalized_at must be timezone-aware")
    finalized_at = finalized_at.astimezone(UTC)
    sessions = required_session_dates(entry_date, max_hold_days)
    fixed_date = sessions[-1]
    if finalized_at < daily_bar_available_at(entry_date):
        return None

    entry_bar = bars_by_date.get(entry_date)
    if entry_bar is None:
        raise ValueError(f"missing completed entry OHLCV: {symbol}:{entry_date}")
    entry_price = entry_bar.open
    if entry_price <= 0:
        raise ValueError(f"non-positive official entry open: {symbol}:{entry_date}")
    stop_price = entry_price * (Decimal("1") - STOP_LOSS_PCT)

    for session_date in sessions:
        if finalized_at < daily_bar_available_at(session_date):
            return None
        bar = bars_by_date.get(session_date)
        if bar is None:
            raise ValueError(f"missing completed outcome OHLCV: {symbol}:{session_date}")
        if bar.open <= stop_price:
            return {
                "official_entry_open": entry_price,
                "fixed_exit_date": fixed_date,
                "actual_exit_date": session_date,
                "exit_reason": "gap_through_catastrophic_stop",
                "modeled_exit_price": bar.open,
                "stop_price": stop_price,
                "official_exit_bar_open": bar.open,
                "official_exit_bar_high": bar.high,
                "official_exit_bar_low": bar.low,
                "official_exit_bar_close": bar.close,
            }
        if bar.low <= stop_price:
            return {
                "official_entry_open": entry_price,
                "fixed_exit_date": fixed_date,
                "actual_exit_date": session_date,
                "exit_reason": "catastrophic_stop",
                "modeled_exit_price": stop_price,
                "stop_price": stop_price,
                "official_exit_bar_open": bar.open,
                "official_exit_bar_high": bar.high,
                "official_exit_bar_low": bar.low,
                "official_exit_bar_close": bar.close,
            }

    exit_bar = bars_by_date[fixed_date]
    return {
        "official_entry_open": entry_price,
        "fixed_exit_date": fixed_date,
        "actual_exit_date": fixed_date,
        "exit_reason": "fixed_20d_close",
        "modeled_exit_price": exit_bar.close,
        "stop_price": stop_price,
        "official_exit_bar_open": exit_bar.open,
        "official_exit_bar_high": exit_bar.high,
        "official_exit_bar_low": exit_bar.low,
        "official_exit_bar_close": exit_bar.close,
    }


def build_outcome_record(
    *,
    source_row: dict[str, Any],
    candidate: EventPaperCandidate,
    prices: dict[str, Any],
    ohlcv_path: Path,
    ohlcv_sha256: str,
    finalized_at: datetime,
    previous_outcome_sha256: str | None,
) -> dict[str, Any]:
    entry_price = Decimal(prices["official_entry_open"])
    exit_price = Decimal(prices["modeled_exit_price"])
    gross_return = (exit_price / entry_price) - Decimal("1")
    net_return = (exit_price * (Decimal("1") - COST_PER_SIDE_RATE) / entry_price) - (
        Decimal("1") + COST_PER_SIDE_RATE
    )
    record: dict[str, Any] = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "source_record_sha256": source_row["record_sha256"],
        "source_signal_date": source_row["signal_date"],
        "source_artifact_sha256": source_row["artifact_sha256"],
        "strategy_key": source_row["strategy_key"],
        "execution_candidate_id": candidate.execution_candidate_id,
        "symbol": candidate.symbol,
        "entry_date": candidate.entry_date.isoformat(),
        "fixed_exit_date": prices["fixed_exit_date"].isoformat(),
        "actual_exit_date": prices["actual_exit_date"].isoformat(),
        "exit_reason": prices["exit_reason"],
        "official_entry_open": str(entry_price),
        "stop_price": str(prices["stop_price"]),
        "modeled_exit_price": str(exit_price),
        "official_exit_bar_open": str(prices["official_exit_bar_open"]),
        "official_exit_bar_high": str(prices["official_exit_bar_high"]),
        "official_exit_bar_low": str(prices["official_exit_bar_low"]),
        "official_exit_bar_close": str(prices["official_exit_bar_close"]),
        "gross_return": str(gross_return),
        "round_trip_cost_rate": str(ROUND_TRIP_COST_RATE),
        "net_return_after_cost": str(net_return),
        "ohlcv_path": str(ohlcv_path),
        "ohlcv_sha256": ohlcv_sha256,
        "finalized_at": finalized_at.astimezone(UTC).isoformat(),
        "outcome_status": "finalized_registered_backtest_shadow",
        "evidence_class": "registered_backtest_shadow",
        "official_entry_reconciled": True,
        "official_exit_reconciled": True,
        "paper_execution_observed": False,
        "execution_evidence_eligible": False,
        "comparable_to_registered_backtest": True,
        "previous_outcome_sha256": previous_outcome_sha256,
    }
    record["outcome_sha256"] = canonical_sha256(record)
    return record


def finalize_due_outcomes(
    *,
    source_ledger_path: Path,
    outcome_ledger_path: Path,
    ohlcv_path: Path,
    finalized_at: datetime,
) -> dict[str, int]:
    if finalized_at.tzinfo is None:
        raise ValueError("finalized_at must be timezone-aware")
    source_rows = read_source_ledger(source_ledger_path)
    outcome_rows = read_outcome_ledger(outcome_ledger_path)
    source_by_hash = {str(row["record_sha256"]): row for row in source_rows}
    for outcome_row in outcome_rows:
        source_row = source_by_hash.get(str(outcome_row["source_record_sha256"]))
        if source_row is None:
            raise ValueError(
                f"outcome references a missing source record: {outcome_row['source_record_sha256']}"
            )
        bindings = (
            ("source_signal_date", "signal_date"),
            ("source_artifact_sha256", "artifact_sha256"),
            ("strategy_key", "strategy_key"),
        )
        for outcome_key, source_key in bindings:
            if outcome_row.get(outcome_key) != source_row.get(source_key):
                raise ValueError(
                    f"outcome/source {outcome_key} mismatch: {outcome_row['outcome_sha256']}"
                )
        if outcome_row.get("execution_candidate_id") not in source_row.get(
            "execution_candidate_ids", []
        ):
            raise ValueError(
                "outcome candidate is absent from its source record: "
                f"{outcome_row['outcome_sha256']}"
            )
    existing = {
        (str(row["source_record_sha256"]), str(row["execution_candidate_id"]))
        for row in outcome_rows
    }
    previous_outcome_sha256 = outcome_rows[-1]["outcome_sha256"] if outcome_rows else None
    pending_candidates: list[tuple[dict[str, Any], EventPaperCandidate]] = []
    blocked_incomplete = 0
    for source_row in source_rows:
        if source_row.get("economic_outcome_status") != "pending_forward_exit":
            continue
        loaded = load_event_paper_artifact(Path(str(source_row["artifact_path"])))
        if loaded.sha256 != source_row.get("artifact_sha256"):
            raise ValueError(f"source artifact hash mismatch: {source_row['record_sha256']}")
        artifact = loaded.artifact
        artifact_candidate_ids = sorted(
            candidate.execution_candidate_id for candidate in artifact.candidates
        )
        source_candidate_ids = list(source_row.get("execution_candidate_ids", []))
        if source_row.get("schema_version") != 1:
            raise ValueError(f"unsupported source schema: {source_row['record_sha256']}")
        if source_row.get("strategy_key") != EVENT_STRATEGY_KEY:
            raise ValueError(f"source strategy mismatch: {source_row['record_sha256']}")
        if source_row.get("signal_date") != artifact.signal_date.isoformat():
            raise ValueError(f"source signal date mismatch: {source_row['record_sha256']}")
        if source_row.get("source_received_at") != artifact.fetched_at.isoformat():
            raise ValueError(f"source receipt mismatch: {source_row['record_sha256']}")
        if source_row.get("candidate_count") != len(artifact.candidates):
            raise ValueError(f"source candidate count mismatch: {source_row['record_sha256']}")
        if source_row.get("complete_candidate_count") != sum(
            candidate.feature_data_complete for candidate in artifact.candidates
        ):
            raise ValueError(
                f"source complete candidate count mismatch: {source_row['record_sha256']}"
            )
        if source_candidate_ids != artifact_candidate_ids:
            raise ValueError(f"source candidate IDs mismatch: {source_row['record_sha256']}")
        candidates = {
            candidate.execution_candidate_id: candidate for candidate in artifact.candidates
        }
        for execution_candidate_id in source_candidate_ids:
            key = (str(source_row["record_sha256"]), str(execution_candidate_id))
            if key in existing:
                continue
            candidate = candidates.get(str(execution_candidate_id))
            if candidate is None:
                raise ValueError(
                    f"source candidate missing from artifact: {execution_candidate_id}"
                )
            if not candidate.feature_data_complete:
                blocked_incomplete += 1
                continue
            pending_candidates.append((source_row, candidate))

    if not pending_candidates:
        return {
            "source_rows": len(source_rows),
            "existing_outcomes": len(outcome_rows),
            "finalized": 0,
            "pending": 0,
            "blocked_incomplete": blocked_incomplete,
        }

    symbols = {candidate.symbol for _, candidate in pending_candidates}
    start_date = min(candidate.entry_date for _, candidate in pending_candidates)
    end_date = max(
        fixed_exit_date(candidate.entry_date, candidate.max_hold_days)
        for _, candidate in pending_candidates
    )
    ohlcv_sha256 = file_sha256(ohlcv_path)
    bars_by_symbol = _bars_by_symbol_date(
        read_ohlcv_csv(
            ohlcv_path,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )
    )

    new_rows: list[dict[str, Any]] = []
    pending = 0
    for source_row, candidate in pending_candidates:
        key = (str(source_row["record_sha256"]), candidate.execution_candidate_id)
        prices = _outcome_prices(
            symbol=candidate.symbol,
            entry_date=candidate.entry_date,
            max_hold_days=candidate.max_hold_days,
            bars_by_date=bars_by_symbol.get(candidate.symbol, {}),
            finalized_at=finalized_at,
        )
        if prices is None:
            pending += 1
            continue
        record = build_outcome_record(
            source_row=source_row,
            candidate=candidate,
            prices=prices,
            ohlcv_path=ohlcv_path,
            ohlcv_sha256=ohlcv_sha256,
            finalized_at=finalized_at,
            previous_outcome_sha256=previous_outcome_sha256,
        )
        new_rows.append(record)
        existing.add(key)
        previous_outcome_sha256 = record["outcome_sha256"]

    if new_rows:
        outcome_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with outcome_ledger_path.open("a", encoding="utf-8") as handle:
            for record in new_rows:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "source_rows": len(source_rows),
        "existing_outcomes": len(outcome_rows),
        "finalized": len(new_rows),
        "pending": pending,
        "blocked_incomplete": blocked_incomplete,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_SOURCE_LEDGER)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOME_LEDGER)
    parser.add_argument("--ohlcv", type=Path, default=DEFAULT_OHLCV)
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    args = parser.parse_args()
    finalized_at = args.as_of or datetime.now(UTC)
    summary = finalize_due_outcomes(
        source_ledger_path=args.ledger,
        outcome_ledger_path=args.outcomes,
        ohlcv_path=args.ohlcv,
        finalized_at=finalized_at,
    )
    print("event_forward_outcomes " + " ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
