#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from strategy_rule.event_paper.artifact import (
    EventArtifactError,
    EventPaperArtifact,
    EventPaperCandidate,
    LoadedEventPaperArtifact,
    load_event_paper_artifact,
)
from strategy_rule.event_paper.models import (
    EVENT_EXECUTION_PROFILE,
    EVENT_EXECUTION_STRATEGY_KEY,
    EVENT_MAX_BOOK_AGE_SECONDS,
    EVENT_MAX_FUTURE_SKEW_SECONDS,
    EVENT_SIGNAL_TOPIC,
    EventPaperPublishedRecord,
    EventPaperPublishReceipt,
    parse_claim_json,
)
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import deterministic_strategy_signal_id

JST = ZoneInfo("Asia/Tokyo")


class ObservationInputError(RuntimeError):
    """Raised when an artifact/receipt pair cannot be safely reconciled."""


@dataclass(frozen=True, slots=True)
class SupabaseRows:
    strategy_logs: list[dict[str, Any]]
    aggregator_logs: list[dict[str, Any]]
    trades_paper: list[dict[str, Any]]
    positions: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile event cluster paper candidates with Supabase paper execution rows."
    )
    parser.add_argument("--candidates-json", type=Path, required=True)
    parser.add_argument(
        "--publish-receipt-json",
        type=Path,
        help="Separate receipt written by strategy_rule event-paper-publish",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument(
        "--to-date",
        type=date.fromisoformat,
        help="Inclusive final JST execution date.",
    )
    parser.add_argument("--skip-supabase", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    try:
        loaded_artifact = load_event_paper_artifact(args.candidates_json)
        receipt = (
            None
            if args.publish_receipt_json is None
            else load_and_validate_publish_receipt(
                args.publish_receipt_json,
                artifact=loaded_artifact,
            )
        )
    except (EventArtifactError, ObservationInputError) as exc:
        print(f"event paper observation input rejected: {exc}", file=sys.stderr)
        return 2

    rows: SupabaseRows | None = None
    if not args.skip_supabase:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        if url and key:
            with httpx.Client(
                base_url=url.rstrip("/"),
                timeout=args.timeout,
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            ) as client:
                rows = fetch_supabase_rows(
                    client,
                    artifact=loaded_artifact.artifact,
                    receipt=receipt,
                    from_date=args.from_date,
                    to_date=args.to_date,
                )
        else:
            print(
                "SUPABASE_URL / SUPABASE_SECRET_KEY missing; writing report without "
                "Supabase reconciliation",
                file=sys.stderr,
            )

    report = build_report(loaded_artifact.artifact, receipt=receipt, rows=rows)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, report["rows"])
    print(
        "event_paper_observation_report "
        f"candidates={report['summary']['candidate_count']} "
        f"published={report['summary']['published_count']} "
        f"ambiguous={report['summary']['ambiguous_count']} "
        f"with_supabase={report['summary']['with_supabase']} "
        f"output={args.output_json}"
    )
    return 0


def load_and_validate_publish_receipt(
    path: Path,
    *,
    artifact: LoadedEventPaperArtifact,
) -> EventPaperPublishReceipt:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ObservationInputError(f"cannot read publication receipt: {exc}") from exc
    try:
        receipt = EventPaperPublishReceipt.model_validate_json(raw)
    except (UnicodeDecodeError, ValidationError) as exc:
        raise ObservationInputError(f"publication receipt is invalid: {exc}") from exc
    validate_publish_receipt(artifact=artifact, receipt=receipt)
    return receipt


def validate_publish_receipt(
    *,
    artifact: LoadedEventPaperArtifact,
    receipt: EventPaperPublishReceipt,
) -> None:
    """Bind a publication-attempt receipt to one exact causal artifact."""

    if receipt.artifact_sha256 != artifact.sha256:
        raise ObservationInputError("publication receipt artifact_sha256 mismatch")
    try:
        artifact.artifact.validate_target_date(receipt.target_date)
    except EventArtifactError as exc:
        raise ObservationInputError(str(exc)) from exc

    candidates = {row.execution_candidate_id: row for row in artifact.artifact.candidates}
    selected_ids = receipt.selected_execution_candidate_ids
    if len(selected_ids) != len(set(selected_ids)):
        raise ObservationInputError("publication receipt has duplicate selected occurrence")
    unknown_selected = set(selected_ids) - set(candidates)
    if unknown_selected:
        raise ObservationInputError(
            "publication receipt selected occurrence is absent from artifact: "
            + ",".join(sorted(unknown_selected))
        )
    record_ids = [row.execution_candidate_id for row in receipt.published]
    if len(record_ids) != len(set(record_ids)):
        raise ObservationInputError("publication receipt has duplicate execution_candidate_id")
    expected_ids = set(selected_ids)
    actual_ids = set(record_ids)
    if expected_ids != actual_ids:
        missing = ",".join(sorted(expected_ids - actual_ids)) or "-"
        extra = ",".join(sorted(actual_ids - expected_ids)) or "-"
        raise ObservationInputError(
            f"publication receipt candidate coverage mismatch: missing={missing} extra={extra}"
        )
    if len(receipt.published) != len(selected_ids):
        raise ObservationInputError("publication receipt selected candidate count mismatch")

    signal_ids: set[str] = set()
    for record in receipt.published:
        candidate = candidates[record.execution_candidate_id]
        if record.artifact_sha256 != artifact.sha256:
            raise ObservationInputError(
                f"publication record artifact_sha256 mismatch: {record.execution_candidate_id}"
            )
        if record.strategy_key != EVENT_EXECUTION_STRATEGY_KEY:
            raise ObservationInputError(
                f"publication record strategy_key mismatch: {record.execution_candidate_id}"
            )
        if record.symbol != candidate.symbol:
            raise ObservationInputError(
                f"publication record symbol mismatch: {record.execution_candidate_id}"
            )
        if record.topic != EVENT_SIGNAL_TOPIC:
            raise ObservationInputError(
                f"publication record topic mismatch: {record.execution_candidate_id}"
            )
        expected_signal_id = deterministic_strategy_signal_id(
            strategy_key=EVENT_EXECUTION_STRATEGY_KEY,
            candidate_id=candidate.execution_candidate_id,
            source=SignalSource.RULE,
            symbol=candidate.symbol,
            action=Action.BUY,
        )
        if record.signal_id != str(expected_signal_id):
            raise ObservationInputError(
                f"publication record signal_id mismatch: {record.execution_candidate_id}"
            )
        if record.signal_id in signal_ids:
            raise ObservationInputError("publication receipt has duplicate signal_id")
        signal_ids.add(record.signal_id)
        if record.observed_ask <= 0:
            raise ObservationInputError(
                f"publication record observed_ask must be positive: {record.execution_candidate_id}"
            )
        if not record.raw_book_message_id.strip() or not record.publication_attempt_id.strip():
            raise ObservationInputError(
                f"publication record contains a blank attempt/message ID: "
                f"{record.execution_candidate_id}"
            )
        if record.book_received_at.tzinfo is None or record.attempted_at.tzinfo is None:
            raise ObservationInputError(
                f"publication record timestamps must be timezone-aware: "
                f"{record.execution_candidate_id}"
            )
        attempt_age = (record.attempted_at - record.book_received_at).total_seconds()
        if attempt_age < -EVENT_MAX_FUTURE_SKEW_SECONDS or attempt_age > EVENT_MAX_BOOK_AGE_SECONDS:
            raise ObservationInputError(
                f"publication attempt used an invalid selected book: "
                f"{record.execution_candidate_id}"
            )
        local_book = record.book_received_at.astimezone(JST)
        if local_book.date() != receipt.target_date or not (
            time(9, 0) <= local_book.time().replace(tzinfo=None) < time(9, 30)
        ):
            raise ObservationInputError(
                f"publication selected book outside target entry window: "
                f"{record.execution_candidate_id}"
            )
        local_attempted = record.attempted_at.astimezone(JST)
        attempted_time = local_attempted.time().replace(tzinfo=None)
        if local_attempted.date() != receipt.target_date or not (
            time(9, 0) <= attempted_time < time(9, 30)
        ):
            raise ObservationInputError(
                f"publication attempt outside target entry window: {record.execution_candidate_id}"
            )
        if record.publication_status == "ambiguous":
            if record.strategy_message_id is not None or record.published_at is not None:
                raise ObservationInputError(
                    f"ambiguous publication contains success metadata: "
                    f"{record.execution_candidate_id}"
                )
            continue

        if (
            record.strategy_message_id is None
            or not record.strategy_message_id.strip()
            or record.published_at is None
        ):
            raise ObservationInputError(
                f"confirmed publication is missing Pub/Sub metadata: "
                f"{record.execution_candidate_id}"
            )
        if record.published_at.tzinfo is None:
            raise ObservationInputError(
                f"publication record timestamps must be timezone-aware: "
                f"{record.execution_candidate_id}"
            )
        if record.published_at < record.attempted_at:
            raise ObservationInputError(
                f"publication predates durable attempt: {record.execution_candidate_id}"
            )
        local_published = record.published_at.astimezone(JST)
        published_time = local_published.time().replace(tzinfo=None)
        if local_published.date() != receipt.target_date or not (
            time(9, 0) <= published_time < time(9, 30)
        ):
            raise ObservationInputError(
                f"publication completed outside target entry window: "
                f"{record.execution_candidate_id}"
            )
        publication_age = (record.published_at - record.book_received_at).total_seconds()
        if (
            publication_age < -EVENT_MAX_FUTURE_SKEW_SECONDS
            or publication_age > EVENT_MAX_BOOK_AGE_SECONDS
        ):
            raise ObservationInputError(
                f"publication used a stale selected book: {record.execution_candidate_id}"
            )
    if any(not key.strip() or value < 0 for key, value in receipt.skipped_messages.items()):
        raise ObservationInputError("publication receipt has invalid skipped_messages")


def fetch_supabase_rows(
    client: httpx.Client,
    *,
    artifact: EventPaperArtifact,
    receipt: EventPaperPublishReceipt | None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> SupabaseRows:
    signal_ids = _published_signal_ids(receipt)
    symbols = sorted(row.symbol for row in artifact.candidates)
    strategy_logs = (
        _get(
            client,
            "/rest/v1/strategy_logs",
            {
                "select": "signal_id,source,symbol,action,confidence,reasoning,created_at",
                "signal_id": _in_filter(signal_ids),
            },
        )
        if signal_ids
        else []
    )
    aggregator_logs = (
        _get(
            client,
            "/rest/v1/aggregator_logs",
            {
                "select": (
                    "signal_id,symbol,action,confidence,signal_source,"
                    "strategy_signal_id_a,strategy_signal_id_b,created_at"
                ),
                "strategy_signal_id_a": _in_filter(signal_ids),
            },
        )
        if signal_ids
        else []
    )
    unified_ids = [str(row["signal_id"]) for row in aggregator_logs]
    trade_filters = {
        "select": (
            "trade_id,order_id,symbol,side,quantity,price,signal_source,"
            "unified_signal_id,position_generation_id,executed_at"
        ),
        "unified_signal_id": _in_filter(unified_ids),
        "order": "executed_at.asc",
    }
    _apply_executed_at_bounds(trade_filters, from_date=from_date, to_date=to_date)
    trades_paper = _get(client, "/rest/v1/trades_paper", trade_filters) if unified_ids else []
    # Scheduled and stop-loss swing SELL fills intentionally have a null
    # unified_signal_id. Fetch symbol history separately, then attribute only
    # SELLs inside the generation opened by the exactly linked event BUY.
    symbol_trade_filters = {
        "select": (
            "trade_id,order_id,symbol,side,quantity,price,signal_source,"
            "unified_signal_id,position_generation_id,executed_at"
        ),
        "symbol": _in_filter(symbols),
        "order": "executed_at.asc",
    }
    history_start = from_date or min(
        (row.entry_date for row in artifact.candidates),
        default=None,
    )
    _apply_executed_at_bounds(
        symbol_trade_filters,
        from_date=history_start,
        to_date=to_date,
    )
    symbol_trades = (
        _get(client, "/rest/v1/trades_paper", symbol_trade_filters)
        if receipt is not None and symbols
        else []
    )
    trades_paper = _dedupe_by_key(trades_paper + symbol_trades, "trade_id")
    positions = (
        _get(
            client,
            "/rest/v1/positions",
            {
                "select": (
                    "symbol,trade_type,side,quantity,entry_price,current_price,"
                    "unrealized_pnl,holding_type,stop_loss_price,max_hold_days,"
                    "scheduled_exit_date,scheduled_exit_time,opened_at,position_generation_id"
                ),
                "trade_type": "eq.paper",
                "symbol": _in_filter(symbols),
                "order": "symbol.asc",
            },
        )
        if receipt is not None and symbols
        else []
    )
    return SupabaseRows(
        strategy_logs=strategy_logs,
        aggregator_logs=aggregator_logs,
        trades_paper=trades_paper,
        positions=positions,
    )


def build_report(
    artifact: EventPaperArtifact,
    *,
    receipt: EventPaperPublishReceipt | None,
    rows: SupabaseRows | None,
) -> dict[str, Any]:
    published_by_candidate = {
        row.execution_candidate_id: row for row in (() if receipt is None else receipt.published)
    }
    strategy_by_id = _index(rows.strategy_logs if rows else [], "signal_id")
    aggregator_by_strategy_id = _index(rows.aggregator_logs if rows else [], "strategy_signal_id_a")
    positions_by_symbol = _index(rows.positions if rows else [], "symbol")
    trades_by_unified = _group(rows.trades_paper if rows else [], "unified_signal_id")
    trades_by_generation = _group(rows.trades_paper if rows else [], "position_generation_id")

    out_rows: list[dict[str, Any]] = []
    for candidate in artifact.candidates:
        published = published_by_candidate.get(candidate.execution_candidate_id)
        strategy_signal_id = None if published is None else published.signal_id
        raw_strategy_log = (
            strategy_by_id.get(str(strategy_signal_id)) if strategy_signal_id else None
        )
        strategy_log = _matching_strategy_log(
            raw_strategy_log,
            candidate=candidate,
            published=published,
            artifact_sha256=(None if receipt is None else receipt.artifact_sha256),
        )
        raw_aggregator_log = (
            aggregator_by_strategy_id.get(str(strategy_signal_id)) if strategy_signal_id else None
        )
        aggregator_log = _matching_aggregator_log(
            raw_aggregator_log,
            symbol=candidate.symbol,
        )
        unified_signal_id = None if aggregator_log is None else aggregator_log.get("signal_id")
        linked_trades = (
            trades_by_unified.get(str(unified_signal_id), []) if unified_signal_id else []
        )
        linked_trades = [row for row in linked_trades if row.get("symbol") == candidate.symbol]
        buy_trade = _first_trade(
            [row for row in linked_trades if row.get("signal_source") == "RULE"],
            side="BUY",
        )
        position_generation_id, exit_lineage_status, exit_trades = _generation_exits(
            buy_trade=buy_trade,
            trades_by_generation=trades_by_generation,
            symbol=candidate.symbol,
        )
        raw_position = positions_by_symbol.get(candidate.symbol)
        position = _matching_position(
            raw_position,
            position_generation_id=(
                position_generation_id if exit_lineage_status == "verified" else None
            ),
        )
        exit_summary = _exit_summary(exit_trades)
        intended = None if published is None else published.observed_ask
        buy_price = _decimal(None if buy_trade is None else buy_trade.get("price"))
        position_stop = None if position is None else position.get("stop_loss_price")
        out_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "execution_candidate_id": candidate.execution_candidate_id,
                "selection_strategy_key": artifact.strategy_key,
                "strategy_key": EVENT_EXECUTION_STRATEGY_KEY,
                "execution_profile": EVENT_EXECUTION_PROFILE,
                "comparable_to_registered_backtest": False,
                "symbol": candidate.symbol,
                "signal_date": candidate.signal_date.isoformat(),
                "entry_date": candidate.entry_date.isoformat(),
                "feature_cutoff_at": candidate.feature_cutoff_at.isoformat(),
                "data_available_at": candidate.data_available_at.isoformat(),
                "source_received_at": candidate.source_received_at.isoformat(),
                "feature_data_complete": candidate.feature_data_complete,
                "selection_status": candidate.selection_status,
                "required_ohlcv_session_date": candidate.required_ohlcv_session_date.isoformat(),
                "valuation_reference_price": (
                    None
                    if candidate.valuation_reference_price is None
                    else str(candidate.valuation_reference_price)
                ),
                "valuation_reference_bar_date": (
                    None
                    if candidate.valuation_reference_bar_date is None
                    else candidate.valuation_reference_bar_date.isoformat()
                ),
                "valuation_reference_available_at": (
                    None
                    if candidate.valuation_reference_available_at is None
                    else candidate.valuation_reference_available_at.isoformat()
                ),
                "intended_entry_price": None if intended is None else str(intended),
                "observed_ask": None if intended is None else str(intended),
                "book_received_at": (
                    None if published is None else published.book_received_at.isoformat()
                ),
                "publication_status": (None if published is None else published.publication_status),
                "publication_attempt_id": (
                    None if published is None else published.publication_attempt_id
                ),
                "attempted_at": (None if published is None else published.attempted_at.isoformat()),
                "published_at": (
                    None
                    if published is None or published.published_at is None
                    else published.published_at.isoformat()
                ),
                "raw_book_message_id": (
                    None if published is None else published.raw_book_message_id
                ),
                "strategy_message_id": (
                    None if published is None else published.strategy_message_id
                ),
                "catastrophic_stop_pct": str(candidate.catastrophic_stop_pct),
                "stop_loss_price": position_stop,
                "position_stop_loss_price": position_stop,
                "max_hold_days": candidate.max_hold_days,
                "strategy_signal_id": strategy_signal_id,
                "strategy_log_found": strategy_log is not None,
                "unified_signal_id": unified_signal_id,
                "aggregator_log_found": aggregator_log is not None,
                "buy_trade_id": None if buy_trade is None else buy_trade.get("trade_id"),
                "buy_executed_at": None if buy_trade is None else buy_trade.get("executed_at"),
                "buy_price": None if buy_trade is None else buy_trade.get("price"),
                "buy_quantity": None if buy_trade is None else buy_trade.get("quantity"),
                "entry_slippage_bps": _slippage_bps(fill=buy_price, intended=intended),
                "position_generation_id": position_generation_id,
                "exit_lineage_status": exit_lineage_status,
                **exit_summary,
                "position_open": position is not None,
                "position_quantity": None if position is None else position.get("quantity"),
                "position_entry_price": (None if position is None else position.get("entry_price")),
                "position_current_price": (
                    None if position is None else position.get("current_price")
                ),
                "position_unrealized_pnl": (
                    None if position is None else position.get("unrealized_pnl")
                ),
                "position_scheduled_exit_date": (
                    None if position is None else position.get("scheduled_exit_date")
                ),
                "position_scheduled_exit_time": (
                    None if position is None else position.get("scheduled_exit_time")
                ),
                "reconciliation_status": _status(
                    strategy_signal_id=strategy_signal_id,
                    publication_status=(
                        None if published is None else published.publication_status
                    ),
                    receipt_loaded=receipt is not None,
                    supabase_checked=rows is not None,
                    strategy_log=strategy_log,
                    aggregator_log=aggregator_log,
                    buy_trade=buy_trade,
                    exit_lineage_status=exit_lineage_status,
                    exit_count=exit_summary["exit_count"],
                    position=position,
                ),
            }
        )
    return {
        "candidate_id": artifact.candidate_id,
        "selection_strategy_key": artifact.strategy_key,
        "strategy_key": EVENT_EXECUTION_STRATEGY_KEY,
        "execution_profile": EVENT_EXECUTION_PROFILE,
        "comparable_to_registered_backtest": False,
        "source_mode": artifact.mode if receipt is None else receipt.mode,
        "publication_artifact_sha256": None if receipt is None else receipt.artifact_sha256,
        "selected_execution_candidate_ids": (
            [] if receipt is None else receipt.selected_execution_candidate_ids
        ),
        "summary": {
            "execution_profile": EVENT_EXECUTION_PROFILE,
            "comparable_to_registered_backtest": False,
            "candidate_count": len(out_rows),
            "feature_data_incomplete_count": sum(
                not row.feature_data_complete for row in artifact.candidates
            ),
            "published_count": (
                0
                if receipt is None
                else sum(row.publication_status == "confirmed" for row in receipt.published)
            ),
            "ambiguous_count": (
                0
                if receipt is None
                else sum(row.publication_status == "ambiguous" for row in receipt.published)
            ),
            "publication_receipt_loaded": receipt is not None,
            "with_supabase": rows is not None,
            "strategy_log_count": 0 if rows is None else len(rows.strategy_logs),
            "aggregator_log_count": 0 if rows is None else len(rows.aggregator_logs),
            "trades_paper_count": 0 if rows is None else len(rows.trades_paper),
            "open_position_count": sum(bool(row["position_open"]) for row in out_rows),
            "unverifiable_generation_lineage_count": sum(
                str(row["exit_lineage_status"]).startswith("unverifiable_") for row in out_rows
            ),
            "status_counts": _status_counts(out_rows),
        },
        "rows": out_rows,
    }


