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
        description=(
            "Diagnose point-in-time forecast_revision rule components without changing "
            "registered strategy arms."
        )
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--ohlcv",
        type=Path,
        help="Optional daily OHLCV CSV for exit-arm-specific matched random baselines.",
    )
    parser.add_argument(
        "--random-baseline-rule",
        action="append",
        default=[],
        help="Rule name to evaluate with exit-arm-specific random baselines. May be repeated.",
    )
    parser.add_argument("--random-seeds", type=int, default=300)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Evaluation split. Default excludes locked OOS details.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
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
        obs for obs in split_observations if obs.event_type == EventType.FORECAST_REVISION
    ]
    rows = diagnostic_rows(observations)
    summary = summarize_rows(rows, min_trades=args.min_trades)
    exit_random_baselines = exit_specific_random_baselines(
        observations,
        ohlcv_path=args.ohlcv,
        rule_names=args.random_baseline_rule,
        seed_count=args.random_seeds,
    )
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
        "rule_summary": summary,
        "exit_random_baselines": exit_random_baselines,
        "rule_definitions": rule_definitions(),
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, rows)
    print(
        "forecast_revision_rule_diagnostics "
        f"split={args.split} observations={len(observations)} rows={len(rows)}"
    )
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
    summary: list[dict[str, Any]] = []
    for row in rows:
        trades = int(row.get("trade_count") or 0)
        if trades < min_trades:
            continue
        pf = row.get("profit_factor")
        summary.append(
            {
                "rule_name": row["rule_name"],
                "exit_arm": row["exit_arm"],
                "trade_count": trades,
                "net_pnl": row.get("net_pnl"),
                "profit_factor": pf,
                "max_drawdown": row.get("max_drawdown"),
                "hit_rate": row.get("hit_rate"),
                "average_return": row.get("average_return"),
            }
        )
    return sorted(
        summary,
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
        return {
            "enabled": False,
            "reason": "provide --ohlcv and --random-baseline-rule to compute this diagnostic",
        }
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
        pools_by_name, combined_observations, coverage = _baseline_index_pools(
            selected,
            random_date_observations=random_date_observations,
        )
        baselines = random_baselines_for_selection_by_exit(
            combined_observations,
            selected_indexes=list(range(len(selected))),
            seed_count=seed_count,
            pools_by_name=pools_by_name,
        )
        result["rules"][rule_name] = {
            "selected_observation_count": len(selected),
            "selected_symbol_count": len({obs.symbol for obs in selected}),
            "random_date_observation_count": len(random_date_observations),
            "coverage": coverage,
            "baselines_by_exit": baselines,
        }
    return result


def rules() -> dict[str, Rule]:
    return {
        "all_forecast_revision": lambda obs: True,
        "current_fundamental_rule": fundamental_rule_allows,
        "current_technical_veto": technical_veto_allows,
        "current_fundamental_plus_technical": lambda obs: (
            fundamental_rule_allows(obs) and technical_veto_allows(obs)
        ),
        "profit_revision_positive": lambda obs: _positive(obs, "profit_revision_pct"),
        "operating_profit_revision_positive": lambda obs: _positive(
            obs,
            "operating_profit_revision_pct",
        ),
        "sales_revision_positive": lambda obs: _positive(obs, "sales_revision_pct"),
        "forecast_eps_revision_positive_abs": lambda obs: _positive_abs(
            obs,
            "forecast_eps_revision_absolute",
        ),
        "profit_and_operating_profit_positive": lambda obs: (
            _positive(
                obs,
                "profit_revision_pct",
            )
            and _positive(obs, "operating_profit_revision_pct")
        ),
        "profit_operating_profit_sales_positive": lambda obs: (
            _positive(
                obs,
                "profit_revision_pct",
            )
            and _positive(obs, "operating_profit_revision_pct")
            and _positive(obs, "sales_revision_pct")
        ),
        "profit_or_operating_profit_positive_no_core_negative": lambda obs: (
            (
                _positive(obs, "profit_revision_pct")
                or _positive(obs, "operating_profit_revision_pct")
            )
            and not _negative(obs, "profit_revision_pct")
            and not _negative(obs, "operating_profit_revision_pct")
        ),
        "loss_to_profit": lambda obs: bool(obs.fundamental_features_v0.is_loss_to_profit.value),
        "positive_revision_without_eps_red_flags": lambda obs: (
            fundamental_rule_allows(obs) and not _eps_red_flags(obs)
        ),
        "cheap_positive_revision": lambda obs: (
            fundamental_rule_allows(obs) and _forecast_per_bucket(obs) == "cheap"
        ),
        "fair_or_cheap_positive_revision": lambda obs: (
            fundamental_rule_allows(obs) and _forecast_per_bucket(obs) in {"cheap", "fair"}
        ),
        "current_fundamental_plus_technical_no_eps_red_flags": lambda obs: (
            fundamental_rule_allows(obs) and technical_veto_allows(obs) and not _eps_red_flags(obs)
        ),
        "profit_op_positive_plus_technical": lambda obs: (
            _positive(obs, "profit_revision_pct")
            and _positive(obs, "operating_profit_revision_pct")
            and technical_veto_allows(obs)
        ),
        "profit_op_sales_positive_plus_technical": lambda obs: (
            _positive(
                obs,
                "profit_revision_pct",
            )
            and _positive(obs, "operating_profit_revision_pct")
            and _positive(obs, "sales_revision_pct")
            and technical_veto_allows(obs)
        ),
        "fair_or_cheap_positive_revision_plus_technical": lambda obs: (
            fundamental_rule_allows(obs)
            and _forecast_per_bucket(obs) in {"cheap", "fair"}
            and technical_veto_allows(obs)
        ),
        "broad_downtrend": lambda obs: _regime(obs) == "broad_downtrend",
        "transition_chop": lambda obs: _regime(obs) == "transition_chop",
        "narrow_or_broad_uptrend": lambda obs: (
            _regime(obs) in {"narrow_leadership", "broad_uptrend"}
        ),
        "technical_veto_failed_only": lambda obs: not technical_veto_allows(obs),
        "eps_red_flags": _eps_red_flags,
        "profit_revision_negative": lambda obs: _negative(obs, "profit_revision_pct"),
        "operating_profit_revision_negative": lambda obs: _negative(
            obs,
            "operating_profit_revision_pct",
        ),
    }


def rule_definitions() -> dict[str, str]:
    return {
        "all_forecast_revision": "All forecast_revision observations in the requested split.",
        "current_fundamental_rule": (
            "Existing preregistered rule: any positive profit, operating profit, or EPS absolute "
            "forecast revision; dividend increases for dividend events."
        ),
        "current_technical_veto": (
            "Existing preregistered technical veto: turnover >= 200M JPY, ATR 0.5%..8%, "
            "pre-event 20d return < 30%, and not broad_downtrend."
        ),
        "current_fundamental_plus_technical": "Existing rule-only candidate intersection.",
        "positive_revision_without_eps_red_flags": (
            "Existing fundamental rule with missing/negative/sign-change/near-zero EPS "
            "flags removed."
        ),
        "current_fundamental_plus_technical_no_eps_red_flags": (
            "Existing intersection with EPS red flags removed."
        ),
    }


def _positive(obs: ObservationRecord, field: str) -> bool:
    value = _feature_decimal(obs, field)
    return value is not None and value > 0


def _positive_abs(obs: ObservationRecord, field: str) -> bool:
    value = _feature_decimal(obs, field)
    return value is not None and value > 0


def _negative(obs: ObservationRecord, field: str) -> bool:
    value = _feature_decimal(obs, field)
    return value is not None and value < 0


def _feature_decimal(obs: ObservationRecord, field: str) -> Decimal | None:
    feature = getattr(obs.fundamental_features_v0, field)
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


def _eps_red_flags(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    return (
        features.missing_eps
        or features.negative_eps
        or features.sign_changed
        or features.previous_eps_near_zero
        or bool(features.is_profit_to_loss.value)
    )


def _regime(obs: ObservationRecord) -> str:
    tech = obs.technical_context_v0
    market_regime = getattr(tech.market_regime, "value", None)
    if market_regime not in (None, ""):
        return str(market_regime)
    symbol_regime = getattr(tech.symbol_regime, "value", None)
    return str(symbol_regime or "")


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
