#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from event_research_common import (
    FEATURE_SCHEMA_VERSION,
    PURGE_TRADING_DAYS,
)
from trade_contracts.event_research import EventRecord, ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit event research dataset quality.")
    parser.add_argument("--financial-summary-jsonl", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--output", type=Path, default=Path("out/event-research/data-audit.json"))
    parser.add_argument("--max-trading-days", type=int, default=30)
    parser.add_argument("--random-seeds", type=int, default=30)
    args = parser.parse_args()

    raw_row_count = _count_jsonl(args.financial_summary_jsonl)
    manifest = _stream_split_manifest(args.observations)
    window_dates = _first_trading_dates_from_file(args.observations, args.max_trading_days)
    window_events = [
        EventRecord.model_validate(row)
        for row in _iter_jsonl(args.events)
        if date.fromisoformat(str(row["signal_date"])) in window_dates
    ]
    window_observations = [
        ObservationRecord.model_validate(row)
        for row in _iter_jsonl(args.observations)
        if date.fromisoformat(str(row["signal_date"])) in window_dates
    ]
    random_coverage = _random_coverage_from_ohlcv(
        args.ohlcv,
        window_observations,
        embargo_days=PURGE_TRADING_DAYS,
    )
    result = {
        "window": {
            "max_trading_days": args.max_trading_days,
            "start": min(window_dates).isoformat() if window_dates else None,
            "end": max(window_dates).isoformat() if window_dates else None,
            "trading_day_count": len(window_dates),
        },
        "raw_rows": raw_row_count,
        "window_event_count": len(window_events),
        "window_observation_count": len(window_observations),
        "event_type_counts": dict(Counter(event.event_type.value for event in window_events)),
        "unknown_doc_type_rate": _rate(not event.raw_document_type for event in window_events),
        "missing_disclosure_time_rate": _rate(
            event.disclosed_time in (None, "") for event in window_events
        ),
        "previous_forecast_reconstruction_coverage": _coverage(
            obs.previous_forecast_source_record_id is not None
            for obs in window_observations
            if obs.event_type.value == "forecast_revision"
        ),
        "valid_revision_coverage": _coverage(
            obs.fundamental_features_v0.profit_revision_pct.valid
            or obs.fundamental_features_v0.operating_profit_revision_pct.valid
            or obs.fundamental_features_v0.forecast_eps_revision_absolute.valid
            for obs in window_observations
            if obs.event_type.value == "forecast_revision"
        ),
        "event_cluster_counts": _cluster_counts(window_events),
        "duplicate_trade_count": len(window_observations)
        - len({obs.trade_group_id or obs.observation_id for obs in window_observations}),
        "matched_random_coverage": random_coverage,
        "point_in_time_violation_count": sum(
            1
            for obs in window_observations
            if obs.source_bar_available_at is not None
            and obs.source_bar_available_at > obs.feature_cutoff_at
        ),
        "split_overlap_violation_count": _stream_split_overlap_violations(
            args.observations,
            manifest,
        ),
        "observation_level_alpha_note": (
            "PF/DD in event-alpha reports are observation/trade-notional alpha metrics, "
            "not portfolio-level PF/DD."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        "event_data_audit "
        f"days={len(window_dates)} observations={len(window_observations)} output={args.output}"
    )
    return 0


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _first_trading_dates_from_file(path: Path, limit: int) -> set[date]:
    dates = sorted({date.fromisoformat(str(row["signal_date"])) for row in _iter_jsonl(path)})
    return set(dates[:limit])


def _random_coverage_from_ohlcv(
    path: Path,
    observations: list[ObservationRecord],
    *,
    embargo_days: int,
) -> dict[str, Any]:
    event_dates_by_symbol: dict[str, set[date]] = {}
    for obs in observations:
        event_dates_by_symbol.setdefault(obs.symbol, set()).add(_as_date(obs.signal_date))

    candidate_dates_by_symbol: dict[str, set[date]] = {
        symbol: set() for symbol in event_dates_by_symbol
    }
    with path.open("r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        symbol_idx = _csv_index(header, ["symbol", "Code"])
        date_idx = _csv_index(header, ["date", "Date"])
        if symbol_idx is None or date_idx is None:
            raise ValueError("OHLCV CSV must include symbol/date or Code/Date columns")
        for line in f:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split(",")
            if len(parts) <= max(symbol_idx, date_idx):
                continue
            symbol = parts[symbol_idx]
            if symbol not in candidate_dates_by_symbol:
                continue
            candidate_dates_by_symbol[symbol].add(date.fromisoformat(parts[date_idx][:10]))

    matched = 0
    fallback = 0
    pool_sizes: list[int] = []
    for symbol, event_dates in event_dates_by_symbol.items():
        all_dates = sorted(candidate_dates_by_symbol.get(symbol, set()))
        event_date_set = set(event_dates)
        for event_date in event_dates:
            candidates = [
                candidate_date
                for candidate_date in all_dates
                if candidate_date not in event_date_set
                and abs((candidate_date - event_date).days) > embargo_days
            ]
            pool_sizes.append(len(candidates))
            if candidates:
                matched += 1
            else:
                fallback += 1

    pool_sizes_sorted = sorted(pool_sizes)
    median = None
    if pool_sizes_sorted:
        median = pool_sizes_sorted[len(pool_sizes_sorted) // 2]
    return {
        "same_symbol_random_date": {
            "matched": matched,
            "unmatched": fallback,
            "fallback": fallback,
            "fallback_rate": None if matched + fallback == 0 else fallback / (matched + fallback),
            "candidate_pool_size_min": min(pool_sizes_sorted) if pool_sizes_sorted else 0,
            "candidate_pool_size_median": median,
            "candidate_pool_size_max": max(pool_sizes_sorted) if pool_sizes_sorted else 0,
        }
    }


def _csv_index(header: list[str], names: list[str]) -> int | None:
    for name in names:
        if name in header:
            return header.index(name)
    return None


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _stream_split_manifest(path: Path) -> dict[str, Any]:
    dates: set[date] = set()
    symbols: set[str] = set()
    count = 0
    digest = hashlib.sha256()
    for row in _iter_jsonl(path):
        dates.add(date.fromisoformat(str(row["signal_date"])))
        symbols.add(str(row["symbol"]))
        count += 1
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    ordered_dates = sorted(dates)
    if not ordered_dates:
        return {}
    train_end = ordered_dates[int(len(ordered_dates) * 0.60)]
    validation_start = _shift_trading_date(ordered_dates, train_end, PURGE_TRADING_DAYS)
    validation_end = ordered_dates[int(len(ordered_dates) * 0.80)]
    locked_oos_start = _shift_trading_date(ordered_dates, validation_end, PURGE_TRADING_DAYS)
    return {
        "train_start": ordered_dates[0].isoformat(),
        "train_end": train_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
        "locked_oos_start": locked_oos_start.isoformat(),
        "locked_oos_end": ordered_dates[-1].isoformat(),
        "purge_days": PURGE_TRADING_DAYS,
        "dataset_hash": digest.hexdigest(),
        "split_observation_count": count,
        "split_symbol_count": len(symbols),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def _shift_trading_date(dates: list[date], start: date, offset: int) -> date:
    idx = dates.index(start)
    return dates[min(idx + offset, len(dates) - 1)]


def _rate(items: Any) -> float | None:
    values = list(items)
    if not values:
        return None
    return sum(1 for item in values if item) / len(values)


def _coverage(items: Any) -> dict[str, Any]:
    values = list(items)
    covered = sum(1 for item in values if item)
    return {
        "covered": covered,
        "total": len(values),
        "coverage": None if not values else covered / len(values),
    }


def _cluster_counts(events: list[EventRecord]) -> dict[str, Any]:
    counts = Counter(event.event_cluster_id for event in events)
    return {
        "cluster_count": len(counts),
        "multi_event_cluster_count": sum(1 for value in counts.values() if value > 1),
        "max_cluster_size": max(counts.values()) if counts else 0,
    }


def _stream_split_overlap_violations(
    path: Path,
    manifest: dict[str, Any],
) -> int:
    if not manifest:
        return 0
    validation_start = date.fromisoformat(manifest["validation_start"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    violations = 0
    for row in _iter_jsonl(path):
        split = _observation_split_label(row, manifest)
        raw_exit = row.get("labels", {}).get("exit_date_20d")
        if raw_exit in (None, ""):
            continue
        exit_date = date.fromisoformat(str(raw_exit))
        if split == "train" and exit_date >= validation_start:
            violations += 1
        if split == "validation" and exit_date >= locked_oos_start:
            violations += 1
    return violations


def _observation_split_label(row: dict[str, Any], manifest: dict[str, Any]) -> str:
    signal_date = date.fromisoformat(str(row["signal_date"]))
    train_end = date.fromisoformat(manifest["train_end"])
    validation_start = date.fromisoformat(manifest["validation_start"])
    validation_end = date.fromisoformat(manifest["validation_end"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    raw_exit = row.get("labels", {}).get("exit_date_20d")
    exit_20d = None if raw_exit in (None, "") else date.fromisoformat(str(raw_exit))
    if signal_date <= train_end:
        if exit_20d is not None and exit_20d >= validation_start:
            return "purge_train_validation"
        return "train"
    if signal_date < validation_start:
        return "purge_train_validation"
    if signal_date <= validation_end:
        if exit_20d is not None and exit_20d >= locked_oos_start:
            return "purge_validation_locked_oos"
        return "validation"
    if signal_date < locked_oos_start:
        return "purge_validation_locked_oos"
    return "locked_oos"


if __name__ == "__main__":
    raise SystemExit(main())