def _get(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def _published_signal_ids(receipt: EventPaperPublishReceipt | None) -> list[str]:
    if receipt is None:
        return []
    return [row.signal_id for row in receipt.published]


def _in_filter(values: list[str]) -> str:
    return "in.(" + ",".join(values) + ")"


def _jst_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, JST).astimezone(UTC)


def _apply_executed_at_bounds(
    filters: dict[str, str],
    *,
    from_date: date | None,
    to_date: date | None,
) -> None:
    lower = None if from_date is None else _jst_midnight(from_date).isoformat()
    upper = None if to_date is None else _jst_midnight(to_date + timedelta(days=1)).isoformat()
    if lower is not None and upper is not None:
        filters["and"] = f"(executed_at.gte.{lower},executed_at.lt.{upper})"
    elif lower is not None:
        filters["executed_at"] = f"gte.{lower}"
    elif upper is not None:
        filters["executed_at"] = f"lt.{upper}"


def _dedupe_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row[key])] = row
    return list(out.values())


def _index(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row[key]): row for row in rows if row.get(key) is not None}


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get(key) is None:
            continue
        grouped.setdefault(str(row[key]), []).append(row)
    return grouped


def _matching_strategy_log(
    row: dict[str, Any] | None,
    *,
    candidate: EventPaperCandidate,
    published: EventPaperPublishedRecord | None,
    artifact_sha256: str | None,
) -> dict[str, Any] | None:
    if row is None or published is None or artifact_sha256 is None:
        return None
    if (
        row.get("signal_id") != published.signal_id
        or row.get("symbol") != candidate.symbol
        or row.get("source") != "RULE"
        or row.get("action") != "BUY"
    ):
        return None
    try:
        claim = parse_claim_json(row.get("reasoning"))
    except (TypeError, ValueError):
        return None
    fields = claim.signal_fields
    attempt = claim.publication_attempt
    publication = claim.publication
    if (
        claim.artifact_sha256 != artifact_sha256
        or claim.raw_book_message_id != published.raw_book_message_id
        or claim.raw_book_received_at != published.book_received_at
        or claim.cluster_id != candidate.cluster_id
        or claim.observation_id != candidate.observation_id
        or claim.event_ids != candidate.event_ids
        or claim.signal_date != candidate.signal_date
        or claim.entry_date != candidate.entry_date
        or fields.candidate_id != candidate.execution_candidate_id
        or fields.symbol != candidate.symbol
        or fields.price != published.observed_ask
        or attempt is None
        or attempt.attempt_id != published.publication_attempt_id
        or attempt.attempted_at != published.attempted_at
        or _decimal(row.get("confidence")) != Decimal(str(fields.confidence))
        or _datetime(row.get("created_at")) != fields.created_at.astimezone(UTC)
    ):
        return None
    if published.publication_status == "confirmed":
        if (
            publication is None
            or publication.attempt_id != published.publication_attempt_id
            or publication.topic != published.topic
            or publication.strategy_message_id != published.strategy_message_id
            or publication.published_at != published.published_at
        ):
            return None
    elif publication is not None:
        return None
    return row


