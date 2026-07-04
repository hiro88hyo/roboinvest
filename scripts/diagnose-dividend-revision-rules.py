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
    parser = argparse.ArgumentParser(description="Diagnose dividend_revision rule-only components.")
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
    observations = [
        obs for obs in split_observations if obs.event_type == EventType.DIVIDEND_REVISION
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
    print(f"dividend_revision_rule_diagnostics split={args.split} observations={len(observations)}")
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
    ohlcv_rows = read_ohlcv_csv(ohlcv_path)
    result: dict[str, Any] = {
        "enabled": True,
        "seed_count": seed_count,
        "ohlcv": str(ohlcv_path),
        "rules": {},
    }
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
        "all_dividend_revision": lambda obs: True,
        "increase": _is_increase,
        "decrease": lambda obs: obs.event_subtype == "decrease",
        "invalid": lambda obs: obs.event_subtype == "invalid",
        "increase_plus_technical": lambda obs: _is_increase(obs) and technical_veto_allows(obs),
        "increase_yield_valid": lambda obs: _is_increase(obs) and _dividend_yield(obs) is not None,
        "increase_yield_2pct": lambda obs: (
            _is_increase(obs)
            and _dividend_yield(obs) is not None
            and _dividend_yield(obs) >= Decimal("0.02")
        ),
        "increase_yield_3pct": lambda obs: (
            _is_increase(obs)
            and _dividend_yield(obs) is not None
            and _dividend_yield(obs) >= Decimal("0.03")
        ),
        "increase_yield_2pct_plus_technical": lambda obs: (
            _is_increase(obs)
            and _dividend_yield(obs) is not None
            and _dividend_yield(obs) >= Decimal("0.02")
            and technical_veto_allows(obs)
        ),
        "increase_yield_3pct_plus_technical": lambda obs: (
            _is_increase(obs)
            and _dividend_yield(obs) is not None
            and _dividend_yield(obs) >= Decimal("0.03")
            and technical_veto_allows(obs)
        ),
        "increase_value_plus_technical": lambda obs: (
            _is_increase(obs) and _forecast_per_fair_or_cheap(obs) and technical_veto_allows(obs)
        ),
    }


def rule_definitions() -> dict[str, str]:
    return {
        "increase_yield_2pct_plus_technical": (
            "Dividend increase, point-in-time forecast dividend yield >= 2%, and existing "
            "technical veto passes."
        ),
        "increase_yield_3pct_plus_technical": (
            "Dividend increase, point-in-time forecast dividend yield >= 3%, and existing "
            "technical veto passes."
        ),
        "increase_value_plus_technical": (
            "Dividend increase, valid forecast PER <= 25, and existing technical veto passes."
        ),
    }


def _is_increase(obs: ObservationRecord) -> bool:
    return obs.event_subtype == "increase"


def _dividend_yield(obs: ObservationRecord) -> Decimal | None:
    valuation = obs.valuation_features_v0
    if not valuation.dividend_yield_valid:
        return None
    return _as_decimal(valuation.forecast_dividend_yield.value)


def _forecast_per_fair_or_cheap(obs: ObservationRecord) -> bool:
    valuation = obs.valuation_features_v0
    value = _as_decimal(valuation.forecast_per.value)
    return bool(valuation.forecast_per_valid) and value is not None and Decimal("0") < value <= 25


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
