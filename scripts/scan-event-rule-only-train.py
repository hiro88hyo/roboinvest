#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from event_research_common import (
    EXIT_ARMS_FOR_REPORT,
    FEATURE_SCHEMA_VERSION,
    PURGE_TRADING_DAYS,
    _shift_trading_date,
    cluster_trade_representatives,
    fundamental_rule_allows,
    metrics_for_observations,
    read_jsonl,
    technical_veto_allows,
)
from trade_contracts.event_research import EventType, ExitArm, ObservationRecord

ObservationRule = Callable[[ObservationRecord], bool]
ClusterRule = Callable[[list[ObservationRecord]], bool]
DEFAULT_PORTFOLIO_CAPITALS = (Decimal("1000000"), Decimal("2000000"), Decimal("5000000"))
INSPECTED_FAMILY_BY_RULE = {
    "forecast_revision_quality_value_technical": {
        "family": "forecast_revision",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "forecast_revision_core_profit_quality_technical": {
        "family": "forecast_revision",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "dividend_increase_yield_2pct_technical": {
        "family": "dividend_revision",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "dividend_increase_yield_3pct_technical": {
        "family": "dividend_revision",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "earnings_quality_deep_value_technical": {
        "family": "earnings_deep_value",
        "status": "prior_inspected_locked_oos_uninspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "cluster_earnings_dividend_increase": {
        "family": "cluster_v0",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "cluster_earnings_dividend_value_guard": {
        "family": "cluster_v1",
        "status": "locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "cluster_earnings_dividend_value_technical_guard": {
        "family": "cluster_v1_related",
        "status": "family_locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
    "cluster_earnings_dividend_yield_2pct_guard": {
        "family": "cluster_v1_related",
        "status": "family_locked_oos_inspected",
        "registry": "docs/adr/0005-locked-oos-inspection-freeze.md",
    },
}


@dataclass(frozen=True, slots=True)
class RuleSpec:
    name: str
    scope: Literal["observation", "cluster"]
    description: str
    rule: ObservationRule | ClusterRule


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan pre-validation rule-only event hypotheses on the train split only. "
            "This is a diagnostic screen, not a paper/live promotion tool."
        )
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument(
        "--portfolio-capital",
        action="append",
        default=[],
        help="Portfolio capital to simulate. May be repeated. Defaults to 1M/2M/5M.",
    )
    parser.add_argument(
        "--ohlcv",
        type=Path,
        help="Daily OHLCV CSV to compute train-only portfolio same-symbol random-date percentiles.",
    )
    parser.add_argument("--portfolio-random-seeds", type=int, default=300)
    args = parser.parse_args()

    if args.min_trades < 1:
        parser.error("--min-trades must be >= 1")
    if args.portfolio_random_seeds < 1:
        parser.error("--portfolio-random-seeds must be >= 1")

    portfolio_capitals = (
        [Decimal(value) for value in args.portfolio_capital]
        if args.portfolio_capital
        else list(DEFAULT_PORTFOLIO_CAPITALS)
    )
    manifest = split_manifest_from_raw(args.observations)
    observations = load_train_observations(args.observations, manifest)
    clusters = clusters_by_key(observations)
    ohlcv_rows = _portfolio_module().read_ohlcv_csv(args.ohlcv) if args.ohlcv else None
    rows = scan_rows(
        observations,
        clusters,
        portfolio_capitals=portfolio_capitals,
        ohlcv_rows=ohlcv_rows,
        portfolio_random_seeds=args.portfolio_random_seeds,
    )
    result = {
        "summary": {
            "purpose": "train_only_rule_screen_not_registered_strategy",
            "selected_train_observations": len(observations),
            "selected_train_symbols": len({obs.symbol for obs in observations}),
            "train_cluster_count": len(clusters),
            "multi_event_train_cluster_count": sum(
                1 for items in clusters.values() if len(items) > 1
            ),
            "min_trades": args.min_trades,
            "portfolio_capitals": [float(value) for value in portfolio_capitals],
            "portfolio_random_baseline": "same_symbol_random_date"
            if ohlcv_rows is not None
            else None,
            "portfolio_random_seeds": args.portfolio_random_seeds
            if ohlcv_rows is not None
            else None,
            "warning": (
                "Use this only to choose one pre-registered validation hypothesis. "
                "Do not tune these rules after inspecting validation or locked OOS."
            ),
        },
        "split_manifest": manifest,
        "rows": rows,
        "rule_summary": summarize_rows(rows, min_trades=args.min_trades),
        "rule_definitions": {
            spec.name: {
                "scope": spec.scope,
                "description": spec.description,
            }
            for spec in rule_specs()
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    write_csv(args.output_csv, rows)
    print(
        "event_rule_only_train_scan "
        f"train_observations={len(observations)} clusters={len(clusters)} rows={len(rows)}"
    )
    return 0


def split_manifest_from_raw(path: Path) -> dict[str, Any]:
    dates: set[date] = set()
    symbols: set[str] = set()
    count = 0
    digest = hashlib.sha256()
    for row in read_jsonl(path):
        count += 1
        dates.add(date.fromisoformat(str(row["signal_date"])))
        symbols.add(str(row.get("symbol", "")))
        digest.update(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    if not dates:
        return {}
    ordered_dates = sorted(dates)
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
        "dataset_hash_algorithm": "jsonl_stream_sha256_v1",
        "split_observation_count": count,
        "split_symbol_count": len(symbols),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def load_train_observations(path: Path, manifest: dict[str, Any]) -> list[ObservationRecord]:
    observations: list[ObservationRecord] = []
    for row in read_jsonl(path):
        if raw_observation_split(row, manifest) == "train":
            observations.append(ObservationRecord.model_validate(row))
    return observations


def raw_observation_split(row: dict[str, Any], manifest: dict[str, Any]) -> str:
    signal_date = date.fromisoformat(str(row["signal_date"]))
    train_end = date.fromisoformat(manifest["train_end"])
    validation_start = date.fromisoformat(manifest["validation_start"])
    validation_end = date.fromisoformat(manifest["validation_end"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    exit_20d = raw_label_exit_date(row, 20)
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


def raw_label_exit_date(row: dict[str, Any], horizon: int) -> date | None:
    raw = row.get("labels", {}).get(f"exit_date_{horizon}d")
    if raw in (None, ""):
        return None
    return date.fromisoformat(str(raw))


def clusters_by_key(observations: list[ObservationRecord]) -> dict[str, list[ObservationRecord]]:
    clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        clusters[obs.trade_group_id or obs.event_cluster_id or obs.observation_id].append(obs)
    return dict(clusters)


def scan_rows(
    observations: list[ObservationRecord],
    clusters: dict[str, list[ObservationRecord]],
    *,
    portfolio_capitals: list[Decimal],
    ohlcv_rows: list[object] | None = None,
    portfolio_random_seeds: int = 300,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in rule_specs():
        selected = select_for_rule(spec, observations, clusters)
        for exit_arm in EXIT_ARMS_FOR_REPORT:
            inspected = inspected_family_marker(spec.name)
            rows.append(
                {
                    "rule_name": spec.name,
                    "scope": spec.scope,
                    "exit_arm": exit_arm.value,
                    **inspected,
                    **metrics_for_observations(
                        selected,
                        exit_arm=exit_arm,
                        include_bootstrap_ci=False,
                    ),
                    **portfolio_metrics_for_selection(
                        selected,
                        rule_name=spec.name,
                        exit_arm=exit_arm,
                        portfolio_capitals=portfolio_capitals,
                        ohlcv_rows=ohlcv_rows,
                        random_seeds=portfolio_random_seeds,
                    ),
                }
            )
    return rows


def select_for_rule(
    spec: RuleSpec,
    observations: list[ObservationRecord],
    clusters: dict[str, list[ObservationRecord]],
) -> list[ObservationRecord]:
    if spec.scope == "observation":
        rule = spec.rule
        return [obs for obs in observations if rule(obs)]  # type: ignore[misc]
    selected: list[ObservationRecord] = []
    cluster_rule = spec.rule
    for items in clusters.values():
        if cluster_rule(items):  # type: ignore[misc]
            selected.extend(cluster_trade_representatives(items))
    return selected


def rule_specs() -> list[RuleSpec]:
    return [
        RuleSpec(
            "event_plus_fundamental_plus_technical",
            "observation",
            "Existing fundamental rule and existing technical veto.",
            lambda obs: fundamental_rule_allows(obs) and technical_veto_allows(obs),
        ),
        RuleSpec(
            "forecast_revision_quality_value_technical",
            "observation",
            (
                "Forecast revision with positive fundamental revision, no EPS red flags, "
                "forecast PER missing or <= 25, and existing technical veto."
            ),
            lambda obs: (
                obs.event_type == EventType.FORECAST_REVISION
                and fundamental_rule_allows(obs)
                and not eps_red_flags(obs)
                and forecast_per_missing_or_lte(obs, Decimal("25"))
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "forecast_revision_core_profit_quality_technical",
            "observation",
            (
                "Forecast revision with positive profit or operating profit revision, "
                "no negative core profit revision, no EPS red flags, and existing technical veto."
            ),
            lambda obs: (
                obs.event_type == EventType.FORECAST_REVISION
                and (
                    positive_feature(obs, "profit_revision_pct")
                    or positive_feature(obs, "operating_profit_revision_pct")
                )
                and not negative_feature(obs, "profit_revision_pct")
                and not negative_feature(obs, "operating_profit_revision_pct")
                and not eps_red_flags(obs)
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "dividend_increase_yield_2pct_technical",
            "observation",
            "Dividend increase, forecast dividend yield >= 2%, and existing technical veto.",
            lambda obs: (
                obs.event_type == EventType.DIVIDEND_REVISION
                and obs.event_subtype == "increase"
                and dividend_yield(obs) is not None
                and dividend_yield(obs) >= Decimal("0.02")
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "dividend_increase_yield_3pct_technical",
            "observation",
            "Dividend increase, forecast dividend yield >= 3%, and existing technical veto.",
            lambda obs: (
                obs.event_type == EventType.DIVIDEND_REVISION
                and obs.event_subtype == "increase"
                and dividend_yield(obs) is not None
                and dividend_yield(obs) >= Decimal("0.03")
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "earnings_quality_value_technical",
            "observation",
            (
                "Earnings result with positive revised forecast EPS, no EPS red flags, "
                "forecast PER missing or <= 25, and existing technical veto."
            ),
            lambda obs: (
                obs.event_type == EventType.EARNINGS_RESULT
                and positive_feature(obs, "revised_forecast_eps")
                and not eps_red_flags(obs)
                and forecast_per_missing_or_lte(obs, Decimal("25"))
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "earnings_quality_deep_value_technical",
            "observation",
            (
                "Earnings result with positive revised forecast EPS, no EPS red flags, "
                "forecast PER missing or <= 15, and existing technical veto."
            ),
            lambda obs: (
                obs.event_type == EventType.EARNINGS_RESULT
                and positive_feature(obs, "revised_forecast_eps")
                and not eps_red_flags(obs)
                and forecast_per_missing_or_lte(obs, Decimal("15"))
                and technical_veto_allows(obs)
            ),
        ),
        RuleSpec(
            "cluster_earnings_dividend_increase",
            "cluster",
            "Same trade cluster contains earnings_result and dividend_revision increase.",
            lambda items: (
                has_type(items, EventType.EARNINGS_RESULT) and has_dividend_increase(items)
            ),
        ),
        RuleSpec(
            "cluster_earnings_dividend_value_guard",
            "cluster",
            (
                "Same trade cluster contains earnings_result and dividend_revision increase, "
                "and cluster forecast PER is missing or <= 15."
            ),
            lambda items: (
                has_type(items, EventType.EARNINGS_RESULT)
                and has_dividend_increase(items)
                and cluster_forecast_per_missing_or_lte(items, Decimal("15"))
            ),
        ),
        RuleSpec(
            "cluster_earnings_dividend_value_technical_guard",
            "cluster",
            (
                "Same trade cluster contains earnings_result and dividend_revision increase, "
                "cluster forecast PER is missing or <= 15, and a member passes technical veto."
            ),
            lambda items: (
                has_type(items, EventType.EARNINGS_RESULT)
                and has_dividend_increase(items)
                and cluster_forecast_per_missing_or_lte(items, Decimal("15"))
                and any(technical_veto_allows(obs) for obs in items)
            ),
        ),
        RuleSpec(
            "cluster_earnings_dividend_yield_2pct_guard",
            "cluster",
            (
                "Same trade cluster contains earnings_result and dividend_revision increase, "
                "and a member has forecast dividend yield >= 2%."
            ),
            lambda items: (
                has_type(items, EventType.EARNINGS_RESULT)
                and has_dividend_increase(items)
                and any(
                    (value := dividend_yield(obs)) is not None and value >= Decimal("0.02")
                    for obs in items
                )
            ),
        ),
        RuleSpec(
            "multi_event_fundamental_technical",
            "cluster",
            "Multi-event cluster with at least one fundamental pass and one technical pass.",
            lambda items: (
                len(items) > 1
                and any(fundamental_rule_allows(obs) for obs in items)
                and any(technical_veto_allows(obs) for obs in items)
            ),
        ),
    ]


def summarize_rows(rows: list[dict[str, Any]], *, min_trades: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        trades = int(row.get("trade_count") or 0)
        if trades < min_trades:
            continue
        out.append(
            {
                "rule_name": row["rule_name"],
                "scope": row["scope"],
                "exit_arm": row["exit_arm"],
                "inspected_family": row.get("inspected_family"),
                "inspected_family_status": row.get("inspected_family_status"),
                "trade_count": trades,
                "net_pnl": row.get("net_pnl"),
                "profit_factor": row.get("profit_factor"),
                "max_drawdown": row.get("max_drawdown"),
                "hit_rate": row.get("hit_rate"),
                "average_return": row.get("average_return"),
                "positive_block_ratio": row.get("block_stability", {}).get("positive_block_ratio"),
                "worst_block_pnl": row.get("block_stability", {}).get("worst_block_pnl"),
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


def inspected_family_marker(rule_name: str) -> dict[str, Any]:
    marker = INSPECTED_FAMILY_BY_RULE.get(rule_name)
    if marker is None:
        return {
            "inspected_family": None,
            "inspected_family_status": None,
            "inspected_family_registry": None,
        }
    return {
        "inspected_family": marker["family"],
        "inspected_family_status": marker["status"],
        "inspected_family_registry": marker["registry"],
    }


def portfolio_metrics_for_selection(
    selected: list[ObservationRecord],
    *,
    rule_name: str,
    exit_arm: ExitArm,
    portfolio_capitals: list[Decimal],
    ohlcv_rows: list[object] | None,
    random_seeds: int,
) -> dict[str, Any]:
    portfolio = _portfolio_module()
    portfolio_spec = portfolio.CandidateSpec(
        candidate_id=f"train_only:{rule_name}:{exit_arm.value}",
        exit_horizon=exit_horizon(exit_arm),
        catastrophic_stop="catastrophic_stop" in exit_arm.value,
    )
    candidates = [
        candidate
        for obs in selected
        if (
            candidate := portfolio.portfolio_candidate_from_observation(
                obs,
                spec=portfolio_spec,
            )
        )
        is not None
    ]
    params_by_capital = [
        portfolio.PortfolioParams(capital=capital) for capital in portfolio_capitals
    ]
    random_by_capital: dict[str, Any] = {}
    if ohlcv_rows is not None and candidates:
        random_result = portfolio.portfolio_random_baselines(
            candidates,
            event_observations=selected,
            ohlcv_rows=ohlcv_rows,
            params_by_capital=params_by_capital,
            seed_count=random_seeds,
            selection_order="feature_time_symbol",
            spec=portfolio_spec,
        )
        random_by_capital = random_result.get("by_capital", {})

    out: dict[str, Any] = {}
    for params in params_by_capital:
        result = portfolio.simulate_portfolio(
            candidates,
            params=params,
            selection_order="feature_time_symbol",
            spec=portfolio_spec,
        )
        capital_key = str(int(params.capital))
        random_summary = random_by_capital.get(str(params.capital), {})
        out.update(
            {
                f"portfolio_{capital_key}_opened": result.opened_trade_count,
                f"portfolio_{capital_key}_net_pnl": result.total_pnl,
                f"portfolio_{capital_key}_profit_factor": result.profit_factor,
                f"portfolio_{capital_key}_max_drawdown": result.max_drawdown,
                f"portfolio_{capital_key}_same_symbol_random_date_percentile": random_summary.get(
                    "selected_percentile"
                ),
            }
        )
    return out


def exit_horizon(exit_arm: ExitArm) -> int:
    if exit_arm in {ExitArm.FIXED_2D}:
        return 2
    if exit_arm in {ExitArm.FIXED_5D}:
        return 5
    if exit_arm in {ExitArm.FIXED_10D, ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP}:
        return 10
    if exit_arm in {ExitArm.FIXED_20D, ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP}:
        return 20
    raise ValueError(f"unsupported exit_arm: {exit_arm}")


def has_type(items: list[ObservationRecord], event_type: EventType) -> bool:
    return any(obs.event_type == event_type for obs in items)


def has_dividend_increase(items: list[ObservationRecord]) -> bool:
    return any(
        obs.event_type == EventType.DIVIDEND_REVISION and obs.event_subtype == "increase"
        for obs in items
    )


def cluster_forecast_per_missing_or_lte(
    items: list[ObservationRecord],
    threshold: Decimal,
) -> bool:
    values = [
        value
        for obs in items
        if (value := valuation_decimal(obs, "forecast_per")) is not None and value > 0
    ]
    return not values or min(values) <= threshold


def forecast_per_missing_or_lte(obs: ObservationRecord, threshold: Decimal) -> bool:
    value = valuation_decimal(obs, "forecast_per")
    return value is None or (value > 0 and value <= threshold)


def dividend_yield(obs: ObservationRecord) -> Decimal | None:
    return valuation_decimal(obs, "forecast_dividend_yield")


def positive_feature(obs: ObservationRecord, field: str) -> bool:
    value = fundamental_decimal(obs, field)
    return value is not None and value > 0


def negative_feature(obs: ObservationRecord, field: str) -> bool:
    value = fundamental_decimal(obs, field)
    return value is not None and value < 0


def fundamental_decimal(obs: ObservationRecord, field: str) -> Decimal | None:
    feature = getattr(obs.fundamental_features_v0, field)
    if not getattr(feature, "valid", False):
        return None
    return as_decimal(feature.value)


def valuation_decimal(obs: ObservationRecord, field: str) -> Decimal | None:
    feature = getattr(obs.valuation_features_v0, field)
    if not getattr(feature, "valid", False):
        return None
    return as_decimal(feature.value)


def eps_red_flags(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    return (
        features.missing_eps
        or features.negative_eps
        or features.sign_changed
        or features.previous_eps_near_zero
        or bool(features.is_profit_to_loss.value)
    )


def as_decimal(value: object) -> Decimal | None:
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
    base_fields = (
        "rule_name",
        "scope",
        "exit_arm",
        "inspected_family",
        "inspected_family_status",
        "inspected_family_registry",
        "event_count",
        "duplicate_trade_count",
        "trade_count",
        "net_pnl",
        "profit_factor",
        "max_drawdown",
        "average_return",
        "median_return",
        "hit_rate",
        "positive_month_ratio",
        "worst_month",
    )
    portfolio_fields = sorted({key for row in rows for key in row if key.startswith("portfolio_")})
    fields = (*base_fields, *portfolio_fields)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


_PORTFOLIO_MODULE: Any | None = None


def _portfolio_module() -> Any:
    global _PORTFOLIO_MODULE
    if _PORTFOLIO_MODULE is not None:
        return _PORTFOLIO_MODULE
    path = Path(__file__).with_name("simulate-event-portfolio.py")
    spec = importlib.util.spec_from_file_location("simulate_event_portfolio", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load portfolio simulator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _PORTFOLIO_MODULE = module
    return module


if __name__ == "__main__":
    raise SystemExit(main())