def _matching_aggregator_log(
    row: dict[str, Any] | None,
    *,
    symbol: str,
) -> dict[str, Any] | None:
    if row is None:
        return None
    if (
        row.get("symbol") != symbol
        or row.get("signal_source") != "RULE"
        or row.get("action") != "BUY"
    ):
        return None
    return row


def _matching_position(
    position: dict[str, Any] | None,
    *,
    position_generation_id: str | None,
) -> dict[str, Any] | None:
    if position is None or position_generation_id is None:
        return None
    if _nonempty_text(position.get("position_generation_id")) != position_generation_id:
        return None
    return position


def _first_trade(rows: list[dict[str, Any]], *, side: str) -> dict[str, Any] | None:
    for row in sorted(rows, key=lambda item: str(item.get("executed_at") or "")):
        if row.get("side") == side:
            return row
    return None


def _generation_exits(
    *,
    buy_trade: dict[str, Any] | None,
    trades_by_generation: dict[str, list[dict[str, Any]]],
    symbol: str,
) -> tuple[str | None, str, list[dict[str, Any]]]:
    """Return every SELL in the exact immutable position generation.

    The legacy schema did not write a generation key to ``trades_paper``.
    Never infer it from timestamps or a subsequent BUY: both would turn a
    reportable uncertainty into a false attribution.
    """

    if buy_trade is None:
        return None, "missing_event_buy", []
    position_generation_id = _nonempty_text(buy_trade.get("position_generation_id"))
    if position_generation_id is None:
        return None, "unverifiable_legacy_lineage", []
    # A later BUY can add to an already-open generation. Its eventual SELLs
    # belong to the original position, not necessarily to this event entry.
    # The event report has no lot-allocation contract, so only the generation's
    # first BUY (whose trade ID is the generation ID) can ever be attributable.
    if _nonempty_text(buy_trade.get("trade_id")) != position_generation_id:
        return position_generation_id, "unverifiable_non_origin_event_buy", []
    generation_trades = [
        row
        for row in trades_by_generation.get(position_generation_id, [])
        if row.get("symbol") == symbol
    ]
    if any(
        row.get("side") == "BUY" and _nonempty_text(row.get("trade_id")) != position_generation_id
        for row in generation_trades
    ):
        return position_generation_id, "unverifiable_mixed_generation_buys", []
    exits = [row for row in generation_trades if row.get("side") == "SELL"]
    return position_generation_id, "verified", _sorted_trades(exits)


