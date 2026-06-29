#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    EXIT_ARMS_FOR_REPORT,
    _baseline_index_pools,
    build_random_date_observations,
    fundamental_rule_allows,
    metrics_for_observations,
    random_baselines_for_selection_by_exit,
    read_jsonl,
    read_ohlcv_csv,
    select_observations_for_split,
    technical_veto_allows,
)
from trade_contracts.event_research import EventType, ObservationRecord

Rule = Callable[[ObservationRecord], bool]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose point-in-time earnings_result rule-only components."
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path)
    parser.add_argument("--random-baseline-rule", action="append", default=[])
    parser.add_argument("--random-seeds", type=int, default=300)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="train",
        help="Default is train to avoid validation-first tuning.",
    )
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
    observations = [
        obs for obs in split_observations if obs.event_type == EventType.EARNINGS_RESULT
    ]
    rows = diagnostic_rows(observations)
    result = {
        "summary": {
            "requested_split": args.split,
            "selected_observation_count": len(observations),
            "selected_symbol_count": len({obs.symbol for obs in observations}),
            "min_trades": args.min_trades,
            "purpose": "diagnostic_only_not_registered_strategy",
        },
        "evaluation_split": split_info,
        "rows": rows,
        "rule_summary": summarize_rows(rows, min_trades=args.min_trades),
        "exit_random_baselines": exit_specific_random_baselines(
            observations,
            ohlcv_path=args.ohlcv,
            rule_names=args.random_baseline_rule,
            seed_count=args.random_seeds,
        ),
        "rule_definitions": rule_definitions(),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, rows)
    print(f"earnings_result_rule_diagnostics split={args.split} observations={len(observations)}")
    return 0


def diagnostic_rows(observations: list[ObservationRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule_name, rule in rules().items():
        selected = [obs for obs in observations if rule(obs)]
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
    observations: list[ObservationRecord],
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
    result: dict[str, Any] = {
        "enabled": True,
        "seed_count": seed_count,
        "ohlcv": str(ohlcv_path),
        "rules": {},
    }
    ohlcv_rows = read_ohlcv_csv(ohlcv_path)
    for rule_name in rule_names:
        selected = [obs for obs in observations if rule_map[rule_name](obs)]
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


def rules() -> dict[str, Rule]:
    return {
        "all_earnings_result": lambda obs: True,
        "current_fundamental_rule": fundamental_rule_allows,
        "current_technical_veto": technical_veto_allows,
        "current_fundamental_plus_technical": lambda obs: (
            fundamental_rule_allows(obs) and technical_veto_allows(obs)
        ),
        "eps_latest_positive": lambda obs: _positive_feature(obs, "eps_latest"),
        "forecast_eps_positive": lambda obs: _positive_feature(obs, "revised_forecast_eps"),
        "eps_growth_yoy_positive": lambda obs: _positive_feature(obs, "eps_growth_yoy"),
        "forecast_per_cheap": lambda obs: _forecast_per_bucket(obs) == "cheap",
        "forecast_per_fair_or_cheap": lambda obs: _forecast_per_bucket(obs) in {"cheap", "fair"},
        "trailing_per_fair_or_cheap": lambda obs: _trailing_per_fair_or_cheap(obs),
        "pbr_below_1_5": lambda obs: (
            _valuation_decimal(obs, "pbr") is not None
            and _valuation_decimal(obs, "pbr") <= Decimal("1.5")
        ),
        "forecast_eps_positive_plus_technical": lambda obs: (
            _positive_feature(
                obs,
                "revised_forecast_eps",
            )
            and technical_veto_allows(obs)
        ),
        "forecast_per_fair_or_cheap_plus_technical": lambda obs: (
            _forecast_per_bucket(obs) in {"cheap", "fair"} and technical_veto_allows(obs)
        ),
        "earnings_quality_value_plus_technical": lambda obs: (
            _positive_feature(
                obs,
                "revised_forecast_eps",
            )
            and _forecast_per_bucket(obs) in {"cheap", "fair"}
            and technical_veto_allows(obs)
            and not _eps_red_flags(obs)
        ),
        "earnings_quality_deep_value_plus_technical": lambda obs: (
            _positive_feature(
                obs,
                "revised_forecast_eps",
            )
            and _forecast_per_bucket(obs) == "cheap"
            and technical_veto_allows(obs)
            and not _eps_red_flags(obs)
        ),
    }


def rule_definitions() -> dict[str, str]:
    return {
        "earnings_quality_value_plus_technical": (
            "Positive revised forecast EPS, valid forecast PER <= 25, existing technical veto, "
            "and no EPS red flags."
        ),
        "earnings_quality_deep_value_plus_technical": (
            "Positive revised forecast EPS, valid forecast PER <= 15, existing technical veto, "
            "and no EPS red flags."
        ),
    }


def _positive_feature(obs: ObservationRecord, field: str) -> bool:
    value = _feature_decimal(obs, field)
    return value is not None and value > 0


def _feature_decimal(obs: ObservationRecord, field: str) -> Decimal | None:
    feature = getattr(obs.fundamental_features_v0, field)
    if not getattr(feature, "valid", False):
        return None
    return _as_decimal(feature.value)


def _valuation_decimal(obs: ObservationRecord, field: str) -> Decimal | None:
    feature = getattr(obs.valuation_features_v0, field)
    if not getattr(feature, "valid", False):
        return None
    return _as_decimal(feature.value)


def _forecast_per_bucket(obs: ObservationRecord) -> str:
    valuation = obs.valuation_features_v0
    value = _as_decimal(valuation.forecast_per.value)
    if not valuation.forecast_per_valid or value is None or value <= 0:
        return "invalid"
    if value <= Decimal("15"):
        return "cheap"
    if value <= Decimal("25"):
        return "fair"
    return "expensive"


def _trailing_per_fair_or_cheap(obs: ObservationRecord) -> bool:
    valuation = obs.valuation_features_v0
    value = _as_decimal(valuation.trailing_per.value)
    return bool(valuation.trailing_per_valid) and value is not None and Decimal("0") < value <= 25


def _eps_red_flags(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    return (
        features.missing_eps
        or features.negative_eps
        or features.sign_changed
        or features.previous_eps_near_zero
        or bool(features.is_profit_to_loss.value)
    )


def _as_decimal(value: object) -> Decimal | None:
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
