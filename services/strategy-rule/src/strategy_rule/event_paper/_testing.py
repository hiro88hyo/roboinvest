"""Deterministic builders shared by event-paper tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from trade_contracts.market import OrderBookSnapshot, PriceLevel

from .artifact import EVENT_STRATEGY_KEY


def make_event_candidate(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_id": EVENT_STRATEGY_KEY,
        "execution_candidate_id": "cluster-7203:obs-7203",
        "cluster_id": "cluster-7203",
        "observation_id": "obs-7203",
        "event_id": "event-earnings",
        "event_ids": ["event-earnings", "event-dividend"],
        "symbol": "7203",
        "symbol_name": "トヨタ自動車",
        "signal_date": "2026-01-20",
        "entry_date": "2026-01-21",
        "feature_cutoff_at": "2026-01-20T06:30:00+00:00",
        "data_available_at": "2026-01-20T06:30:00+00:00",
        "source_received_at": "2026-01-20T15:30:00+00:00",
        "feature_data_complete": True,
        "valuation_reference_price": "1000",
        "valuation_reference_bar_date": "2026-01-20",
        "valuation_reference_available_at": "2026-01-20T06:30:00+00:00",
        "entry_price_status": "unresolved_until_fresh_market_observation",
        "catastrophic_stop_pct": "-0.10",
        "max_hold_days": 20,
        "min_forecast_per": "8.4",
        "has_earnings_result": True,
        "has_dividend_increase": True,
        "publish_ready": False,
    }
    row.update(overrides)
    return row


def make_event_artifact_payload(**overrides: Any) -> dict[str, Any]:
    candidates = overrides.pop("candidates", [make_event_candidate()])
    exclusions = overrides.pop("exclusions", [])
    payload: dict[str, Any] = {
        "schema_version": 2,
        "strategy_key": EVENT_STRATEGY_KEY,
        "candidate_id": EVENT_STRATEGY_KEY,
        "mode": "dry_run",
        "paper_live_enabled": False,
        "paper_publish_enabled": False,
        "publish_enabled": False,
        "causality_verified": True,
        "causality": {
            "candidate_features_use_forward_bars": False,
            "candidate_artifact_contains_entry_price": False,
            "entry_date_source": "tse_business_calendar",
            "data_receipt_checked": True,
            "receipt_provenance": "export_metadata",
            "fetch_completion_verified": True,
            "source_coverage_window_verified": True,
            "paper_publish_disabled": True,
        },
        "signal_date": "2026-01-20",
        "fetched_at": "2026-01-20T15:30:00+00:00",
        "rule": {
            "cluster_contains": ["earnings_result", "dividend_revision:increase"],
            "forecast_per_threshold": "15",
            "missing_forecast_per": "allowed",
            "max_hold_days": 20,
            "catastrophic_stop_pct": "-0.10",
        },
        "summary": {
            "event_count": 2,
            "observation_count": 2,
            "late_data_receipt_count": 0,
            "fetched_before_disclosure_count": 0,
            "missing_signal_date_ohlcv_count": 0,
            "missing_feature_history_count": 0,
            "candidate_count": len(candidates),
            "exclusion_count": len(exclusions),
            "published_count": 0,
        },
        "candidates": candidates,
        "exclusions": exclusions,
        "published": [],
    }
    payload.update(overrides)
    return payload


def make_event_book(
    *,
    symbol: str = "7203",
    received_at: datetime = datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
    best_bid: str = "999",
    best_ask: str = "1000",
    bid_quantity: int = 200,
    ask_quantity: int = 300,
) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=received_at,
        received_at=received_at,
        bids=[PriceLevel(price=best_bid, quantity=bid_quantity)],
        asks=[PriceLevel(price=best_ask, quantity=ask_quantity)],
    )


TARGET_DATE = date(2026, 1, 21)
