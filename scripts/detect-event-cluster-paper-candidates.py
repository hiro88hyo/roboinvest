#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from event_research_common import (
    CAT_STOP_PCT,
    build_candidate_features,
    build_events_from_financial_rows,
    cluster_earnings_dividend_increase_allows,
    cluster_earnings_dividend_value_guard_allows,
    cluster_trade_representatives,
    daily_bar_available_at,
    next_tse_business_date,
    read_jsonl,
    read_master_csv,
    read_ohlcv_csv,
)
from trade_contracts.event_research import EventRecord, ObservationRecord

CANDIDATE_ID = "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
ARTIFACT_SCHEMA_VERSION = 2
PER_THRESHOLD = Decimal("15")
MAX_HOLD_DAYS = 20
PUBLISH_DISABLED_REASON = (
    "inline event paper publish is disabled; use the separately gated "
    "strategy-rule event-paper-publish command after all safety checks pass"
)
TOKYO = ZoneInfo("Asia/Tokyo")
ENTRY_CUTOFF_TIME_JST = time(9, 0)
FETCH_METADATA_RECORD_TYPE = "fetch_metadata"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run detector for the cluster v1 paper observation candidate. "
            "This command never publishes StrategySignal messages."
        )
    )
    parser.add_argument("--financial-summary-jsonl", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--publish-paper",
        action="store_true",
        help=f"Disabled: {PUBLISH_DISABLED_REASON}.",
    )
    parser.add_argument(
        "--signal-date",
        type=date.fromisoformat,
        help="Restrict candidates/exclusions to this signal_date.",
    )
    parser.add_argument("--fetched-at", help="ISO timestamp used for event fetched_at metadata.")
    args = parser.parse_args()

    explicit_fetched_at = (
        parse_aware_timestamp(args.fetched_at, field="--fetched-at") if args.fetched_at else None
    )
    financial_rows = read_jsonl(args.financial_summary_jsonl)
    fetch_metadata_at, fetch_metadata_complete = validated_fetch_metadata(
        financial_rows,
        signal_date=args.signal_date,
    )
    fetched_at = fetch_metadata_at or explicit_fetched_at or datetime.now(tz=UTC)
    if args.signal_date is not None and fetch_metadata_complete:
        financial_rows = rows_for_completed_fetch(
            financial_rows,
            signal_date=args.signal_date,
            fetched_at=fetch_metadata_at,
        )
    ohlcv_rows = read_ohlcv_csv(args.ohlcv)
    events = build_events_from_financial_rows(
        financial_rows,
        ohlcv_rows=ohlcv_rows,
        fetched_at=fetched_at,
        entry_date_resolver=next_tse_business_date,
    )
    receipt_exclusions: list[dict[str, Any]] = []
    feature_exclusions: list[dict[str, Any]] = []
    eligible_event_ids: set[str] | None = None
    if args.signal_date is not None:
        selected_events = [
            event for event in events if date.fromisoformat(event.signal_date) == args.signal_date
        ]
        eligible_event_ids = set()
        for event in selected_events:
            receipt_reason = event_receipt_rejection_reason(event, fetched_at=event.fetched_at)
            if receipt_reason is not None:
                receipt_exclusions.append(
                    {
                        "cluster_id": event.event_cluster_id,
                        "symbol": event.symbol,
                        "signal_date": event.signal_date,
                        "reason": receipt_reason,
                        "event_ids": [event.event_id],
                        "disclosed_at": event.disclosed_at.isoformat(),
                        "fetched_at": event.fetched_at.isoformat(),
                        "entry_date": event.entry_date,
                    }
                )
                continue
            eligible_event_ids.add(event.event_id)
    master = read_master_csv(args.master)
    observations = build_candidate_features(
        events,
        ohlcv_rows=ohlcv_rows,
        master=master,
    )
    if args.signal_date is not None:
        events = [
            event for event in events if date.fromisoformat(event.signal_date) == args.signal_date
        ]
        observations = [
            obs
            for obs in observations
            if date.fromisoformat(obs.signal_date) == args.signal_date
            and eligible_event_ids is not None
            and obs.event_id in eligible_event_ids
        ]
        observation_event_ids = {obs.event_id for obs in observations}
        for event in events:
            if (
                eligible_event_ids is not None
                and event.event_id in eligible_event_ids
                and event.event_id not in observation_event_ids
            ):
                feature_exclusions.append(
                    feature_exclusion_row(event, reason="missing_feature_history")
                )
    symbol_names = {symbol: row.symbol_name for symbol, row in master.items()}
    source_received_by_event_id = {
        event.event_id: max(event.disclosed_at, event.fetched_at) for event in events
    }
    candidates, exclusions = detect_candidates(
        observations,
        symbol_names=symbol_names,
        source_received_by_event_id=source_received_by_event_id,
    )
    exclusions = [*receipt_exclusions, *feature_exclusions, *exclusions]
    published: list[dict[str, Any]] = []
    publish_enabled = False
    if args.publish_paper:
        raise SystemExit(f"{PUBLISH_DISABLED_REASON}; no signals published")

    mode = "paper_publish" if publish_enabled else "dry_run"
    receipt_provenance = receipt_provenance_label(
        signal_date=args.signal_date,
        financial_rows=financial_rows,
        fetch_metadata_at=fetch_metadata_at,
        fetch_metadata_complete=fetch_metadata_complete,
        explicit_fetched_at=explicit_fetched_at,
    )
    source_coverage_window_verified = (
        args.signal_date is not None
        and fetch_metadata_at is not None
        and source_coverage_window_allows(args.signal_date, fetched_at=fetch_metadata_at)
    )
    causality_verified = fetch_metadata_complete and source_coverage_window_verified
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "strategy_key": CANDIDATE_ID,
        "candidate_id": CANDIDATE_ID,
        "mode": mode,
        "paper_live_enabled": False,
        "paper_publish_enabled": publish_enabled,
        "publish_enabled": publish_enabled,
        "causality_verified": causality_verified,
        "causality": {
            "candidate_features_use_forward_bars": False,
            "candidate_artifact_contains_entry_price": False,
            "entry_date_source": "tse_business_calendar",
            "data_receipt_checked": causality_verified,
            "receipt_provenance": receipt_provenance,
            "fetch_completion_verified": fetch_metadata_complete,
            "source_coverage_window_verified": source_coverage_window_verified,
            "paper_publish_disabled": True,
        },
        "signal_date": None if args.signal_date is None else args.signal_date.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "rule": {
            "cluster_contains": ["earnings_result", "dividend_revision:increase"],
            "forecast_per_threshold": str(PER_THRESHOLD),
            "missing_forecast_per": "allowed",
            "max_hold_days": MAX_HOLD_DAYS,
            "catastrophic_stop_pct": str(CAT_STOP_PCT),
        },
        "summary": {
            "event_count": len(events),
            "observation_count": len(observations),
            "late_data_receipt_count": count_exclusions(
                receipt_exclusions, reason="late_data_receipt"
            ),
            "fetched_before_disclosure_count": count_exclusions(
                receipt_exclusions, reason="fetched_before_disclosure"
            ),
            "missing_signal_date_ohlcv_count": sum(
                candidate["feature_data_complete"] is False for candidate in candidates
            ),
            "missing_feature_history_count": count_exclusions(
                feature_exclusions, reason="missing_feature_history"
            ),
            "candidate_count": len(candidates),
            "exclusion_count": len(exclusions),
            "published_count": len(published),
        },
        "candidates": candidates,
        "exclusions": exclusions,
        "published": published,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    write_candidates_csv(args.output_csv, candidates)
    print(
        "event_cluster_paper_candidates "
        f"mode={mode} candidates={len(candidates)} exclusions={len(exclusions)} "
        f"published={len(published)} output={args.output_json}"
    )
    return 0