def _exit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return JSON-safe, aggregate exit evidence without single-SELL loss."""

    total_quantity = 0
    total_notional = Decimal("0")
    complete = True
    normalized: list[dict[str, Any]] = []
    trade_ids: list[str] = []
    for row in rows:
        quantity = _positive_int(row.get("quantity"))
        price = _decimal(row.get("price"))
        notional = None if quantity is None or price is None else price * quantity
        if quantity is None or price is None:
            complete = False
        else:
            assert notional is not None
            total_quantity += quantity
            total_notional += notional
        trade_id = _nonempty_text(row.get("trade_id"))
        if trade_id is not None:
            trade_ids.append(trade_id)
        normalized.append(
            {
                "trade_id": trade_id,
                "order_id": _nonempty_text(row.get("order_id")),
                "executed_at": _timestamp_text(row.get("executed_at")),
                "quantity": quantity,
                "price": None if price is None else str(price),
                "notional": None if notional is None else str(notional),
                "signal_source": _nonempty_text(row.get("signal_source")),
                "unified_signal_id": _nonempty_text(row.get("unified_signal_id")),
            }
        )
    aggregate_quantity = total_quantity if complete else None
    aggregate_notional = total_notional if complete else None
    return {
        "exit_trades": normalized,
        "exit_trade_ids": ",".join(trade_ids),
        "exit_count": len(normalized),
        "exit_quantity": aggregate_quantity,
        "exit_notional": None if aggregate_notional is None else str(aggregate_notional),
        "exit_vwap": (
            None
            if aggregate_notional is None or aggregate_quantity is None or aggregate_quantity == 0
            else str(aggregate_notional / aggregate_quantity)
        ),
    }


def _sorted_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _timestamp_text(row.get("executed_at")) or "",
            _nonempty_text(row.get("trade_id")) or "",
        ),
    )


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _timestamp_text(value: Any) -> str | None:
    parsed = _datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    return _nonempty_text(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _slippage_bps(*, fill: Decimal | None, intended: Decimal | None) -> str | None:
    if fill is None or intended is None or intended <= 0:
        return None
    return str(((fill / intended) - Decimal("1")) * Decimal("10000"))


def _status(
    *,
    strategy_signal_id: Any,
    publication_status: str | None,
    receipt_loaded: bool,
    supabase_checked: bool,
    strategy_log: dict[str, Any] | None,
    aggregator_log: dict[str, Any] | None,
    buy_trade: dict[str, Any] | None,
    exit_lineage_status: str,
    exit_count: Any,
    position: dict[str, Any] | None,
) -> str:
    if strategy_signal_id is None:
        return "not_selected_in_receipt" if receipt_loaded else "dry_run_only"
    if not supabase_checked:
        return "published_unqueried"
    if strategy_log is None:
        return "missing_strategy_log"
    if aggregator_log is None:
        if publication_status == "ambiguous":
            return "publication_ambiguous"
        return "missing_aggregator_log"
    if buy_trade is None:
        return "missing_buy_fill"
    if exit_lineage_status.startswith("unverifiable_"):
        return "unverifiable_generation_lineage"
    if position is not None:
        return "open_position"
    if exit_count:
        return "closed_or_exited"
    return "no_open_position_no_sell"


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["reconciliation_status"])
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "execution_candidate_id",
        "selection_strategy_key",
        "strategy_key",
        "execution_profile",
        "comparable_to_registered_backtest",
        "symbol",
        "signal_date",
        "entry_date",
        "feature_cutoff_at",
        "data_available_at",
        "source_received_at",
        "feature_data_complete",
        "selection_status",
        "required_ohlcv_session_date",
        "valuation_reference_price",
        "valuation_reference_bar_date",
        "valuation_reference_available_at",
        "intended_entry_price",
        "observed_ask",
        "book_received_at",
        "publication_status",
        "publication_attempt_id",
        "attempted_at",
        "published_at",
        "raw_book_message_id",
        "strategy_message_id",
        "catastrophic_stop_pct",
        "stop_loss_price",
        "position_stop_loss_price",
        "max_hold_days",
        "strategy_signal_id",
        "strategy_log_found",
        "unified_signal_id",
        "aggregator_log_found",
        "buy_trade_id",
        "buy_executed_at",
        "buy_price",
        "buy_quantity",
        "entry_slippage_bps",
        "position_generation_id",
        "exit_lineage_status",
        "exit_trade_ids",
        "exit_count",
        "exit_quantity",
        "exit_notional",
        "exit_vwap",
        "position_open",
        "position_quantity",
        "position_entry_price",
        "position_current_price",
        "position_unrealized_pnl",
        "position_scheduled_exit_date",
        "position_scheduled_exit_time",
        "reconciliation_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
