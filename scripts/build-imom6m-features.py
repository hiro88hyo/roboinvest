#!/usr/bin/env python3
"""Build the preregistered outcome-blind IMOM6M feature cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

CANDIDATE_ID = "imom6m_top5_fixed20_v0_research"
CONFIG_STATUS = "PREREGISTERED_DESIGN_NO_COMPUTE_AUTHORITY"
AUTHORIZATION_ID = "imom6m_top5_fixed20_v0_phase1_outcome_blind_feature"
FEATURE_ID = "IMOM6M_NO_SKIP_V0"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
OUTPUT_SCHEMA_VERSION = "imom6m_no_skip_feature_cohort_v1"
NORMALIZED_MANIFEST_FILENAME = "normalized-manifest.json"
FEATURE_FILENAME = "feature-cohort.parquet"
FEATURE_MANIFEST_FILENAME = "feature-manifest.json"
AUDIT_FILENAME = "cohort-audit.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_OUTPUT_COLUMN_PARTS = (
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


class ImomFeatureBuildError(ValueError):
    """Raised when a preregistration, input, or feature invariant is violated."""


def main() -> int:
    args = build_parser().parse_args()
    build_feature_artifact(
        normalized_dir=args.normalized_dir,
        config_path=args.config,
        authorization_path=args.authorization,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def build_feature_artifact(
    *,
    normalized_dir: Path,
    config_path: Path,
    authorization_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_config(config_path)
    normalized_manifest_path = normalized_dir / NORMALIZED_MANIFEST_FILENAME
    normalized_manifest = load_and_verify_normalized_archive(
        normalized_manifest_path,
        normalized_dir=normalized_dir,
    )
    authorization = load_and_verify_authorization(
        authorization_path,
        config_path=config_path,
        normalized_manifest_path=normalized_manifest_path,
        output_dir=output_dir,
    )
    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    temporary_dir.mkdir(parents=True)

    bars = pl.read_parquet(normalized_dir / "bars" / "*.parquet")
    master = pl.read_parquet(normalized_dir / "master" / "*.parquet")
    cohort, calendar = build_feature_cohort(bars=bars, master=master, config=config)
    assert_outcome_blind_columns(cohort.columns)
    validate_built_cohort(cohort)
    feature_path = temporary_dir / FEATURE_FILENAME
    cohort.write_parquet(feature_path, compression="zstd", statistics=True)
    audit = build_audit(cohort=cohort, calendar=calendar)
    write_json(temporary_dir / AUDIT_FILENAME, audit)
    manifest = build_feature_manifest(
        normalized_manifest_path=normalized_manifest_path,
        normalized_manifest=normalized_manifest,
        config_path=config_path,
        authorization_path=authorization_path,
        authorization=authorization,
        feature_path=feature_path,
        cohort=cohort,
        audit=audit,
    )
    write_json(temporary_dir / FEATURE_MANIFEST_FILENAME, manifest)
    temporary_dir.rename(output_dir)
    return manifest


def load_config(path: Path) -> dict[str, Any]:
    config = load_json_object(path, label="IMOM research config")
    if config.get("candidate_id") != CANDIDATE_ID:
        raise ImomFeatureBuildError("unexpected candidate ID")
    if config.get("status") != CONFIG_STATUS:
        raise ImomFeatureBuildError("unexpected preregistration status")
    if config.get("evidence_class") != "PAPER_INSPIRED_IMPLEMENTABLE_ADAPTATION":
        raise ImomFeatureBuildError("evidence boundary drifted")
    source = required_mapping(config, "source_boundary")
    if source.get("replication_claim_authorized") is not False:
        raise ImomFeatureBuildError("replication claim must remain prohibited")
    cycle = required_mapping(config, "research_cycle")
    if (
        cycle.get("candidate_number") != 2
        or cycle.get("maximum_candidates") != 2
        or cycle.get("stop_after_this_candidate") is not True
    ):
        raise ImomFeatureBuildError("trial-limit contract drifted")
    feature = required_mapping(config, "feature")
    if (
        feature.get("feature_id") != FEATURE_ID
        or feature.get("lookback_months") != 6
        or feature.get("skip_most_recent_month") is not False
        or feature.get("direction") != "HIGHER_IS_BETTER"
    ):
        raise ImomFeatureBuildError("IMOM6M feature definition drifted")
    universe = required_mapping(config, "universe")
    if (
        universe.get("required_consecutive_month_end_adjusted_closes") != 7
        or universe.get("required_monthly_returns") != 6
        or universe.get("winsorization") != "NONE"
    ):
        raise ImomFeatureBuildError("fixed monthly window drifted")
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
        raise ImomFeatureBuildError("base config authority boundary must remain closed")
    decision = required_mapping(config, "decision_contract")
    if (
        decision.get("counts_as_2026_09_30_kill_switch_evidence") is not False
        or decision.get("paper_or_live_activation_authorized") is not False
    ):
        raise ImomFeatureBuildError("research-only decision boundary is open")
    return config


def load_and_verify_authorization(
    path: Path,
    *,
    config_path: Path,
    normalized_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    value = load_json_object(path, label="Phase 1 authorization")
    if value.get("authorization_id") != AUTHORIZATION_ID:
        raise ImomFeatureBuildError("unexpected Phase 1 authorization")
    if value.get("candidate_id") != CANDIDATE_ID:
        raise ImomFeatureBuildError("authorization candidate mismatch")
    if (
        value.get("research_only") is not True
        or value.get("paper_live_enabled") is not False
        or value.get("counts_as_2026_09_30_kill_switch_evidence") is not False
    ):
        raise ImomFeatureBuildError("authorization research boundary is open")
    scope = required_mapping(value, "scope")
    if (
        scope.get("implement_outcome_blind_feature_builder") is not True
        or scope.get("build_feature_artifact_once") is not True
        or scope.get("compute_next_month_returns") is not False
        or scope.get("compute_gate_a") is not False
        or scope.get("implement_or_compute_gate_b") is not False
        or scope.get("inspect_development_outcomes") is not False
        or scope.get("inspect_validation_outcomes") is not False
        or scope.get("inspect_locked_oos_outcomes") is not False
        or scope.get("modify_paper_or_live") is not False
    ):
        raise ImomFeatureBuildError("Phase 1 authorization scope drifted")
    expected_output = scope.get("expected_output_dir")
    if not isinstance(expected_output, str) or (
        output_dir.resolve() != Path(expected_output).resolve()
    ):
        raise ImomFeatureBuildError("output path differs from authorization")
    inputs = required_mapping(value, "bound_inputs")
    config_record = required_mapping(inputs, "config")
    normalized_record = required_mapping(inputs, "normalized_manifest")
    trial_registry_record = required_mapping(inputs, "trial_registry")
    if bound_repository_path(config_record, label="config") != config_path.resolve():
        raise ImomFeatureBuildError("authorization/config path mismatch")
    if (
        bound_repository_path(normalized_record, label="normalized manifest")
        != normalized_manifest_path.resolve()
    ):
        raise ImomFeatureBuildError("authorization/normalized-manifest path mismatch")
    if config_record.get("sha256") != file_sha256(config_path):
        raise ImomFeatureBuildError("authorization/config hash mismatch")
    if normalized_record.get("sha256") != file_sha256(normalized_manifest_path):
        raise ImomFeatureBuildError("authorization/normalized-manifest hash mismatch")
    trial_registry_path = bound_repository_path(
        trial_registry_record,
        label="trial registry",
    )
    if not trial_registry_path.is_file():
        raise ImomFeatureBuildError("authorization/trial registry is missing")
    if trial_registry_record.get("sha256") != file_sha256(trial_registry_path):
        raise ImomFeatureBuildError("authorization/trial-registry hash mismatch")
    return value


def load_and_verify_normalized_archive(
    path: Path,
    *,
    normalized_dir: Path,
) -> dict[str, Any]:
    value = load_json_object(path, label="normalized manifest")
    if value.get("manifest_version") != 1:
        raise ImomFeatureBuildError("unsupported normalized manifest version")
    if value.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise ImomFeatureBuildError("unexpected normalized schema version")
    if (
        value.get("research_only") is not True
        or value.get("paper_live_enabled") is not False
        or value.get("factor_or_outcome_computed") is not False
    ):
        raise ImomFeatureBuildError("normalized archive boundary is not outcome-blind")
    datasets = required_mapping(value, "datasets")
    for dataset_name in ("equities_bars_daily", "equities_master_month_end"):
        dataset = required_mapping(datasets, dataset_name)
        partitions = dataset.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise ImomFeatureBuildError(f"normalized partitions missing: {dataset_name}")
        total_rows = 0
        for record in partitions:
            if not isinstance(record, dict):
                raise ImomFeatureBuildError(f"invalid partition record: {dataset_name}")
            relative_path = record.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                raise ImomFeatureBuildError(f"invalid partition path: {dataset_name}")
            partition_path = normalized_dir / relative_path
            if not partition_path.is_file():
                raise ImomFeatureBuildError(f"partition missing: {partition_path}")
            if record.get("sha256") != file_sha256(partition_path):
                raise ImomFeatureBuildError(f"partition hash mismatch: {relative_path}")
            row_count = record.get("row_count")
            if isinstance(row_count, bool) or not isinstance(row_count, int):
                raise ImomFeatureBuildError(f"invalid partition row count: {relative_path}")
            total_rows += row_count
        if total_rows != dataset.get("source_row_count"):
            raise ImomFeatureBuildError(f"partition row count mismatch: {dataset_name}")
    return value


def build_feature_cohort(
    *,
    bars: pl.DataFrame,
    master: pl.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    validate_input_columns(bars=bars, master=master)
    calendar = derive_global_month_end_calendar(bars)
    monthly_features = compute_monthly_features(bars=bars, calendar=calendar)
    turnover = compute_month_end_turnover(bars=bars, calendar=calendar)
    feature_points = monthly_features.join(turnover, on=["signal_date", "code"], how="left")

    universe = required_mapping(config, "universe")
    product_allowlist = string_list(universe, "product_category_allowlist")
    market_allowlist = string_list(universe, "market_code_allowlist")
    minimum_turnover = positive_number(
        universe.get("minimum_current_20_valid_session_median_turnover_jpy"),
        label="minimum turnover",
    )
    cohort = (
        master.rename({"as_of_date": "signal_date"})
        .join(feature_points, on=["signal_date", "code"], how="left")
        .with_columns(
            pl.when(~pl.col("product_category").is_in(product_allowlist))
            .then(pl.lit("DISALLOWED_PRODUCT_CATEGORY"))
            .when(~pl.col("market_code").is_in(market_allowlist))
            .then(pl.lit("DISALLOWED_MARKET"))
            .when(pl.col("imom6m_no_skip_v0").is_null())
            .then(pl.lit("INSUFFICIENT_CONSECUTIVE_MONTH_END_RETURNS"))
            .when(pl.col("current_20_median_turnover_jpy").is_null())
            .then(pl.lit("INSUFFICIENT_VALID_TURNOVER_WINDOW"))
            .when(pl.col("current_20_median_turnover_jpy") < minimum_turnover)
            .then(pl.lit("BELOW_MINIMUM_MEDIAN_TURNOVER"))
            .otherwise(pl.lit("ELIGIBLE"))
            .alias("eligibility_reason")
        )
        .with_columns((pl.col("eligibility_reason") == "ELIGIBLE").alias("eligible"))
    )
    eligible_rank = (
        cohort.filter(pl.col("eligible"))
        .sort(
            ["signal_date", "imom6m_no_skip_v0", "code"],
            descending=[False, True, False],
        )
        .with_columns(
            pl.int_range(1, pl.len() + 1).over("signal_date").alias("selection_rank"),
            pl.len().over("signal_date").alias("eligible_cross_section_count"),
        )
        .with_columns(
            (
                pl.lit(10)
                - ((pl.col("selection_rank") - 1) * 10 / pl.col("eligible_cross_section_count"))
                .floor()
                .cast(pl.UInt8)
            ).alias("imom_decile")
        )
        .with_columns((pl.col("imom_decile") == 10).alias("decile_10_candidate"))
        .select(
            "signal_date",
            "code",
            "selection_rank",
            "eligible_cross_section_count",
            "imom_decile",
            "decile_10_candidate",
        )
    )
    splits = required_mapping(config, "splits")
    result = (
        cohort.join(eligible_rank, on=["signal_date", "code"], how="left")
        .with_columns(
            split_expression(splits).alias("research_split"),
            pl.col("decile_10_candidate").fill_null(False),
        )
        .sort("signal_date", "code")
    )
    return result, calendar


def derive_global_month_end_calendar(bars: pl.DataFrame) -> pl.DataFrame:
    return (
        bars.select("date")
        .unique()
        .with_columns(
            pl.col("date").dt.year().alias("_year"),
            pl.col("date").dt.month().alias("_month"),
        )
        .group_by("_year", "_month")
        .agg(pl.col("date").max().alias("signal_date"))
        .sort("signal_date")
        .with_row_index("global_month_index")
        .select("signal_date", "global_month_index")
    )


def compute_monthly_features(*, bars: pl.DataFrame, calendar: pl.DataFrame) -> pl.DataFrame:
    monthly = (
        bars.select(pl.col("date").alias("signal_date"), "code", "adjusted_close")
        .join(calendar, on="signal_date", how="inner")
        .sort("code", "global_month_index")
        .with_columns(
            pl.col("adjusted_close").shift(1).over("code").alias("_previous_close"),
            pl.col("global_month_index").shift(1).over("code").alias("_previous_month_index"),
            pl.col("adjusted_close").shift(6).over("code").alias("_six_month_prior_close"),
            pl.col("global_month_index").shift(6).over("code").alias("_six_month_prior_index"),
        )
        .with_columns(
            pl.when(
                pl.col("adjusted_close").is_not_null()
                & (pl.col("adjusted_close") > 0)
                & pl.col("_previous_close").is_not_null()
                & (pl.col("_previous_close") > 0)
                & (pl.col("global_month_index") - pl.col("_previous_month_index") == 1)
            )
            .then(pl.col("adjusted_close") / pl.col("_previous_close") - 1)
            .otherwise(None)
            .alias("_monthly_return")
        )
        .with_columns(
            pl.col("_monthly_return")
            .rolling_sum(window_size=6, min_samples=6)
            .over("code")
            .alias("_six_month_return_sum"),
            pl.col("_monthly_return")
            .is_not_null()
            .cast(pl.UInt8)
            .rolling_sum(window_size=6, min_samples=6)
            .over("code")
            .alias("valid_monthly_return_count"),
        )
        .with_columns(
            pl.when(
                (pl.col("valid_monthly_return_count") == 6)
                & pl.col("_six_month_prior_close").is_not_null()
                & (pl.col("_six_month_prior_close") > 0)
                & (pl.col("global_month_index") - pl.col("_six_month_prior_index") == 6)
            )
            .then(pl.lit(100.0) * (pl.col("adjusted_close") / pl.col("_six_month_prior_close") - 1))
            .otherwise(None)
            .alias("mom6m_no_skip_v0"),
            pl.when(pl.col("valid_monthly_return_count") == 6)
            .then(pl.lit(100.0) * pl.col("_six_month_return_sum"))
            .otherwise(None)
            .alias("sum6m_no_skip_v0"),
        )
        .with_columns(
            (pl.col("mom6m_no_skip_v0") - pl.col("sum6m_no_skip_v0")).alias("imom6m_no_skip_v0")
        )
    )
    return monthly.select(
        "signal_date",
        "code",
        "global_month_index",
        "valid_monthly_return_count",
        "mom6m_no_skip_v0",
        "sum6m_no_skip_v0",
        "imom6m_no_skip_v0",
    )


def compute_month_end_turnover(*, bars: pl.DataFrame, calendar: pl.DataFrame) -> pl.DataFrame:
    return (
        bars.filter(
            pl.col("adjusted_close").is_not_null()
            & (pl.col("adjusted_close") > 0)
            & pl.col("turnover_jpy").is_not_null()
            & (pl.col("turnover_jpy") > 0)
        )
        .select("date", "code", "turnover_jpy")
        .sort("code", "date")
        .with_columns(
            pl.col("turnover_jpy")
            .rolling_median(window_size=20, min_samples=20)
            .over("code")
            .alias("current_20_median_turnover_jpy")
        )
        .rename({"date": "signal_date"})
        .join(calendar.select("signal_date"), on="signal_date", how="inner")
        .select("signal_date", "code", "current_20_median_turnover_jpy")
    )


def split_expression(split_config: Mapping[str, Any]) -> pl.Expr:
    development = required_mapping(split_config, "development")
    validation = required_mapping(split_config, "validation")
    locked = required_mapping(split_config, "locked_oos")
    development_end = date.fromisoformat(required_text(development, "signal_date_end"))
    validation_start = date.fromisoformat(required_text(validation, "signal_date_start"))
    validation_end = date.fromisoformat(required_text(validation, "signal_date_end"))
    locked_start = date.fromisoformat(required_text(locked, "signal_date_start"))
    locked_end = date.fromisoformat(required_text(locked, "signal_date_end"))
    return (
        pl.when(pl.col("signal_date") <= development_end)
        .then(pl.lit("development"))
        .when(pl.col("signal_date").is_between(validation_start, validation_end, closed="both"))
        .then(pl.lit("validation"))
        .when(pl.col("signal_date").is_between(locked_start, locked_end, closed="both"))
        .then(pl.lit("locked_oos"))
        .otherwise(pl.lit("outside_registered_splits"))
    )


def validate_built_cohort(cohort: pl.DataFrame) -> None:
    if cohort.select(pl.struct("signal_date", "code").is_duplicated().any()).item():
        raise ImomFeatureBuildError("built cohort has duplicate signal-date/code rows")
    eligible = cohort.filter(pl.col("eligible"))
    if eligible.is_empty():
        raise ImomFeatureBuildError("built cohort has no eligible rows")
    if eligible.get_column("imom_decile").null_count() != 0:
        raise ImomFeatureBuildError("eligible row missing decile")
    if eligible.filter(~pl.col("imom_decile").is_between(1, 10, closed="both")).height:
        raise ImomFeatureBuildError("decile outside registered range")
    per_date = eligible.group_by("signal_date").agg(
        pl.len().alias("n"),
        pl.col("decile_10_candidate").sum().alias("actual_top"),
        pl.col("selection_rank").min().alias("min_rank"),
        pl.col("selection_rank").max().alias("max_rank"),
    )
    failures = per_date.filter(
        (pl.col("actual_top") != (pl.col("n").cast(pl.Float64) / 10).ceil())
        | (pl.col("min_rank") != 1)
        | (pl.col("max_rank") != pl.col("n"))
    )
    if not failures.is_empty():
        raise ImomFeatureBuildError("rank or decile-10 count invariant failed")


def build_audit(*, cohort: pl.DataFrame, calendar: pl.DataFrame) -> dict[str, Any]:
    per_split = (
        cohort.group_by("research_split")
        .agg(
            pl.len().alias("master_rows"),
            pl.col("signal_date").n_unique().alias("signal_dates"),
            pl.col("imom6m_no_skip_v0").is_not_null().sum().alias("feature_available_rows"),
            pl.col("eligible").sum().alias("eligible_rows"),
            pl.col("decile_10_candidate").sum().alias("decile_10_candidate_rows"),
        )
        .sort("research_split")
    )
    per_date = (
        cohort.group_by("signal_date", "research_split")
        .agg(
            pl.len().alias("master_rows"),
            pl.col("imom6m_no_skip_v0").is_not_null().sum().alias("feature_available_rows"),
            pl.col("eligible").sum().alias("eligible_rows"),
            pl.col("decile_10_candidate").sum().alias("decile_10_candidate_rows"),
        )
        .sort("signal_date")
    )
    reasons = cohort.group_by("eligibility_reason").len(name="row_count").sort("eligibility_reason")
    calendar_dates = calendar.get_column("signal_date")
    return {
        "audit_version": 1,
        "feature_id": FEATURE_ID,
        "research_only": True,
        "paper_live_enabled": False,
        "feature_computed": True,
        "next_month_returns_computed": False,
        "gate_a_computed": False,
        "gate_b_computed": False,
        "development_outcomes_inspected": False,
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "row_count": len(cohort),
        "global_month_end_count": len(calendar),
        "first_global_month_end": min(calendar_dates).isoformat(),
        "last_global_month_end": max(calendar_dates).isoformat(),
        "per_split": records_for_json(per_split),
        "per_signal_date": records_for_json(per_date),
        "eligibility_reasons": records_for_json(reasons),
    }


def build_feature_manifest(
    *,
    normalized_manifest_path: Path,
    normalized_manifest: Mapping[str, Any],
    config_path: Path,
    authorization_path: Path,
    authorization: Mapping[str, Any],
    feature_path: Path,
    cohort: pl.DataFrame,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    signal_dates = cohort.get_column("signal_date")
    return {
        "manifest_version": 1,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "builder_sha256": file_sha256(Path(__file__)),
        "candidate_id": CANDIDATE_ID,
        "feature_id": FEATURE_ID,
        "evidence_class": "PAPER_INSPIRED_IMPLEMENTABLE_ADAPTATION",
        "research_only": True,
        "paper_live_enabled": False,
        "feature_computed": True,
        "next_month_returns_computed": False,
        "gate_a_computed": False,
        "gate_b_computed": False,
        "development_outcomes_inspected": False,
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "inputs": {
            "config_path": str(config_path),
            "config_sha256": file_sha256(config_path),
            "authorization_path": str(authorization_path),
            "authorization_sha256": file_sha256(authorization_path),
            "authorization_id": authorization["authorization_id"],
            "normalized_manifest_path": str(normalized_manifest_path),
            "normalized_manifest_sha256": file_sha256(normalized_manifest_path),
            "normalized_schema_version": normalized_manifest.get("normalized_schema_version"),
        },
        "feature_file": {
            "path": FEATURE_FILENAME,
            "sha256": file_sha256(feature_path),
            "byte_size": feature_path.stat().st_size,
            "row_count": len(cohort),
            "first_signal_date": min(signal_dates).isoformat(),
            "last_signal_date": max(signal_dates).isoformat(),
            "feature_available_row_count": int(
                cohort.get_column("imom6m_no_skip_v0").is_not_null().sum()
            ),
            "eligible_row_count": int(cohort.get_column("eligible").sum()),
            "decile_10_candidate_row_count": int(cohort.get_column("decile_10_candidate").sum()),
        },
        "audit": {
            "path": AUDIT_FILENAME,
            "sha256": file_sha256(feature_path.parent / AUDIT_FILENAME),
            "row_count": audit["row_count"],
        },
    }


def validate_input_columns(*, bars: pl.DataFrame, master: pl.DataFrame) -> None:
    required_bars = {"date", "code", "adjusted_close", "turnover_jpy"}
    required_master = {
        "as_of_date",
        "code",
        "company_name",
        "company_name_en",
        "product_category",
        "market_code",
        "market_name",
        "margin_code",
        "margin_name",
        "sector17_code",
        "sector17_name",
        "sector33_code",
        "sector33_name",
        "scale_category",
    }
    if missing := sorted(required_bars - set(bars.columns)):
        raise ImomFeatureBuildError(f"normalized bars missing columns: {missing}")
    if missing := sorted(required_master - set(master.columns)):
        raise ImomFeatureBuildError(f"normalized master missing columns: {missing}")
    if bars.select(pl.struct("date", "code").is_duplicated().any()).item():
        raise ImomFeatureBuildError("normalized bars contain duplicate date/code rows")
    if master.select(pl.struct("as_of_date", "code").is_duplicated().any()).item():
        raise ImomFeatureBuildError("normalized master contains duplicate date/code rows")


def assert_outcome_blind_columns(columns: Sequence[str]) -> None:
    for column in columns:
        if any(part in column.lower() for part in FORBIDDEN_OUTPUT_COLUMN_PARTS):
            raise ImomFeatureBuildError(f"outcome-like column is prohibited: {column}")


def records_for_json(frame: pl.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: value.isoformat() if isinstance(value, date) else value for key, value in row.items()}
        for row in frame.iter_rows(named=True)
    ]


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    for path in (output_dir, temporary_dir):
        if path.exists():
            raise FileExistsError(f"feature output path already exists: {path}")


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImomFeatureBuildError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ImomFeatureBuildError(f"{label} must be a JSON object")
    return value


def required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ImomFeatureBuildError(f"missing object: {key}")
    return nested


def required_text(value: Mapping[str, Any], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise ImomFeatureBuildError(f"missing text: {key}")
    return nested


def bound_repository_path(value: Mapping[str, Any], *, label: str) -> Path:
    relative = Path(required_text(value, "path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ImomFeatureBuildError(f"authorization/{label} path is not repository-relative")
    resolved = (REPOSITORY_ROOT / relative).resolve()
    if not resolved.is_relative_to(REPOSITORY_ROOT):
        raise ImomFeatureBuildError(f"authorization/{label} path escapes repository")
    return resolved


def string_list(value: Mapping[str, Any], key: str) -> list[str]:
    nested = value.get(key)
    if (
        not isinstance(nested, list)
        or not nested
        or not all(isinstance(item, str) for item in nested)
    ):
        raise ImomFeatureBuildError(f"field must be a nonempty string list: {key}")
    return list(nested)


def positive_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ImomFeatureBuildError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ImomFeatureBuildError(f"{label} must be positive and finite")
    return parsed


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