def event_receipt_rejection_reason(
    event: EventRecord,
    *,
    fetched_at: datetime,
) -> str | None:
    """Reject disclosures that were not observable before the intended entry."""

    if fetched_at < event.disclosed_at:
        return "fetched_before_disclosure"
    entry_cutoff = datetime.combine(
        date.fromisoformat(event.entry_date),
        ENTRY_CUTOFF_TIME_JST,
        tzinfo=TOKYO,
    ).astimezone(UTC)
    if fetched_at >= entry_cutoff:
        return "late_data_receipt"
    return None


def source_coverage_window_allows(signal_date: date, *, fetched_at: datetime) -> bool:
    """Require a full JST signal-date snapshot before the entry cutoff.

    A response fetched before the signal date has ended can be complete as an
    HTTP response while still omitting later disclosures.  The operational
    snapshot is therefore accepted only from the next JST calendar day and
    before 09:00 JST on the next TSE business day.
    """

    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    coverage_start = datetime.combine(
        signal_date + timedelta(days=1),
        time(0, 0),
        tzinfo=TOKYO,
    ).astimezone(UTC)
    entry_cutoff = datetime.combine(
        next_tse_business_date(signal_date),
        ENTRY_CUTOFF_TIME_JST,
        tzinfo=TOKYO,
    ).astimezone(UTC)
    return coverage_start <= fetched_at < entry_cutoff


