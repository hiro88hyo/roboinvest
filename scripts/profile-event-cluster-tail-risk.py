#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    ExitArm,
    metrics_for_observations,
    read_jsonl,
    select_observations_for_split,
    technical_veto_allows,
)
from trade_contracts.event_research import EventType, ObservationRecord


@dataclass(frozen=True, slots=True)
class ClusterProfile:
    trade_group_id: str
    representative: ObservationRecord
    member_count: int
    any_technical_veto: bool
    max_avg_turnover: Decimal | None
    min_forecast_per: Decimal | None
    max_dividend_yield: Decimal | None
    max_atr_pct: Decimal | None
    min_return_20d: Decimal | None
    stop_reason_20d: str | None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile tail-risk buckets for the fixed earnings+dividend-increase event "
            "cluster candidate. Research diagnostic only; does not register a candidate."
        )
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--split", choices=EVALUATION_SPLITS, default="train")
    parser.add_argument("--include-locked-oos", action="store_true")
    parser.add_argument("--min-trades", type=int, default=1)
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.min_trades < 1:
        parser.error("--min-trades must be >= 1")

    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    split_observations, split_info = select_observations_for_split(observations, split=args.split)
    profiles = selected_cluster_profiles(split_observations)
    rows = bucket_rows(profiles, min_trades=args.min_trades)
    payload = {
        "candidate_id": "event_cluster_earnings_dividend_increase_fixed20_stop_v0_research",
        "research_only": True,
        "paper_live_enabled": False,
        "diagnostic": "train-side tail-risk bucket profile; not a registered candidate",
        "evaluation_split": split_info,
        "selected_trade_count": len(profiles),
        "rows": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, rows)
    print(f"event_cluster_tail_risk_profile split={args.split} trades={len(profiles)}")
    return 0


def selected_cluster_profiles(observations: list[ObservationRecord]) -> list[ClusterProfile]:
    clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
        clusters[key].append(obs)

    profiles: list[ClusterProfile] = []
    for key, items in clusters.items():
        if not _is_earnings_dividend_increase_cluster(items):
            continue
        representative = _representative(items)
        profiles.append(
            ClusterProfile(
                trade_group_id=key,
                representative=representative,
                member_count=len(items),
                any_technical_veto=any(technical_veto_allows(obs) for obs in items),
                max_avg_turnover=_max_feature(items, "technical", "avg_turnover_20d"),
                min_forecast_per=_min_feature(items, "valuation", "forecast_per"),
                max_dividend_yield=_max_feature(
                    items,
                    "valuation",
                    "forecast_dividend_yield",
                ),
                max_atr_pct=_max_feature(items, "technical", "atr_pct_14d"),
                min_return_20d=_min_feature(items, "technical", "return_20d"),
                stop_reason_20d=_stop_reason(representative),
            )
        )
    return profiles


def bucket_rows(profiles: list[ClusterProfile], *, min_trades: int) -> list[dict[str, Any]]:
    dimensions = {
        "technical_veto": lambda item: "pass" if item.any_technical_veto else "fail",
        "avg_turnover_20d": lambda item: _turnover_bucket(item.max_avg_turnover),
        "forecast_per": lambda item: _forecast_per_bucket(item.min_forecast_per),
        "dividend_yield": lambda item: _dividend_yield_bucket(item.max_dividend_yield),
        "atr_pct_14d": lambda item: _atr_bucket(item.max_atr_pct),
        "return_20d": lambda item: _return_20d_bucket(item.min_return_20d),
        "stop_reason_20d": lambda item: item.stop_reason_20d or "missing",
    }
    rows: list[dict[str, Any]] = []
    for dimension, bucket_fn in dimensions.items():
        grouped: dict[str, list[ObservationRecord]] = defaultdict(list)
        member_counts: dict[str, int] = defaultdict(int)
        for profile in profiles:
            bucket = bucket_fn(profile)
            grouped[bucket].append(profile.representative)
            member_counts[bucket] += profile.member_count
        for bucket, observations in sorted(grouped.items()):
            if len(observations) < min_trades:
                continue
            metrics = metrics_for_observations(
                observations,
                exit_arm=ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP,
                include_bootstrap_ci=False,
            )
            rows.append(
                {
                    "dimension": dimension,
                    "bucket": bucket,
                    "member_event_count": member_counts[bucket],
                    "catastrophic_stop_count": sum(
                        1
                        for obs in observations
                        if obs.labels.get("catastrophic_stop_exit_reason_20d")
                        == "catastrophic_stop"
                    ),
                    "catastrophic_stop_rate": sum(
                        1
                        for obs in observations
                        if obs.labels.get("catastrophic_stop_exit_reason_20d")
                        == "catastrophic_stop"
                    )
                    / len(observations),
                    **{
                        key: metrics[key]
                        for key in (
                            "trade_count",
                            "net_pnl",
                            "profit_factor",
                            "max_drawdown",
                            "average_return",
                            "median_return",
                            "hit_rate",
                        )
                    },
                }
            )
    return rows


