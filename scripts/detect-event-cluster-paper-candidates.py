#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from event_research_common import (
    CAT_STOP_PCT,
    build_events_from_financial_rows,
    build_observations,
    cluster_earnings_dividend_increase_allows,
    cluster_earnings_dividend_value_guard_allows,
    cluster_trade_representatives,
    read_jsonl,
    read_master_csv,
    read_ohlcv_csv,
)
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.event_research import ObservationRecord
from trade_contracts.pubsub_client import PubSubPublisher
from trade_contracts.signal import StrategySignal

CANDIDATE_ID = "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
PER_THRESHOLD = Decimal("15")
MAX_HOLD_DAYS = 20
PUBLISH_ENABLED_ENV = "EVENT_CLUSTER_PAPER_PUBLISH_ENABLED"
DEFAULT_PUBSUB_TOPIC_SIGNALS_A = "strategy-signals-a"
DEFAULT_SIGNAL_CONFIDENCE = 0.5


class PreflightError(RuntimeError):
    """Raised when paper publish safety checks fail before Pub/Sub publish."""


@dataclass(frozen=True, slots=True)
class PublishSettings:
    supabase_url: str
    supabase_secret_key: str
    pubsub_project_id: str
    pubsub_topic_signals: str = DEFAULT_PUBSUB_TOPIC_SIGNALS_A
    pubsub_emulator_host: str = ""
    confidence: float = DEFAULT_SIGNAL_CONFIDENCE


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
        help=(
            "Publish paper-only StrategySignal messages after dry-run detection. "
            f"Also requires {PUBLISH_ENABLED_ENV}=true and system_status.trade_mode=paper."
        ),
    )
    parser.add_argument(
        "--pubsub-topic-signals",
        default=(
            os.environ.get("PUBSUB_TOPIC_SIGNALS_A")
            or os.environ.get("PUBSUB_TOPIC_SIGNALS")
            or DEFAULT_PUBSUB_TOPIC_SIGNALS_A
        ),
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=float(os.environ.get("EVENT_CLUSTER_PAPER_SIGNAL_CONFIDENCE", "0.5")),
        help="StrategySignal confidence for published paper candidates.",
    )
    parser.add_argument(
        "--signal-date",
        type=date.fromisoformat,
        help="Restrict candidates/exclusions to this signal_date.",
    )
    parser.add_argument("--fetched-at", help="ISO timestamp used for event fetched_at metadata.")
    args = parser.parse_args()

    fetched_at = (
        datetime.fromisoformat(args.fetched_at) if args.fetched_at else datetime.now(tz=UTC)
    )
    financial_rows = read_jsonl(args.financial_summary_jsonl)
    ohlcv_rows = read_ohlcv_csv(args.ohlcv)
    observations = build_observations(
        build_events_from_financial_rows(
            financial_rows,
            ohlcv_rows=ohlcv_rows,
            fetched_at=fetched_at,
        ),
        ohlcv_rows=ohlcv_rows,
        master=read_master_csv(args.master),
    )
    if args.signal_date is not None:
        observations = [
            obs for obs in observations if date.fromisoformat(obs.signal_date) == args.signal_date
        ]

    candidates, exclusions = detect_candidates(observations)
    published: list[dict[str, Any]] = []
    publish_enabled = False
    if args.publish_paper:
        if not _env_flag_enabled(PUBLISH_ENABLED_ENV):
            raise SystemExit(
                f"--publish-paper requires {PUBLISH_ENABLED_ENV}=true; no signals published"
            )
        publish_enabled = True
        for candidate in candidates:
            candidate["publish_ready"] = True
        try:
            published = asyncio.run(
                publish_paper_candidates(
                    candidates,
                    settings=PublishSettings(
                        supabase_url=_required_env("SUPABASE_URL"),
                        supabase_secret_key=_required_env("SUPABASE_SECRET_KEY"),
                        pubsub_project_id=_required_env("PUBSUB_PROJECT_ID"),
                        pubsub_emulator_host=os.environ.get("PUBSUB_EMULATOR_HOST", ""),
                        pubsub_topic_signals=args.pubsub_topic_signals,
                        confidence=args.confidence,
                    ),
                )
            )
        except PreflightError as exc:
            raise SystemExit(
                f"paper publish preflight failed: {exc}; no signals published"
            ) from exc

    mode = "paper_publish" if publish_enabled else "dry_run"
    payload = {
        "candidate_id": CANDIDATE_ID,
        "mode": mode,
        "paper_live_enabled": False,
        "paper_publish_enabled": publish_enabled,
        "publish_enabled": publish_enabled,
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
            "observation_count": len(observations),
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


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required for --publish-paper; no signals published")
    return value


async def publish_paper_candidates(
    candidates: list[dict[str, Any]],
    *,
    settings: PublishSettings,
    now: datetime | None = None,
    supabase_transport: httpx.AsyncBaseTransport | None = None,
    pubsub_transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    trade_mode = await read_trade_mode(
        url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        transport=supabase_transport,
    )
    if trade_mode is not TradeMode.PAPER:
        raise PreflightError(f"paper publish requires trade_mode=paper, got {trade_mode.value}")
    published: list[dict[str, Any]] = []
    signal_created_at = now or datetime.now(UTC)
    signals = [
        strategy_signal_from_candidate(
            candidate,
            confidence=settings.confidence,
            created_at=signal_created_at,
        )
        for candidate in candidates
    ]
    await insert_strategy_logs(
        signals,
        url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        transport=supabase_transport,
    )
    async with PubSubPublisher(
        project_id=settings.pubsub_project_id,
        emulator_host=settings.pubsub_emulator_host,
        transport=pubsub_transport,
    ) as publisher:
        for signal in signals:
            message_id = await publisher.publish(
                settings.pubsub_topic_signals,
                data=signal.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": signal.symbol,
                    "source": signal.source.value,
                    "candidate_id": CANDIDATE_ID,
                    "mode": "paper",
                },
            )
            published.append(
                {
                    "message_id": message_id,
                    "signal_id": str(signal.signal_id),
                    "symbol": signal.symbol,
                    "topic": settings.pubsub_topic_signals,
                }
            )
    return published


async def insert_strategy_logs(
    signals: list[StrategySignal],
    *,
    url: str,
    secret_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    if not signals:
        return 0
    rows = [
        {
            "signal_id": str(signal.signal_id),
            "source": signal.source.value,
            "symbol": signal.symbol,
            "action": signal.action.value,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
            "created_at": signal.created_at.isoformat(),
        }
        for signal in signals
    ]
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        timeout=30.0,
        headers={
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        transport=transport,
    ) as client:
        resp = await client.post(
            "/rest/v1/strategy_logs",
            params={"on_conflict": "signal_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
    if resp.status_code >= 300:
        raise PreflightError(
            f"insert failed: table=strategy_logs status={resp.status_code} body={resp.text[:200]}"
        )
    return len(rows)


async def read_trade_mode(
    *,
    url: str,
    secret_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TradeMode:
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        timeout=30.0,
        headers={
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        transport=transport,
    ) as client:
        resp = await client.get(
            "/rest/v1/system_status",
            params={"select": "trade_mode", "id": "eq.1", "limit": "1"},
        )
    if resp.status_code >= 300:
        raise PreflightError(
            f"read failed: table=system_status status={resp.status_code} body={resp.text[:200]}"
        )
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise PreflightError("system_status row id=1 not found")
    try:
        return TradeMode(str(rows[0]["trade_mode"]).lower())
    except (KeyError, ValueError) as exc:
        raise PreflightError(f"invalid system_status.trade_mode row: {rows[0]!r}") from exc


def strategy_signal_from_candidate(
    candidate: dict[str, Any],
    *,
    confidence: float,
    created_at: datetime,
) -> StrategySignal:
    return StrategySignal(
        source=SignalSource.RULE,
        symbol=str(candidate["symbol"]),
        price=Decimal(str(candidate["entry_price_assumption"])),
        action=Action.BUY,
        confidence=confidence,
        reasoning=json.dumps(
            {
                "candidate_id": CANDIDATE_ID,
                "cluster_id": candidate["cluster_id"],
                "event_ids": candidate["event_ids"],
                "signal_date": candidate["signal_date"],
                "entry_date": candidate["entry_date"],
                "min_forecast_per": candidate["min_forecast_per"],
                "missing_forecast_per": candidate["min_forecast_per"] is None,
                "mode": "paper_observation",
            },
            ensure_ascii=False,
        ),
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal(str(candidate["stop_loss_price"])),
        max_hold_days=MAX_HOLD_DAYS,
        created_at=created_at,
    )


def detect_candidates(
    observations: list[ObservationRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        clusters[obs.trade_group_id or obs.event_cluster_id or obs.observation_id].append(obs)

    candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for cluster_id, items in sorted(clusters.items()):
        if cluster_earnings_dividend_value_guard_allows(items, per_threshold=PER_THRESHOLD):
            for representative in cluster_trade_representatives(items):
                candidates.append(candidate_row(cluster_id, representative, items))
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
) -> dict[str, Any]:
    entry_price = Decimal(str(representative.entry_price))
    return {
        "candidate_id": CANDIDATE_ID,
        "cluster_id": cluster_id,
        "observation_id": representative.observation_id,
        "event_id": representative.event_id,
        "event_ids": [obs.event_id for obs in items],
        "symbol": representative.symbol,
        "symbol_name": getattr(representative, "symbol_name", ""),
        "signal_date": representative.signal_date,
        "entry_date": representative.entry_date,
        "feature_cutoff_at": representative.feature_cutoff_at.isoformat(),
        "entry_price_assumption": str(representative.entry_price),
        "stop_loss_price": str(entry_price * (Decimal("1") + CAT_STOP_PCT)),
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
        "cluster_id",
        "observation_id",
        "event_id",
        "symbol",
        "symbol_name",
        "signal_date",
        "entry_date",
        "entry_price_assumption",
        "stop_loss_price",
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
