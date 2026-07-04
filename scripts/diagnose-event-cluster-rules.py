#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    EXIT_ARMS_FOR_REPORT,
    _baseline_index_pools,
    build_random_date_observations,
    cluster_trade_representatives,
    fundamental_rule_allows,
    metrics_for_observations,
    random_baselines_for_selection_by_exit,
    read_jsonl,
    read_ohlcv_csv,
    select_observations_for_split,
    technical_veto_allows,
)
from trade_contracts.event_research import EventType, ObservationRecord

ClusterRule = Callable[[list[ObservationRecord]], bool]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose point-in-time multi-event cluster rule-only components."
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path)
    parser.add_argument("--random-baseline-rule", action="append", default=[])
    parser.add_argument("--random-seeds", type=int, default=300)
    parser.add_argument("--split", choices=EVALUATION_SPLITS, default="train")
    parser.add_argument("--include-locked-oos", action="store_true")
    parser.add_argument("--min-trades", type=int, default=30)
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.min_trades < 1:
        parser.error("--min-trades must be >= 1")
    if args.random_seeds < 1:
        parser.error("--random-seeds must be >= 1")

    all_observations = [
        ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)
    ]
    split_observations, split_info = select_observations_for_split(
        all_observations,
        split=args.split,
    )
    clusters = clusters_by_key(split_observations)
    rows = diagnostic_rows(clusters)
    result = {
        "summary": {
            "requested_split": args.split,
            "cluster_count": len(clusters),
            "multi_event_cluster_count": sum(1 for items in clusters.values() if len(items) > 1),
            "min_trades": args.min_trades,
            "purpose": "diagnostic_only_not_registered_strategy",
        },
        "evaluation_split": split_info,
        "rows": rows,
        "rule_summary": summarize_rows(rows, min_trades=args.min_trades),
        "exit_random_baselines": exit_specific_random_baselines(
            clusters,
            ohlcv_path=args.ohlcv,
            rule_names=args.random_baseline_rule,
            seed_count=args.random_seeds,
        ),
        "rule_definitions": rule_definitions(),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, rows)
    print(f"event_cluster_rule_diagnostics split={args.split} clusters={len(clusters)}")
    return 0


def clusters_by_key(observations: list[ObservationRecord]) -> dict[str, list[ObservationRecord]]:
    clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
        clusters[key].append(obs)
    return dict(clusters)


