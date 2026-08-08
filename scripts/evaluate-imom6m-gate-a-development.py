#!/usr/bin/env python3
"""Evaluate the single authorized development Gate A for IMOM6M V0.

This executable is deliberately limited to the source-structure diagnostic. It
cannot read post-development price outcomes or calculate Gate B trades, PnL,
profit factor, or drawdown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any

import polars as pl

CANDIDATE_ID = "imom6m_top5_fixed20_v0_research"
CONFIG_STATUS = "PREREGISTERED_DESIGN_NO_COMPUTE_AUTHORITY"
AUTHORIZATION_ID = "imom6m_top5_fixed20_v0_gate_a_development_once"
FEATURE_SCHEMA_VERSION = "imom6m_no_skip_feature_cohort_v1"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
RESULT_SCHEMA_VERSION = "imom6m_gate_a_development_result_v1"
FEATURE_MANIFEST_FILENAME = "feature-manifest.json"
FEATURE_FILENAME = "feature-cohort.parquet"
NORMALIZED_MANIFEST_FILENAME = "normalized-manifest.json"
RESULT_FILENAME = "gate-a-result.json"
RUN_MANIFEST_FILENAME = "run-manifest.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_FEATURE_COLUMN_PARTS = (
    "next_month_return",
    "forward_return",
    "future_return",
    "entry_price",
    "exit_price",
    "stop_result",
    "trade_pnl",
    "profit_factor",
    "drawdown",
    "rank_ic",
)


class GateAEvaluationError(ValueError):
    """Raised when an authorization, input, or Gate A invariant is violated."""


def main() -> int:
    args = build_parser().parse_args()
    run_gate_a(
        normalized_dir=args.normalized_dir,
        feature_dir=args.feature_dir,
        config_path=args.config,
        authorization_path=args.authorization,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--feature-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def run_gate_a(
    *,
    normalized_dir: Path,
    feature_dir: Path,
    config_path: Path,
    authorization_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_config(config_path)
    feature_manifest_path = feature_dir / FEATURE_MANIFEST_FILENAME
    normalized_manifest_path = normalized_dir / NORMALIZED_MANIFEST_FILENAME
    authorization = load_and_verify_authorization(
        authorization_path,
        config_path=config_path,
        feature_manifest_path=feature_manifest_path,
        normalized_manifest_path=normalized_manifest_path,
        output_dir=output_dir,
    )
    feature_manifest = verify_feature_artifact(
        feature_dir=feature_dir,
        feature_manifest_path=feature_manifest_path,
        config_path=config_path,
        normalized_manifest_path=normalized_manifest_path,
    )
    development = required_mapping(required_mapping(config, "splits"), "development")
    split_start = date.fromisoformat(required_text(development, "signal_date_start"))
    split_end = date.fromisoformat(required_text(development, "signal_date_end"))
    normalized_manifest, price_paths = verify_normalized_archive(
        normalized_dir=normalized_dir,
        normalized_manifest_path=normalized_manifest_path,
        split_start=split_start,
        split_end=split_end,
    )

    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    features, calendar = load_development_features(
        feature_dir / FEATURE_FILENAME,
        split_start=split_start,
        split_end=split_end,
    )
    prices = load_development_prices(
        price_paths,
        split_start=split_start,
        split_end=split_end,
    )
    gate = required_mapping(config, "gate_a_source_structure_diagnostic")
    portfolio = required_mapping(gate, "portfolio")
    monthly = evaluate_months(
        features=features,
        prices=prices,
        calendar=calendar,
        split_start=split_start,
        split_end=split_end,
        minimum_eligible=int(portfolio["eligible_cross_section_minimum"]),
    )
    metrics, removed_month = aggregate_complete_months(monthly)
    gates = evaluate_gates(
        metrics=metrics,
        complete_month_count=sum(bool(row["complete"]) for row in monthly),
        pass_contract=required_mapping(gate, "pass_requires_all"),
    )
    decision = (
        "GATE_A_PASS_GATE_B_REQUIRES_SEPARATE_AUTHORIZATION"
        if gates["all_passed"]
        else "GATE_A_FAIL_CANDIDATE_FROZEN_GATE_B_AND_LATER_SPLITS_PROHIBITED"
    )
    result = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "evidence_class": config["evidence_class"],
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "split": "development",
        "split_start": split_start.isoformat(),
        "split_end": split_end.isoformat(),
        "feature_computed_previously": True,
        "development_next_month_returns_computed": True,
        "gate_a_computed": True,
        "gate_b_computed": False,
        "trades_profit_factor_or_drawdown_computed": False,
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "attempted_formation_month_count": len(monthly),
        "complete_month_count": sum(bool(row["complete"]) for row in monthly),
        "incomplete_month_count": sum(not bool(row["complete"]) for row in monthly),
        "largest_spread_month_removed": removed_month,
        "metrics": metrics,
        "monthly_diagnostics": monthly,
        "gates": gates,
        "decision": decision,
    }

    temporary_dir.mkdir(parents=True)
    result_path = temporary_dir / RESULT_FILENAME
    write_json(result_path, result)
    manifest = build_run_manifest(
        result_path=result_path,
        authorization_path=authorization_path,
        authorization=authorization,
        config_path=config_path,
        feature_manifest_path=feature_manifest_path,
        feature_manifest=feature_manifest,
        normalized_manifest_path=normalized_manifest_path,
        normalized_manifest=normalized_manifest,
        result=result,
    )
    write_json(temporary_dir / RUN_MANIFEST_FILENAME, manifest)
    temporary_dir.rename(output_dir)
    return result


def load_config(path: Path) -> dict[str, Any]:
    config = load_json_object(path, label="IMOM research config")
    if config.get("candidate_id") != CANDIDATE_ID or config.get("status") != CONFIG_STATUS:
        raise GateAEvaluationError("unexpected candidate or preregistration status")
    if config.get("evidence_class") != "PAPER_INSPIRED_IMPLEMENTABLE_ADAPTATION":
        raise GateAEvaluationError("evidence boundary drifted")
    feature = required_mapping(config, "feature")
    if (
        feature.get("feature_id") != "IMOM6M_NO_SKIP_V0"
        or feature.get("lookback_months") != 6
        or feature.get("skip_most_recent_month") is not False
        or feature.get("direction") != "HIGHER_IS_BETTER"
    ):
        raise GateAEvaluationError("feature definition drifted")
    cycle = required_mapping(config, "research_cycle")
    if (
        cycle.get("candidate_number") != 2
        or cycle.get("maximum_candidates") != 2
        or cycle.get("stop_after_this_candidate") is not True
    ):
        raise GateAEvaluationError("trial-limit contract drifted")
    gate = required_mapping(config, "gate_a_source_structure_diagnostic")
    portfolio = required_mapping(gate, "portfolio")
    if (
        gate.get("gate_id") != "IMOM6M_SOURCE_STRUCTURE_DEVELOPMENT_V0"
        or gate.get("split") != "development"
        or portfolio.get("eligible_cross_section_minimum") != 100
        or portfolio.get("weighting") != "EQUAL_WEIGHT_WITHIN_DECILE"
        or portfolio.get("cost") != "NONE"
        or portfolio.get("minimum_boundary_complete_months") != 24
    ):
        raise GateAEvaluationError("Gate A portfolio contract drifted")
    thresholds = required_mapping(gate, "pass_requires_all")
    expected_thresholds: dict[str, int | float] = {
        "minimum_boundary_complete_months": 24,
        "mean_decile_10_return_exclusive": 0.0,
        "mean_decile_10_minus_decile_1_exclusive": 0.0,
        "mean_monthly_rank_ic_exclusive": 0.0,
        "mean_spread_first_half_exclusive": 0.0,
        "mean_spread_second_half_exclusive": 0.0,
        "mean_spread_after_largest_month_removed_exclusive": 0.0,
    }
    for key, expected in expected_thresholds.items():
        if thresholds.get(key) != expected:
            raise GateAEvaluationError(f"Gate A threshold drifted: {key}")
    authority = required_mapping(config, "authority_boundary")
    if any(
        authority.get(key) is not False
        for key in (
            "implementation_authorized",
            "feature_computation_authorized",
            "development_outcome_computation_authorized",
            "validation_outcome_computation_authorized",
            "locked_oos_outcome_computation_authorized",
        )
    ):
        raise GateAEvaluationError("base config authority boundary must remain closed")
    decision = required_mapping(config, "decision_contract")
    if (
        decision.get("counts_as_2026_09_30_kill_switch_evidence") is not False
        or decision.get("paper_or_live_activation_authorized") is not False
    ):
        raise GateAEvaluationError("research-only decision boundary is open")
    return config


def load_and_verify_authorization(
    path: Path,
    *,
    config_path: Path,
    feature_manifest_path: Path,
    normalized_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    value = load_json_object(path, label="Gate A authorization")
    if value.get("authorization_id") != AUTHORIZATION_ID:
        raise GateAEvaluationError("unexpected Gate A authorization")
    if value.get("candidate_id") != CANDIDATE_ID:
        raise GateAEvaluationError("authorization candidate mismatch")
    if (
        value.get("research_only") is not True
        or value.get("paper_live_enabled") is not False
        or value.get("counts_as_2026_09_30_kill_switch_evidence") is not False
    ):
        raise GateAEvaluationError("authorization research boundary is open")
    scope = required_mapping(value, "scope")
    expected_scope = {
        "implement_gate_a_evaluator": True,
        "run_synthetic_gate_a_tests": True,
        "compute_development_next_month_returns_for_gate_a_once": True,
        "compute_gate_a_once": True,
        "inspect_development_gate_a_outcomes": True,
        "implement_or_compute_gate_b": False,
        "compute_trades_profit_factor_or_drawdown": False,
        "inspect_validation_outcomes": False,
        "inspect_locked_oos_outcomes": False,
        "modify_paper_or_live": False,
    }
    for key, expected in expected_scope.items():
        if scope.get(key) is not expected:
            raise GateAEvaluationError(f"Gate A authorization scope drifted: {key}")
    expected_output = scope.get("expected_output_dir")
    if (
        not isinstance(expected_output, str)
        or output_dir.resolve() != Path(expected_output).resolve()
    ):
        raise GateAEvaluationError("output path differs from authorization")
    contract = required_mapping(value, "preexecution_implementation_contract")
    if (
        contract.get("minimum_complete_months") != 24
        or contract.get("individual_symbol_outcomes_persisted") is not False
    ):
        raise GateAEvaluationError("preexecution implementation contract drifted")
    inputs = required_mapping(value, "bound_inputs")
    actual_inputs = {
        "config": config_path.resolve(),
        "feature_manifest": feature_manifest_path.resolve(),
        "normalized_manifest": normalized_manifest_path.resolve(),
    }
    for label in (
        "config",
        "phase1_completion",
        "feature_manifest",
        "normalized_manifest",
        "trial_registry",
    ):
        record = required_mapping(inputs, label)
        bound_path = bound_repository_path(record, label=label)
        if label in actual_inputs and bound_path != actual_inputs[label]:
            raise GateAEvaluationError(f"authorization/{label} path mismatch")
        if not bound_path.is_file():
            raise GateAEvaluationError(f"authorization/{label} is missing")
        if record.get("sha256") != file_sha256(bound_path):
            raise GateAEvaluationError(f"authorization/{label} hash mismatch")
    return value


def verify_feature_artifact(
    *,
    feature_dir: Path,
    feature_manifest_path: Path,
    config_path: Path,
    normalized_manifest_path: Path,
) -> dict[str, Any]:
    manifest = load_json_object(feature_manifest_path, label="feature manifest")
    if manifest.get("output_schema_version") != FEATURE_SCHEMA_VERSION:
        raise GateAEvaluationError("unexpected feature schema")
    if manifest.get("candidate_id") != CANDIDATE_ID:
        raise GateAEvaluationError("feature candidate mismatch")
    if (
        manifest.get("research_only") is not True
        or manifest.get("paper_live_enabled") is not False
        or manifest.get("feature_computed") is not True
        or manifest.get("next_month_returns_computed") is not False
        or manifest.get("gate_a_computed") is not False
        or manifest.get("gate_b_computed") is not False
        or manifest.get("validation_outcomes_inspected") is not False
        or manifest.get("locked_oos_outcomes_inspected") is not False
    ):
        raise GateAEvaluationError("feature artifact boundary drifted")
    inputs = required_mapping(manifest, "inputs")
    if inputs.get("config_sha256") != file_sha256(config_path):
        raise GateAEvaluationError("feature/config hash mismatch")
    if inputs.get("normalized_manifest_sha256") != file_sha256(normalized_manifest_path):
        raise GateAEvaluationError("feature/normalized-manifest hash mismatch")
    feature = required_mapping(manifest, "feature_file")
    feature_path = feature_dir / required_text(feature, "path")
    if feature.get("sha256") != file_sha256(feature_path):
        raise GateAEvaluationError("feature cohort hash mismatch")
    return manifest


def verify_normalized_archive(
    *,
    normalized_dir: Path,
    normalized_manifest_path: Path,
    split_start: date,
    split_end: date,
) -> tuple[dict[str, Any], list[Path]]:
    manifest = load_json_object(normalized_manifest_path, label="normalized manifest")
    if manifest.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise GateAEvaluationError("unexpected normalized schema")
    if (
        manifest.get("research_only") is not True
        or manifest.get("paper_live_enabled") is not False
        or manifest.get("factor_or_outcome_computed") is not False
    ):
        raise GateAEvaluationError("normalized archive boundary drifted")
    datasets = required_mapping(manifest, "datasets")
    bars = required_mapping(datasets, "equities_bars_daily")
    partitions = bars.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise GateAEvaluationError("normalized bar partitions missing")
    selected: list[Path] = []
    total_rows = 0
    for record in partitions:
        if not isinstance(record, dict):
            raise GateAEvaluationError("invalid normalized bar partition record")
        relative = record.get("path")
        if not isinstance(relative, str) or not relative:
            raise GateAEvaluationError("invalid normalized bar partition path")
        partition_path = normalized_dir / relative
        if not partition_path.is_file() or record.get("sha256") != file_sha256(partition_path):
            raise GateAEvaluationError(f"normalized bar partition hash mismatch: {relative}")
        row_count = record.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int):
            raise GateAEvaluationError(f"invalid normalized row count: {relative}")
        total_rows += row_count
        first = date.fromisoformat(str(record["first_date"]))
        last = date.fromisoformat(str(record["last_date"]))
        if first <= split_end and last >= split_start:
            if last > split_end:
                raise GateAEvaluationError("selected price partition crosses development end")
            selected.append(partition_path)
    if total_rows != bars.get("source_row_count"):
        raise GateAEvaluationError("normalized bar partition row count mismatch")
    if not selected:
        raise GateAEvaluationError("no development price partitions selected")
    return manifest, selected


def load_development_features(
    path: Path,
    *,
    split_start: date,
    split_end: date,
) -> tuple[pl.DataFrame, list[date]]:
    columns = [
        "signal_date",
        "code",
        "imom6m_no_skip_v0",
        "eligible",
        "selection_rank",
        "eligible_cross_section_count",
        "imom_decile",
        "decile_10_candidate",
        "research_split",
    ]
    schema = pl.read_parquet_schema(path)
    assert_outcome_blind_columns(list(schema))
    missing = sorted(set(columns) - set(schema))
    if missing:
        raise GateAEvaluationError(f"feature cohort missing columns: {missing}")
    frame = (
        pl.scan_parquet(path)
        .select(columns)
        .filter(
            (pl.col("research_split") == "development")
            & pl.col("signal_date").is_between(split_start, split_end, closed="both")
        )
        .collect()
    )
    if frame.is_empty():
        raise GateAEvaluationError("development feature cohort is empty")
    if frame.select(pl.struct("signal_date", "code").is_duplicated().any()).item():
        raise GateAEvaluationError("development feature cohort has duplicate keys")
    calendar = sorted(frame.get_column("signal_date").unique().to_list())
    return frame, calendar


def load_development_prices(
    paths: Sequence[Path],
    *,
    split_start: date,
    split_end: date,
) -> pl.DataFrame:
    frame = (
        pl.read_parquet(paths, columns=["date", "code", "adjusted_close"])
        .filter(pl.col("date").is_between(split_start, split_end, closed="both"))
        .sort("date", "code")
    )
    if frame.select(pl.struct("date", "code").is_duplicated().any()).item():
        raise GateAEvaluationError("development prices contain duplicate keys")
    return frame


def evaluate_months(
    *,
    features: pl.DataFrame,
    prices: pl.DataFrame,
    calendar: Sequence[date],
    split_start: date,
    split_end: date,
    minimum_eligible: int,
) -> list[dict[str, Any]]:
    if minimum_eligible <= 0:
        raise GateAEvaluationError("minimum eligible count must be positive")
    sorted_calendar = sorted(set(calendar))
    next_date = {
        formation: outcome
        for formation, outcome in pairwise(sorted_calendar)
        if split_start <= formation <= split_end and outcome <= split_end
    }
    available_dates = set(
        features.filter(pl.col("imom6m_no_skip_v0").is_not_null())
        .get_column("signal_date")
        .unique()
        .to_list()
    )
    formation_dates = sorted(available_dates & set(next_date))
    if not formation_dates:
        raise GateAEvaluationError("no boundary-complete development formation dates")
    price_by_key = {
        (row["date"], row["code"]): row["adjusted_close"] for row in prices.iter_rows(named=True)
    }
    monthly: list[dict[str, Any]] = []
    for formation_date in formation_dates:
        outcome_date = next_date[formation_date]
        eligible = features.filter(
            (pl.col("signal_date") == formation_date) & pl.col("eligible")
        ).sort("selection_rank")
        validate_formation_ranks(eligible)
        observations: list[tuple[float, float, int]] = []
        missing_count = 0
        for row in eligible.iter_rows(named=True):
            formation_close = price_by_key.get((formation_date, row["code"]))
            outcome_close = price_by_key.get((outcome_date, row["code"]))
            if not valid_positive_number(formation_close) or not valid_positive_number(
                outcome_close
            ):
                missing_count += 1
                continue
            next_return = float(outcome_close) / float(formation_close) - 1.0
            imom = row["imom6m_no_skip_v0"]
            decile = row["imom_decile"]
            if not valid_finite_number(next_return) or not valid_finite_number(imom):
                missing_count += 1
                continue
            if isinstance(decile, bool) or not isinstance(decile, int):
                raise GateAEvaluationError("eligible row has invalid decile")
            observations.append((float(imom), next_return, decile))

        eligible_count = eligible.height
        incomplete_reason: str | None = None
        rank_ic: float | None = None
        if eligible_count < minimum_eligible:
            incomplete_reason = "ELIGIBLE_CROSS_SECTION_BELOW_MINIMUM"
        elif missing_count:
            incomplete_reason = "MISSING_OR_NONPOSITIVE_EXACT_ENDPOINT"
        else:
            rank_ic = spearman_average_rank(
                [row[0] for row in observations],
                [row[1] for row in observations],
            )
            if rank_ic is None or not math.isfinite(rank_ic):
                incomplete_reason = "UNDEFINED_RANK_IC"

        complete = incomplete_reason is None
        d10_returns = [row[1] for row in observations if row[2] == 10]
        d1_returns = [row[1] for row in observations if row[2] == 1]
        if complete and (not d10_returns or not d1_returns):
            complete = False
            incomplete_reason = "EMPTY_EXTREME_DECILE"
        d10_return = fmean(d10_returns) if complete else None
        d1_return = fmean(d1_returns) if complete else None
        spread = d10_return - d1_return if complete else None
        monthly.append(
            {
                "formation_date": formation_date.isoformat(),
                "outcome_date": outcome_date.isoformat(),
                "eligible_count": eligible_count,
                "valid_outcome_count": len(observations),
                "missing_outcome_count": missing_count,
                "decile_10_count": len(d10_returns),
                "decile_1_count": len(d1_returns),
                "complete": complete,
                "incomplete_reason": incomplete_reason,
                "decile_10_return": d10_return,
                "decile_1_return": d1_return,
                "decile_10_minus_decile_1_return": spread,
                "rank_ic": rank_ic if complete else None,
            }
        )
    return monthly


def validate_formation_ranks(eligible: pl.DataFrame) -> None:
    if eligible.is_empty():
        return
    n = eligible.height
    if eligible.get_column("selection_rank").to_list() != list(range(1, n + 1)):
        raise GateAEvaluationError("formation ranks are not contiguous")
    if eligible.get_column("eligible_cross_section_count").unique().to_list() != [n]:
        raise GateAEvaluationError("formation eligible count drifted")
    expected = [10 - math.floor((rank - 1) * 10 / n) for rank in range(1, n + 1)]
    if eligible.get_column("imom_decile").to_list() != expected:
        raise GateAEvaluationError("formation deciles drifted")
    ordered = eligible.sort(
        ["imom6m_no_skip_v0", "code"],
        descending=[True, False],
    ).get_column("code")
    if ordered.to_list() != eligible.get_column("code").to_list():
        raise GateAEvaluationError("formation feature order or tie breaker drifted")


def spearman_average_rank(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    return pearson_correlation(average_ranks(left), average_ranks(right))


def average_ranks(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = ((start + 1) + end) / 2
        for position in range(start, end):
            ranks[indexed[position][0]] = average
        start = end
    return ranks


def pearson_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0:
        return None
    return (
        sum(
            left_value * right_value
            for left_value, right_value in zip(left_centered, right_centered, strict=True)
        )
        / denominator
    )


def aggregate_complete_months(
    monthly: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, float | None], str | None]:
    complete = [row for row in monthly if row.get("complete") is True]
    if not complete:
        return empty_metrics(), None
    d10 = [float(row["decile_10_return"]) for row in complete]
    spreads = [float(row["decile_10_minus_decile_1_return"]) for row in complete]
    rank_ics = [float(row["rank_ic"]) for row in complete]
    half = len(complete) // 2
    first_half = spreads[:half]
    second_half = spreads[half:]
    removed = sorted(
        complete,
        key=lambda row: (
            -float(row["decile_10_minus_decile_1_return"]),
            str(row["formation_date"]),
        ),
    )[0]
    after_removal = [row for row in complete if row is not removed]
    return (
        {
            "mean_decile_10_return": fmean(d10),
            "mean_decile_10_minus_decile_1_return": fmean(spreads),
            "mean_monthly_rank_ic": fmean(rank_ics),
            "mean_spread_first_half": fmean(first_half) if first_half else None,
            "mean_spread_second_half": fmean(second_half) if second_half else None,
            "mean_spread_after_largest_month_removed": (
                fmean(float(row["decile_10_minus_decile_1_return"]) for row in after_removal)
                if after_removal
                else None
            ),
        },
        str(removed["formation_date"]),
    )


def empty_metrics() -> dict[str, None]:
    return {
        "mean_decile_10_return": None,
        "mean_decile_10_minus_decile_1_return": None,
        "mean_monthly_rank_ic": None,
        "mean_spread_first_half": None,
        "mean_spread_second_half": None,
        "mean_spread_after_largest_month_removed": None,
    }


def evaluate_gates(
    *,
    metrics: Mapping[str, float | None],
    complete_month_count: int,
    pass_contract: Mapping[str, Any],
) -> dict[str, Any]:
    minimum = int(pass_contract["minimum_boundary_complete_months"])
    checks: dict[str, dict[str, Any]] = {
        "minimum_boundary_complete_months": {
            "value": complete_month_count,
            "threshold": minimum,
            "comparison": ">=",
            "passed": complete_month_count >= minimum,
        }
    }
    metric_to_threshold = {
        "mean_decile_10_return": "mean_decile_10_return_exclusive",
        "mean_decile_10_minus_decile_1_return": ("mean_decile_10_minus_decile_1_exclusive"),
        "mean_monthly_rank_ic": "mean_monthly_rank_ic_exclusive",
        "mean_spread_first_half": "mean_spread_first_half_exclusive",
        "mean_spread_second_half": "mean_spread_second_half_exclusive",
        "mean_spread_after_largest_month_removed": (
            "mean_spread_after_largest_month_removed_exclusive"
        ),
    }
    for metric, threshold_key in metric_to_threshold.items():
        value = metrics.get(metric)
        threshold = float(pass_contract[threshold_key])
        checks[metric] = {
            "value": value,
            "threshold": threshold,
            "comparison": ">",
            "passed": value is not None and math.isfinite(value) and value > threshold,
        }
    return {
        "checks": checks,
        "all_passed": all(check["passed"] for check in checks.values()),
    }


def build_run_manifest(
    *,
    result_path: Path,
    authorization_path: Path,
    authorization: Mapping[str, Any],
    config_path: Path,
    feature_manifest_path: Path,
    feature_manifest: Mapping[str, Any],
    normalized_manifest_path: Path,
    normalized_manifest: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "builder_sha256": file_sha256(Path(__file__)),
        "candidate_id": CANDIDATE_ID,
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "gate_a_computed": True,
        "gate_b_computed": False,
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "inputs": {
            "authorization_path": str(authorization_path),
            "authorization_sha256": file_sha256(authorization_path),
            "authorization_id": authorization["authorization_id"],
            "config_path": str(config_path),
            "config_sha256": file_sha256(config_path),
            "feature_manifest_path": str(feature_manifest_path),
            "feature_manifest_sha256": file_sha256(feature_manifest_path),
            "feature_cohort_sha256": required_mapping(feature_manifest, "feature_file")["sha256"],
            "normalized_manifest_path": str(normalized_manifest_path),
            "normalized_manifest_sha256": file_sha256(normalized_manifest_path),
            "normalized_schema_version": normalized_manifest.get("normalized_schema_version"),
        },
        "result": {
            "path": RESULT_FILENAME,
            "sha256": file_sha256(result_path),
            "byte_size": result_path.stat().st_size,
            "decision": result["decision"],
            "attempted_formation_month_count": result["attempted_formation_month_count"],
            "complete_month_count": result["complete_month_count"],
        },
    }


def assert_outcome_blind_columns(columns: Sequence[str]) -> None:
    for column in columns:
        if any(part in column.lower() for part in FORBIDDEN_FEATURE_COLUMN_PARTS):
            raise GateAEvaluationError(f"outcome-like feature column is prohibited: {column}")


def valid_positive_number(value: Any) -> bool:
    return valid_finite_number(value) and float(value) > 0


def valid_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    for path in (output_dir, temporary_dir):
        if path.exists():
            raise FileExistsError(f"Gate A output path already exists: {path}")


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateAEvaluationError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise GateAEvaluationError(f"{label} must be a JSON object")
    return value


def required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise GateAEvaluationError(f"missing object: {key}")
    return nested


def required_text(value: Mapping[str, Any], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise GateAEvaluationError(f"missing text: {key}")
    return nested


def bound_repository_path(value: Mapping[str, Any], *, label: str) -> Path:
    relative = Path(required_text(value, "path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise GateAEvaluationError(f"authorization/{label} path is not repository-relative")
    resolved = (REPOSITORY_ROOT / relative).resolve()
    if not resolved.is_relative_to(REPOSITORY_ROOT):
        raise GateAEvaluationError(f"authorization/{label} path escapes repository")
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
