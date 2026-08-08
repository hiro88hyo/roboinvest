#!/usr/bin/env python3
"""Screen the preregistered high-frequency event variants on development data."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from types import ModuleType
from typing import Any

from event_forward_evidence import file_sha256
from event_research_common import fundamental_rule_allows, technical_veto_allows
from trade_contracts.event_research import EventType, ObservationRecord

PROSPECTIVE_OOS_START = date(2026, 7, 21)
DEADLINE = date(2026, 9, 30)
CAPITAL = Decimal("2000000")
MAX_DRAWDOWN = CAPITAL * Decimal("0.10")
VARIANT_ORDER = (
    "broad_feature_time_fixed2",
    "broad_quality_priority_fixed2",
    "quality_tiers_0_2_fixed2",
)


def _portfolio_module() -> ModuleType:
    name = "simulate_event_portfolio_prospective_screen"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name("simulate-event-portfolio.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load portfolio simulator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _feature(obs: ObservationRecord, name: str) -> Decimal | None:
    feature = getattr(obs.fundamental_features_v0, name)
    if not feature.valid:
        return None
    return _decimal(feature.value)


def _valuation(obs: ObservationRecord, name: str) -> Decimal | None:
    feature = getattr(obs.valuation_features_v0, name)
    if not feature.valid:
        return None
    return _decimal(feature.value)


def _eps_red_flags(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    return (
        features.missing_eps
        or features.negative_eps
        or features.sign_changed
        or features.previous_eps_near_zero
        or bool(features.is_profit_to_loss.value)
    )


def _positive(obs: ObservationRecord, name: str) -> bool:
    value = _feature(obs, name)
    return value is not None and value > 0


def _negative(obs: ObservationRecord, name: str) -> bool:
    value = _feature(obs, name)
    return value is not None and value < 0


def _fair_value_quality(obs: ObservationRecord) -> bool:
    forecast_per = _valuation(obs, "forecast_per")
    return (
        obs.event_type == EventType.FORECAST_REVISION
        and fundamental_rule_allows(obs)
        and not _eps_red_flags(obs)
        and (forecast_per is None or (forecast_per > 0 and forecast_per <= Decimal("25")))
        and technical_veto_allows(obs)
    )


def _core_profit_quality(obs: ObservationRecord) -> bool:
    return (
        obs.event_type == EventType.FORECAST_REVISION
        and (
            _positive(obs, "profit_revision_pct") or _positive(obs, "operating_profit_revision_pct")
        )
        and not _negative(obs, "profit_revision_pct")
        and not _negative(obs, "operating_profit_revision_pct")
        and not _eps_red_flags(obs)
        and technical_veto_allows(obs)
    )


def quality_tier(obs: ObservationRecord) -> int:
    fair = _fair_value_quality(obs)
    core = _core_profit_quality(obs)
    if fair and core:
        return 0
    if fair or core:
        return 1
    dividend_yield = _valuation(obs, "forecast_dividend_yield")
    if (
        obs.event_type == EventType.DIVIDEND_REVISION
        and obs.event_subtype == "increase"
        and dividend_yield is not None
        and dividend_yield >= Decimal("0.03")
    ):
        return 2
    return 3


def _eligible(obs: ObservationRecord) -> bool:
    return bool(fundamental_rule_allows(obs) and technical_veto_allows(obs))


def load_eligible_groups(
    path: Path,
    *,
    prospective_start: date,
) -> tuple[dict[str, list[ObservationRecord]], dict[str, int]]:
    groups: dict[str, list[ObservationRecord]] = defaultdict(list)
    counts = {"raw": 0, "before_boundary": 0, "eligible": 0}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            counts["raw"] += 1
            raw = json.loads(line)
            if date.fromisoformat(str(raw["signal_date"])) >= prospective_start:
                continue
            exit_date = raw.get("labels", {}).get("exit_date_2d")
            if exit_date in (None, "") or date.fromisoformat(str(exit_date)) >= prospective_start:
                continue
            counts["before_boundary"] += 1
            obs = ObservationRecord.model_validate(raw)
            if not _eligible(obs):
                continue
            counts["eligible"] += 1
            key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
            groups[key].append(obs)
    return dict(groups), counts


def selected_for_variant(
    groups: dict[str, list[ObservationRecord]],
    *,
    variant: str,
) -> list[ObservationRecord]:
    selected: list[ObservationRecord] = []
    for items in groups.values():
        ordered = sorted(
            items,
            key=lambda obs: (
                quality_tier(obs) if variant != "broad_feature_time_fixed2" else 0,
                obs.feature_cutoff_at.isoformat(),
                obs.symbol,
                obs.event_id,
            ),
        )
        representative = ordered[0]
        if variant == "quality_tiers_0_2_fixed2" and quality_tier(representative) > 2:
            continue
        selected.append(representative)
    return selected


def portfolio_candidates(
    selected: list[ObservationRecord],
    *,
    variant: str,
) -> list[Any]:
    portfolio = _portfolio_module()
    spec = portfolio.CandidateSpec(candidate_id=variant, exit_horizon=2)
    candidates = []
    for obs in selected:
        candidate = portfolio.portfolio_candidate_from_observation(obs, spec=spec)
        if candidate is None:
            continue
        if variant != "broad_feature_time_fixed2":
            candidate = replace(
                candidate,
                sort_key=(
                    f"{quality_tier(obs):02d}:"
                    f"{obs.feature_cutoff_at.isoformat()}:{obs.symbol}:{obs.event_id}"
                ),
            )
        candidates.append(candidate)
    return candidates


def _result(candidates: list[Any], *, stress: bool = False) -> Any:
    portfolio = _portfolio_module()
    return portfolio.simulate_portfolio(
        candidates,
        params=portfolio.PortfolioParams(
            capital=CAPITAL,
            entry_additional_slippage_bps=Decimal("10") if stress else Decimal("0"),
            exit_additional_slippage_bps=Decimal("25") if stress else Decimal("0"),
        ),
        selection_order=(
            "feature_time_symbol"
            if not candidates or not str(candidates[0].sort_key)[:3].endswith(":")
            else "priority_feature_time_symbol"
        ),
        spec=portfolio.CandidateSpec(candidate_id="prospective_screen", exit_horizon=2),
    )


def _summary(result: Any) -> dict[str, Any]:
    row = asdict(result)
    row.pop("trades", None)
    row["max_drawdown_ratio"] = float(Decimal(str(result.max_drawdown)) / CAPITAL)
    return row


def _year_blocks(candidates: list[Any]) -> list[dict[str, Any]]:
    rows = []
    years = sorted({candidate.entry_date.year for candidate in candidates})
    for year in years:
        result = _result(
            [candidate for candidate in candidates if candidate.entry_date.year == year]
        )
        rows.append({"year": year, **_summary(result)})
    return rows


def _seasonal_windows(candidates: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for year in range(2021, 2026):
        start = date(year, 7, 21)
        end = date(year, 9, 30)
        result = _result(
            [
                candidate
                for candidate in candidates
                if start <= candidate.entry_date and candidate.exit_date <= end
            ]
        )
        rows.append({"year": year, **_summary(result)})
    return rows


def _passes(
    *,
    base: Any,
    stress: Any,
    years: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    random_result: dict[str, Any],
) -> tuple[bool, dict[str, bool]]:
    random_2m = random_result["by_capital"][str(CAPITAL)]
    coverage = random_result["coverage"]
    checks = {
        "opened_at_least_500": base.opened_trade_count >= 500,
        "profit_factor_above_1_3": base.profit_factor is not None and base.profit_factor > 1.3,
        "drawdown_below_10pct": Decimal(str(base.max_drawdown)) < MAX_DRAWDOWN,
        "stress_profit_factor_above_1_2": (
            stress.profit_factor is not None and stress.profit_factor > 1.2
        ),
        "stress_drawdown_below_10pct": Decimal(str(stress.max_drawdown)) < MAX_DRAWDOWN,
        "positive_year_ratio_at_least_75pct": (
            sum(float(row["total_pnl"]) > 0 for row in years) / len(years) >= 0.75
        ),
        "seasonal_median_opened_at_least_30": median(
            int(row["opened_trade_count"]) for row in windows
        )
        >= 30,
        "random_percentile_at_least_75pct": random_2m["selected_percentile"] >= 0.75,
        "random_coverage_complete": (coverage["unmatched"] == 0 and coverage["fallback"] == 0),
    }
    return all(checks.values()), checks


def screen_variant(
    *,
    variant: str,
    groups: dict[str, list[ObservationRecord]],
    ohlcv_rows: list[Any],
    random_seeds: int,
) -> dict[str, Any]:
    portfolio = _portfolio_module()
    selected = selected_for_variant(groups, variant=variant)
    candidates = portfolio_candidates(selected, variant=variant)
    base = _result(candidates)
    stress = _result(candidates, stress=True)
    years = _year_blocks(candidates)
    windows = _seasonal_windows(candidates)
    selection_order = (
        "feature_time_symbol"
        if variant == "broad_feature_time_fixed2"
        else "priority_feature_time_symbol"
    )
    spec = portfolio.CandidateSpec(candidate_id=variant, exit_horizon=2)
    random_result = portfolio.portfolio_random_baselines(
        candidates,
        event_observations=selected,
        ohlcv_rows=ohlcv_rows,
        params_by_capital=[portfolio.PortfolioParams(capital=CAPITAL)],
        seed_count=random_seeds,
        selection_order=selection_order,
        spec=spec,
    )
    passed, checks = _passes(
        base=base,
        stress=stress,
        years=years,
        windows=windows,
        random_result=random_result,
    )
    return {
        "variant": variant,
        "selected_trade_groups": len(selected),
        "quality_tier_counts": {
            str(tier): sum(quality_tier(obs) == tier for obs in selected) for tier in range(4)
        },
        "base": _summary(base),
        "stress_entry10_exit25": _summary(stress),
        "calendar_year_blocks": years,
        "historical_deadline_windows": windows,
        "historical_deadline_window_median_opened": median(
            int(row["opened_trade_count"]) for row in windows
        ),
        "random_baseline": random_result,
        "gate_checks": checks,
        "gate_passed": passed,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "variant",
        "selected_trade_groups",
        "opened",
        "net_pnl",
        "profit_factor",
        "max_drawdown",
        "stress_profit_factor",
        "stress_max_drawdown",
        "seasonal_median_opened",
        "random_percentile",
        "gate_passed",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            base = row["base"]
            stress = row["stress_entry10_exit25"]
            random_2m = row["random_baseline"]["by_capital"][str(CAPITAL)]
            writer.writerow(
                {
                    "variant": row["variant"],
                    "selected_trade_groups": row["selected_trade_groups"],
                    "opened": base["opened_trade_count"],
                    "net_pnl": base["total_pnl"],
                    "profit_factor": base["profit_factor"],
                    "max_drawdown": base["max_drawdown"],
                    "stress_profit_factor": stress["profit_factor"],
                    "stress_max_drawdown": stress["max_drawdown"],
                    "seasonal_median_opened": row["historical_deadline_window_median_opened"],
                    "random_percentile": random_2m["selected_percentile"],
                    "gate_passed": row["gate_passed"],
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--random-seeds", type=int, default=300)
    parser.add_argument(
        "--prospective-start", type=date.fromisoformat, default=PROSPECTIVE_OOS_START
    )
    args = parser.parse_args()
    if args.prospective_start != PROSPECTIVE_OOS_START:
        parser.error(f"--prospective-start is frozen at {PROSPECTIVE_OOS_START}")
    if args.random_seeds != 300:
        parser.error("--random-seeds is frozen at 300")

    groups, counts = load_eligible_groups(
        args.observations,
        prospective_start=args.prospective_start,
    )
    symbols = {obs.symbol for items in groups.values() for obs in items}
    portfolio = _portfolio_module()
    ohlcv_rows = portfolio.read_ohlcv_csv(
        args.ohlcv,
        symbols=symbols,
        end_date=args.prospective_start,
    )
    rows = [
        screen_variant(
            variant=variant,
            groups=groups,
            ohlcv_rows=ohlcv_rows,
            random_seeds=args.random_seeds,
        )
        for variant in VARIANT_ORDER
    ]
    passing = [row for row in rows if row["gate_passed"]]
    selected = (
        max(
            passing,
            key=lambda row: (
                float(row["stress_entry10_exit25"]["profit_factor"] or 0),
                -VARIANT_ORDER.index(row["variant"]),
            ),
        )["variant"]
        if passing
        else None
    )
    payload = {
        "schema_version": 1,
        "purpose": "contaminated_historical_development_for_future_prospective_oos",
        "prospective_oos_start": args.prospective_start.isoformat(),
        "deadline": DEADLINE.isoformat(),
        "observations_path": str(args.observations),
        "observations_sha256": file_sha256(args.observations),
        "ohlcv_path": str(args.ohlcv),
        "ohlcv_sha256": file_sha256(args.ohlcv),
        "input_counts": counts,
        "eligible_trade_groups": len(groups),
        "variants": rows,
        "decision": "SELECTED" if selected is not None else "NO_CANDIDATE",
        "selected_variant": selected,
        "paper_live_enabled": False,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    write_csv(args.output_csv, rows)
    print(
        "event_prospective_high_frequency_screen "
        f"groups={len(groups)} decision={payload['decision']} selected={selected}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