def diagnostic_rows(clusters: dict[str, list[ObservationRecord]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule_name, rule in rules().items():
        selected = representatives_for_rule(clusters, rule)
        for exit_arm in EXIT_ARMS_FOR_REPORT:
            rows.append(
                {
                    "rule_name": rule_name,
                    "exit_arm": exit_arm.value,
                    **metrics_for_observations(
                        selected,
                        exit_arm=exit_arm,
                        include_bootstrap_ci=False,
                    ),
                }
            )
    return rows


def representatives_for_rule(
    clusters: dict[str, list[ObservationRecord]],
    rule: ClusterRule,
) -> list[ObservationRecord]:
    selected: list[ObservationRecord] = []
    for items in clusters.values():
        if rule(items):
            selected.extend(cluster_trade_representatives(items))
    return selected


def summarize_rows(rows: list[dict[str, Any]], *, min_trades: int) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        trades = int(row.get("trade_count") or 0)
        if trades < min_trades:
            continue
        out.append(
            {
                "rule_name": row["rule_name"],
                "exit_arm": row["exit_arm"],
                "trade_count": trades,
                "net_pnl": row.get("net_pnl"),
                "profit_factor": row.get("profit_factor"),
                "max_drawdown": row.get("max_drawdown"),
                "hit_rate": row.get("hit_rate"),
                "average_return": row.get("average_return"),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            -float(row["profit_factor"] or 0),
            -float(row["net_pnl"] or 0),
            -int(row["trade_count"]),
        ),
    )


def exit_specific_random_baselines(
    clusters: dict[str, list[ObservationRecord]],
    *,
    ohlcv_path: Path | None,
    rule_names: list[str],
    seed_count: int,
) -> dict[str, Any]:
    if ohlcv_path is None or not rule_names:
        return {"enabled": False}
    rule_map = rules()
    unknown_rules = sorted(set(rule_names) - set(rule_map))
    if unknown_rules:
        raise SystemExit(f"unknown --random-baseline-rule values: {unknown_rules}")
    ohlcv_rows = read_ohlcv_csv(ohlcv_path)
    result: dict[str, Any] = {
        "enabled": True,
        "seed_count": seed_count,
        "ohlcv": str(ohlcv_path),
        "rules": {},
    }
    for rule_name in rule_names:
        selected = representatives_for_rule(clusters, rule_map[rule_name])
        random_date_observations = build_random_date_observations(
            ohlcv_rows=ohlcv_rows,
            symbols={obs.symbol for obs in selected},
        )
        pools_by_name, combined, coverage = _baseline_index_pools(
            selected,
            random_date_observations=random_date_observations,
        )
        result["rules"][rule_name] = {
            "selected_observation_count": len(selected),
            "selected_symbol_count": len({obs.symbol for obs in selected}),
            "coverage": coverage,
            "baselines_by_exit": random_baselines_for_selection_by_exit(
                combined,
                selected_indexes=list(range(len(selected))),
                seed_count=seed_count,
                pools_by_name=pools_by_name,
            ),
        }
    return result


def rules() -> dict[str, ClusterRule]:
    return {
        "multi_event_cluster": lambda items: len(items) > 1,
        "earnings_plus_forecast": lambda items: (
            _has(items, EventType.EARNINGS_RESULT) and _has(items, EventType.FORECAST_REVISION)
        ),
        "earnings_plus_dividend_increase": lambda items: (
            _has(items, EventType.EARNINGS_RESULT) and _has_dividend_increase(items)
        ),
        "earnings_plus_dividend_increase_plus_technical": lambda items: (
            _has(
                items,
                EventType.EARNINGS_RESULT,
            )
            and _has_dividend_increase(items)
            and any(technical_veto_allows(obs) for obs in items)
        ),
        "earnings_plus_dividend_increase_value_guard": lambda items: (
            _has(
                items,
                EventType.EARNINGS_RESULT,
            )
            and _has_dividend_increase(items)
            and _forecast_per_missing_or_lte(items, "15")
        ),
        "forecast_plus_dividend_increase": lambda items: (
            _has(items, EventType.FORECAST_REVISION) and _has_dividend_increase(items)
        ),
        "earnings_forecast_dividend_increase": lambda items: (
            _has(
                items,
                EventType.EARNINGS_RESULT,
            )
            and _has(items, EventType.FORECAST_REVISION)
            and _has_dividend_increase(items)
        ),
        "cluster_fundamental_plus_technical": lambda items: (
            any(fundamental_rule_allows(obs) for obs in items)
            and any(technical_veto_allows(obs) for obs in items)
        ),
        "multi_event_fundamental_plus_technical": lambda items: (
            len(items) > 1
            and any(fundamental_rule_allows(obs) for obs in items)
            and any(technical_veto_allows(obs) for obs in items)
        ),
    }


def rule_definitions() -> dict[str, str]:
    return {
        "earnings_plus_forecast": (
            "Same trade cluster contains earnings_result and forecast_revision."
        ),
        "earnings_plus_dividend_increase": (
            "Same trade cluster contains earnings_result and dividend_revision increase."
        ),
        "earnings_plus_dividend_increase_plus_technical": (
            "Same trade cluster contains earnings_result and dividend_revision increase, "
            "and at least one member passes the preregistered technical veto."
        ),
        "earnings_plus_dividend_increase_value_guard": (
            "Same trade cluster contains earnings_result and dividend_revision increase, "
            "and forecast PER is either unavailable point-in-time or <= 15."
        ),
        "forecast_plus_dividend_increase": (
            "Same trade cluster contains forecast_revision and dividend_revision increase."
        ),
        "multi_event_fundamental_plus_technical": (
            "Multi-event cluster with at least one fundamental-pass member and at least one "
            "technical-veto-pass member."
        ),
    }


def _has(items: list[ObservationRecord], event_type: EventType) -> bool:
    return any(obs.event_type == event_type for obs in items)


def _has_dividend_increase(items: list[ObservationRecord]) -> bool:
    return any(
        obs.event_type == EventType.DIVIDEND_REVISION and obs.event_subtype == "increase"
        for obs in items
    )


def _forecast_per_missing_or_lte(items: list[ObservationRecord], threshold: str) -> bool:
    values = [
        value
        for obs in items
        if (value := _feature_decimal(obs.valuation_features_v0.forecast_per.value)) is not None
    ]
    threshold_value = _feature_decimal(threshold)
    return threshold_value is not None and (not values or min(values) <= threshold_value)


def _feature_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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