def parse_aware_timestamp(value: object, *, field: str) -> datetime:
    parsed = try_parse_aware_timestamp(value)
    if parsed is None:
        raise SystemExit(f"{field} must be an ISO timestamp with a timezone offset")
    return parsed


def try_parse_aware_timestamp(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def validated_fetch_metadata(
    rows: list[dict[str, Any]],
    *,
    signal_date: date | None,
) -> tuple[datetime | None, bool]:
    """Return the latest completed exporter response for ``signal_date``.

    The completion marker is written after every response row.  Matching its
    row count against rows carrying the same receipt timestamp prevents an
    interrupted append, an empty legacy archive, or a stale zero-row artifact
    from being presented as a causally verified fetch.
    """

    if signal_date is None:
        return None, False
    target = signal_date.isoformat()
    latest_marker: dict[str, Any] | None = None
    for row in rows:
        if (
            row.get("_roboinvest_record_type") != FETCH_METADATA_RECORD_TYPE
            or str(row.get("_roboinvest_target_date", ""))[:10] != target
        ):
            continue
        latest_marker = row
    if latest_marker is None:
        return None, False

    fetched_at = try_parse_aware_timestamp(latest_marker.get("_roboinvest_fetched_at"))
    try:
        expected_count = int(latest_marker.get("_roboinvest_row_count"))
    except (TypeError, ValueError):
        return fetched_at, False
    if fetched_at is None or expected_count < 0:
        return fetched_at, False
    actual_count = 0
    for row in rows:
        if _financial_row_date(row) != signal_date:
            continue
        raw_fetched_at = row.get("_roboinvest_fetched_at")
        if raw_fetched_at in (None, ""):
            continue
        if try_parse_aware_timestamp(raw_fetched_at) == fetched_at:
            actual_count += 1
    return fetched_at, actual_count == expected_count


def rows_for_completed_fetch(
    rows: list[dict[str, Any]],
    *,
    signal_date: date,
    fetched_at: datetime | None,
) -> list[dict[str, Any]]:
    """Select only source rows that were observable by the completed fetch.

    The signal date uses exactly the latest completed response snapshot. Older
    rows may supply already-public previous-forecast/dividend features. A
    timestamped historical row known to have arrived only after this snapshot
    is excluded, as are all future-dated rows.
    """

    if fetched_at is None:
        raise ValueError("a completed fetch must have fetched_at")
    selected: list[dict[str, Any]] = []
    for row in rows:
        if row.get("_roboinvest_record_type") == FETCH_METADATA_RECORD_TYPE:
            selected.append(row)
            continue
        row_date = _financial_row_date(row)
        if row_date is None or row_date > signal_date:
            continue
        raw_fetched_at = row.get("_roboinvest_fetched_at")
        if raw_fetched_at in (None, ""):
            if row_date < signal_date:
                selected.append(row)
            continue
        row_fetched_at = try_parse_aware_timestamp(raw_fetched_at)
        if row_fetched_at is None:
            continue
        if (row_date == signal_date and row_fetched_at == fetched_at) or (
            row_date < signal_date and row_fetched_at <= fetched_at
        ):
            selected.append(row)
    return selected


def receipt_provenance_label(
    *,
    signal_date: date | None,
    financial_rows: list[dict[str, Any]],
    fetch_metadata_at: datetime | None,
    fetch_metadata_complete: bool,
    explicit_fetched_at: datetime | None,
) -> str:
    if signal_date is None:
        return "not_date_scoped"
    if fetch_metadata_complete:
        return "export_metadata"
    if fetch_metadata_at is not None:
        return "incomplete_export_metadata"
    if any(
        _financial_row_date(row) == signal_date
        and row.get("_roboinvest_fetched_at") not in (None, "")
        for row in financial_rows
    ):
        return "row_metadata_without_completion"
    if explicit_fetched_at is not None:
        return "explicit_cli_unverified"
    return "execution_time_fallback_unverified"


def _financial_row_date(row: dict[str, Any]) -> date | None:
    value = row.get("DisclosedDate") or row.get("DiscDate") or row.get("Date") or row.get("date")
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def feature_exclusion_row(
    event: EventRecord,
    *,
    reason: str,
    source_bar_date: str | None = None,
) -> dict[str, Any]:
    return {
        "cluster_id": event.event_cluster_id,
        "symbol": event.symbol,
        "signal_date": event.signal_date,
        "reason": reason,
        "event_ids": [event.event_id],
        "data_available_at": event.data_available_at.isoformat(),
        "source_received_at": max(event.disclosed_at, event.fetched_at).isoformat(),
        "source_bar_date": source_bar_date,
        "entry_date": event.entry_date,
    }


def count_exclusions(rows: list[dict[str, Any]], *, reason: str) -> int:
    return sum(row.get("reason") == reason for row in rows)


def detect_candidates(
    observations: list[ObservationRecord],
    *,
    symbol_names: dict[str, str] | None = None,
    source_received_by_event_id: dict[str, datetime] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbol_names = {} if symbol_names is None else symbol_names
    source_received_by_event_id = (
        {} if source_received_by_event_id is None else source_received_by_event_id
    )
    clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        clusters[obs.trade_group_id or obs.event_cluster_id or obs.observation_id].append(obs)

    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for cluster_id, items in sorted(clusters.items()):
        if cluster_earnings_dividend_value_guard_allows(items, per_threshold=PER_THRESHOLD):
            for representative in cluster_trade_representatives(items):
                candidates.append(
                    candidate_row(
                        cluster_id,
                        representative,
                        items,
                        symbol_name=symbol_names.get(representative.symbol, ""),
                        source_received_at=max(
                            source_received_by_event_id.get(
                                item.event_id,
                                item.data_available_at,
                            )
                            for item in items
                        ),
                    )
                )
            continue
        if cluster_earnings_dividend_increase_allows(items):
            exclusions.append(
                {
                    "cluster_id": cluster_id,
                    "symbol": items[0].symbol,
                    "signal_date": min(obs.signal_date for obs in items),
                    "reason": "forecast_per_value_guard",
                    "min_forecast_per": _min_forecast_per(items),
                    "event_ids": [obs.event_id for obs in items],
                }
            )
    return candidates, exclusions


def candidate_row(
    cluster_id: str,
    representative: ObservationRecord,
    items: list[ObservationRecord],
    *,
    symbol_name: str = "",
    source_received_at: datetime,
) -> dict[str, Any]:
    feature_data_complete = all(
        not (
            item.data_available_at >= daily_bar_available_at(date.fromisoformat(item.signal_date))
            and item.source_bar_date != item.signal_date
        )
        for item in items
    )
    return {
        "candidate_id": CANDIDATE_ID,
        "execution_candidate_id": f"{cluster_id}:{representative.observation_id}",
        "cluster_id": cluster_id,
        "observation_id": representative.observation_id,
        "event_id": representative.event_id,
        "event_ids": [obs.event_id for obs in items],
        "symbol": representative.symbol,
        "symbol_name": symbol_name,
        "signal_date": representative.signal_date,
        "entry_date": representative.entry_date,
        "feature_cutoff_at": representative.feature_cutoff_at.isoformat(),
        "data_available_at": representative.data_available_at.isoformat(),
        "source_received_at": source_received_at.isoformat(),
        "feature_data_complete": feature_data_complete,
        "valuation_reference_price": None
        if representative.valuation_price is None
        else str(representative.valuation_price),
        "valuation_reference_bar_date": representative.source_bar_date,
        "valuation_reference_available_at": None
        if representative.source_bar_available_at is None
        else representative.source_bar_available_at.isoformat(),
        "entry_price_status": "unresolved_until_fresh_market_observation",
        "catastrophic_stop_pct": str(CAT_STOP_PCT),
        "max_hold_days": MAX_HOLD_DAYS,
        "min_forecast_per": _min_forecast_per(items),
        "has_earnings_result": any(obs.event_type.value == "earnings_result" for obs in items),
        "has_dividend_increase": any(
            obs.event_type.value == "dividend_revision" and obs.event_subtype == "increase"
            for obs in items
        ),
        "publish_ready": False,
    }


def _min_forecast_per(items: list[ObservationRecord]) -> str | None:
    values = [
        Decimal(str(obs.valuation_features_v0.forecast_per.value))
        for obs in items
        if obs.valuation_features_v0.forecast_per.valid
        and obs.valuation_features_v0.forecast_per.value not in (None, "")
    ]
    return None if not values else str(min(values))


def write_candidates_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id",
        "execution_candidate_id",
        "cluster_id",
        "observation_id",
        "event_id",
        "symbol",
        "symbol_name",
        "signal_date",
        "entry_date",
        "feature_cutoff_at",
        "data_available_at",
        "source_received_at",
        "feature_data_complete",
        "valuation_reference_price",
        "valuation_reference_bar_date",
        "valuation_reference_available_at",
        "entry_price_status",
        "catastrophic_stop_pct",
        "max_hold_days",
        "min_forecast_per",
        "publish_ready",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(candidates)


if __name__ == "__main__":
    raise SystemExit(main())