def _is_earnings_dividend_increase_cluster(items: list[ObservationRecord]) -> bool:
    has_earnings = any(obs.event_type == EventType.EARNINGS_RESULT for obs in items)
    has_dividend_increase = any(
        obs.event_type == EventType.DIVIDEND_REVISION and obs.event_subtype == "increase"
        for obs in items
    )
    return has_earnings and has_dividend_increase


def _representative(items: list[ObservationRecord]) -> ObservationRecord:
    return sorted(
        items,
        key=lambda item: (
            date.fromisoformat(item.entry_date),
            item.feature_cutoff_at.isoformat(),
            item.symbol,
            item.event_id,
        ),
    )[0]


def _max_feature(
    observations: list[ObservationRecord],
    feature_group: str,
    feature_name: str,
) -> Decimal | None:
    values = [
        value
        for obs in observations
        if (value := _feature_value(obs, feature_group, feature_name)) is not None
    ]
    return max(values) if values else None


def _min_feature(
    observations: list[ObservationRecord],
    feature_group: str,
    feature_name: str,
) -> Decimal | None:
    values = [
        value
        for obs in observations
        if (value := _feature_value(obs, feature_group, feature_name)) is not None
    ]
    return min(values) if values else None


def _feature_value(
    obs: ObservationRecord,
    feature_group: str,
    feature_name: str,
) -> Decimal | None:
    source = obs.technical_context_v0 if feature_group == "technical" else obs.valuation_features_v0
    feature = getattr(source, feature_name)
    value = getattr(feature, "value", None)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _stop_reason(obs: ObservationRecord) -> str | None:
    value = obs.labels.get("catastrophic_stop_exit_reason_20d")
    return None if value in (None, "") else str(value)


def _turnover_bucket(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value < Decimal("20000000"):
        return "lt_20m"
    if value < Decimal("100000000"):
        return "20m_100m"
    if value < Decimal("200000000"):
        return "100m_200m"
    return "gte_200m"


def _forecast_per_bucket(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value <= Decimal("15"):
        return "lte_15"
    if value <= Decimal("25"):
        return "15_25"
    if value <= Decimal("40"):
        return "25_40"
    return "gt_40"


def _dividend_yield_bucket(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value < Decimal("0.01"):
        return "lt_1pct"
    if value < Decimal("0.03"):
        return "1_3pct"
    if value <= Decimal("0.08"):
        return "3_8pct"
    return "gt_8pct"


def _atr_bucket(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value < Decimal("0.01"):
        return "lt_1pct"
    if value < Decimal("0.03"):
        return "1_3pct"
    if value < Decimal("0.05"):
        return "3_5pct"
    if value <= Decimal("0.08"):
        return "5_8pct"
    return "gt_8pct"


def _return_20d_bucket(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    if value < Decimal("-0.10"):
        return "lt_neg10pct"
    if value < Decimal("0"):
        return "neg10_0pct"
    if value <= Decimal("0.10"):
        return "0_10pct"
    return "gt_10pct"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
