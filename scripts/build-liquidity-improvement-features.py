#!/usr/bin/env python3
"""Build the preregistered liquidity-improvement feature cohort without outcomes."""

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

NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
CONFIG_STATUS = "REGISTERED_BEFORE_FACTOR_AND_OUTCOME_COMPUTATION"
FEATURE_ID = "LIQIMP1M_LOGDIFF_V0"
OUTPUT_SCHEMA_VERSION = "liqimp1m_logdiff_feature_cohort_v1"
NORMALIZED_MANIFEST_FILENAME = "normalized-manifest.json"
FEATURE_FILENAME = "feature-cohort.parquet"
FEATURE_MANIFEST_FILENAME = "feature-manifest.json"
AUDIT_FILENAME = "cohort-audit.json"

FORBIDDEN_OUTPUT_COLUMN_PARTS = (
    "forward_return",
    "future_return",
    "entry_price",
    "exit_price",
    "trade_pnl",
    "profit_factor",
    "drawdown",
)


class FeatureBuildError(ValueError):
    """Raised when an input or preregistration invariant is violated."""


def main() -> int:
    args = build_parser().parse_args()
    build_feature_artifact(
        normalized_dir=args.normalized_dir,
        config_path=args.config,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def build_feature_artifact(
    *,
    normalized_dir: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_config(config_path)
    normalized_manifest_path = normalized_dir / NORMALIZED_MANIFEST_FILENAME
    normalized_manifest = load_and_verify_normalized_archive(
        normalized_manifest_path,
        normalized_dir=normalized_dir,
    )
    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    temporary_dir.mkdir(parents=True)

    bars = pl.read_parquet(normalized_dir / "bars" / "*.parquet")
    master = pl.read_parquet(normalized_dir / "master" / "*.parquet")
    cohort = build_feature_cohort(bars=bars, master=master, config=config)
    assert_outcome_blind_columns(cohort.columns)
    feature_path = temporary_dir / FEATURE_FILENAME
    cohort.write_parquet(feature_path, compression="zstd", statistics=True)
    audit = build_audit(cohort)
    write_json(temporary_dir / AUDIT_FILENAME, audit)
    manifest = build_feature_manifest(
        normalized_manifest_path=normalized_manifest_path,
        normalized_manifest=normalized_manifest,
        config_path=config_path,
        feature_path=feature_path,
        cohort=cohort,
        audit=audit,
    )
    write_json(temporary_dir / FEATURE_MANIFEST_FILENAME, manifest)
    temporary_dir.rename(output_dir)
    return manifest


def load_config(path: Path) -> dict[str, Any]:
    value = load_json_object(path, label="research config")
    if value.get("candidate_id") != "liqimp1m_logdiff_v0_research":
        raise FeatureBuildError("unexpected candidate ID")
    if value.get("status") != CONFIG_STATUS:
        raise FeatureBuildError("research config is not registered for feature computation")
    if value.get("evidence_class") != "PAPER_INSPIRED_NOT_REPLICATION":
        raise FeatureBuildError("research evidence boundary is missing")
    source_boundary = required_mapping(value, "source_boundary")
    if source_boundary.get("exact_liqc_formula_verified") is not False:
        raise FeatureBuildError("exact LIQC boundary must remain false for V0")
    feature = required_mapping(value, "feature")
    if feature.get("feature_id") != FEATURE_ID:
        raise FeatureBuildError("unexpected feature ID")
    if feature.get("alternative_windows_or_signs_authorized") is not False:
        raise FeatureBuildError("alternative feature variants are not allowed")
    decision = required_mapping(value, "decision_contract")
    if (
        decision.get("counts_as_2026_09_30_kill_switch_evidence") is not False
        or decision.get("paper_or_live_activation_authorized") is not False
    ):
        raise FeatureBuildError("research-only decision boundary is not closed")
    return value


def load_and_verify_normalized_archive(
    path: Path,
    *,
    normalized_dir: Path,
) -> dict[str, Any]:
    value = load_json_object(path, label="normalized manifest")
    if value.get("manifest_version") != 1:
        raise FeatureBuildError("unsupported normalized manifest version")
    if value.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise FeatureBuildError("unexpected normalized schema version")
    if (
        value.get("research_only") is not True
        or value.get("paper_live_enabled") is not False
        or value.get("factor_or_outcome_computed") is not False
    ):
        raise FeatureBuildError("normalized archive boundary is not outcome-blind")
    datasets = required_mapping(value, "datasets")
    for dataset_name in ("equities_bars_daily", "equities_master_month_end"):
        dataset = required_mapping(datasets, dataset_name)
        partitions = dataset.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise FeatureBuildError(f"normalized partitions missing: {dataset_name}")
        expected_rows = dataset.get("source_row_count")
        actual_rows = 0
        for record in partitions:
            if not isinstance(record, dict):
                raise FeatureBuildError(f"invalid partition record: {dataset_name}")
            relative_path = record.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                raise FeatureBuildError(f"invalid partition path: {dataset_name}")
            partition_path = normalized_dir / relative_path
            if not partition_path.is_file():
                raise FeatureBuildError(f"partition not found: {partition_path}")
            actual_hash = file_sha256(partition_path)
            if record.get("sha256") != actual_hash:
                raise FeatureBuildError(f"partition hash mismatch: {relative_path}")
            row_count = record.get("row_count")
            if isinstance(row_count, bool) or not isinstance(row_count, int):
                raise FeatureBuildError(f"invalid partition row count: {relative_path}")
            actual_rows += row_count
        if actual_rows != expected_rows:
            raise FeatureBuildError(f"partition row count mismatch: {dataset_name}")
    return value


def build_feature_cohort(
    *,
    bars: pl.DataFrame,
    master: pl.DataFrame,
    config: Mapping[str, Any],
) -> pl.DataFrame:
    validate_input_columns(bars=bars, master=master)
    universe = required_mapping(config, "universe")
    selection = required_mapping(config, "selection")
    feature_config = required_mapping(config, "feature")
    if universe.get("required_valid_adjusted_closes") != 41:
        raise FeatureBuildError("V0 requires exactly 41 valid adjusted closes")
    if universe.get("required_valid_daily_illiq_observations") != 40:
        raise FeatureBuildError("V0 requires exactly 40 price-impact observations")
    if feature_config.get("aggregation") != "ARITHMETIC_MEAN":
        raise FeatureBuildError("V0 requires arithmetic-mean price impact")
    if feature_config.get("winsorization") != "NONE":
        raise FeatureBuildError("V0 does not permit winsorization")
    if selection.get("eligible_percentile") != "TOP_20_PERCENT_DESCENDING":
        raise FeatureBuildError("V0 selection percentile drift")

    valid_bars = (
        bars.filter(
            pl.col("adjusted_close").is_not_null()
            & (pl.col("adjusted_close") > 0)
            & pl.col("turnover_jpy").is_not_null()
            & (pl.col("turnover_jpy") > 0)
        )
        .select("date", "code", "adjusted_close", "turnover_jpy")
        .sort("code", "date")
    )
    daily = (
        valid_bars.with_columns(
            (
                pl.col("adjusted_close").log()
                - pl.col("adjusted_close").shift(1).over("code").log()
            ).alias("_log_return")
        )
        .with_columns(
            (
                pl.col("_log_return").abs() / (pl.col("turnover_jpy") / pl.lit(1_000_000_000.0))
            ).alias("_daily_illiq")
        )
        .with_columns(
            pl.col("_daily_illiq")
            .rolling_mean(window_size=20, min_samples=20)
            .over("code")
            .alias("current_20_mean_daily_illiq"),
            pl.col("turnover_jpy")
            .rolling_median(window_size=20, min_samples=20)
            .over("code")
            .alias("current_20_median_turnover_jpy"),
        )
        .with_columns(
            pl.col("current_20_mean_daily_illiq")
            .shift(20)
            .over("code")
            .alias("prior_20_mean_daily_illiq")
        )
        .with_columns(
            pl.when(
                (pl.col("prior_20_mean_daily_illiq") > 0)
                & (pl.col("current_20_mean_daily_illiq") > 0)
            )
            .then(
                pl.col("prior_20_mean_daily_illiq").log()
                - pl.col("current_20_mean_daily_illiq").log()
            )
            .otherwise(None)
            .alias("liqimp1m_logdiff_v0")
        )
        .select(
            pl.col("date").alias("signal_date"),
            "code",
            "prior_20_mean_daily_illiq",
            "current_20_mean_daily_illiq",
            "current_20_median_turnover_jpy",
            "liqimp1m_logdiff_v0",
        )
    )

    product_allowlist = string_list(universe, "product_category_allowlist")
    market_allowlist = string_list(universe, "market_code_allowlist")
    minimum_turnover = positive_number(
        universe.get("minimum_current_window_median_turnover_jpy"),
        label="minimum turnover",
    )
    cohort = (
        master.rename({"as_of_date": "signal_date"})
        .join(daily, on=["signal_date", "code"], how="left")
        .with_columns(
            pl.when(~pl.col("product_category").is_in(product_allowlist))
            .then(pl.lit("DISALLOWED_PRODUCT_CATEGORY"))
            .when(~pl.col("market_code").is_in(market_allowlist))
            .then(pl.lit("DISALLOWED_MARKET"))
            .when(pl.col("liqimp1m_logdiff_v0").is_null())
            .then(pl.lit("INSUFFICIENT_VALID_FEATURE_WINDOW"))
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
            ["signal_date", "liqimp1m_logdiff_v0", "code"],
            descending=[False, True, False],
        )
        .with_columns(
            pl.int_range(1, pl.len() + 1).over("signal_date").alias("selection_rank"),
            pl.len().over("signal_date").alias("eligible_cross_section_count"),
        )
        .with_columns(
            (pl.col("eligible_cross_section_count").cast(pl.Float64) * 0.20)
            .ceil()
            .cast(pl.UInt32)
            .alias("top20_candidate_count")
        )
        .with_columns(
            (pl.col("selection_rank") <= pl.col("top20_candidate_count")).alias("top20_candidate")
        )
        .select(
            "signal_date",
            "code",
            "selection_rank",
            "eligible_cross_section_count",
            "top20_candidate_count",
            "top20_candidate",
        )
    )
    split_config = required_mapping(config, "splits")
    return (
        cohort.join(eligible_rank, on=["signal_date", "code"], how="left")
        .with_columns(
            split_expression(split_config).alias("research_split"),
            pl.col("top20_candidate").fill_null(False),
        )
        .sort("signal_date", "code")
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
        .when(
            pl.col("signal_date").is_between(
                validation_start,
                validation_end,
                closed="both",
            )
        )
        .then(pl.lit("validation"))
        .when(pl.col("signal_date").is_between(locked_start, locked_end, closed="both"))
        .then(pl.lit("locked_oos"))
        .otherwise(pl.lit("outside_registered_splits"))
    )


def build_audit(cohort: pl.DataFrame) -> dict[str, Any]:
    per_split = (
        cohort.group_by("research_split")
        .agg(
            pl.len().alias("master_rows"),
            pl.col("signal_date").n_unique().alias("signal_dates"),
            pl.col("liqimp1m_logdiff_v0").is_not_null().sum().alias("feature_available_rows"),
            pl.col("eligible").sum().alias("eligible_rows"),
            pl.col("top20_candidate").sum().alias("top20_candidate_rows"),
        )
        .sort("research_split")
    )
    per_date = (
        cohort.group_by("signal_date", "research_split")
        .agg(
            pl.len().alias("master_rows"),
            pl.col("liqimp1m_logdiff_v0").is_not_null().sum().alias("feature_available_rows"),
            pl.col("eligible").sum().alias("eligible_rows"),
            pl.col("top20_candidate").sum().alias("top20_candidate_rows"),
        )
        .sort("signal_date")
    )
    reasons = cohort.group_by("eligibility_reason").len(name="row_count").sort("eligibility_reason")
    return {
        "audit_version": 1,
        "feature_id": FEATURE_ID,
        "forward_returns_computed": False,
        "outcomes_computed": False,
        "locked_oos_outcomes_inspected": False,
        "row_count": len(cohort),
        "per_split": records_for_json(per_split),
        "per_signal_date": records_for_json(per_date),
        "eligibility_reasons": records_for_json(reasons),
    }


def build_feature_manifest(
    *,
    normalized_manifest_path: Path,
    normalized_manifest: Mapping[str, Any],
    config_path: Path,
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
        "candidate_id": "liqimp1m_logdiff_v0_research",
        "feature_id": FEATURE_ID,
        "evidence_class": "PAPER_INSPIRED_NOT_REPLICATION",
        "research_only": True,
        "paper_live_enabled": False,
        "feature_computed": True,
        "forward_returns_computed": False,
        "outcomes_computed": False,
        "locked_oos_outcomes_inspected": False,
        "inputs": {
            "config_path": str(config_path),
            "config_sha256": file_sha256(config_path),
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
            "eligible_row_count": int(cohort.get_column("eligible").sum()),
            "top20_candidate_row_count": int(cohort.get_column("top20_candidate").sum()),
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
        raise FeatureBuildError(f"normalized bars missing columns: {missing}")
    if missing := sorted(required_master - set(master.columns)):
        raise FeatureBuildError(f"normalized master missing columns: {missing}")
    if bars.select(pl.struct("date", "code").is_duplicated().sum()).item() != 0:
        raise FeatureBuildError("normalized bars contain duplicate date/code rows")
    if master.select(pl.struct("as_of_date", "code").is_duplicated().sum()).item() != 0:
        raise FeatureBuildError("normalized master contains duplicate date/code rows")


def assert_outcome_blind_columns(columns: Sequence[str]) -> None:
    for column in columns:
        if any(part in column.lower() for part in FORBIDDEN_OUTPUT_COLUMN_PARTS):
            raise FeatureBuildError(f"outcome-like column is prohibited: {column}")


def records_for_json(frame: pl.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.iter_rows(named=True):
        records.append(
            {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in row.items()
            }
        )
    return records


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"feature output already exists: {output_dir}")
    if temporary_dir.exists():
        raise FileExistsError(
            f"incomplete feature output exists; inspect it before moving it aside: {temporary_dir}"
        )


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeatureBuildError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FeatureBuildError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise FeatureBuildError(f"{label} must be a JSON object")
    return value


def required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise FeatureBuildError(f"config or manifest field must be an object: {key}")
    return nested


def required_text(value: Mapping[str, Any], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise FeatureBuildError(f"config field must be a nonempty string: {key}")
    return nested


def string_list(value: Mapping[str, Any], key: str) -> list[str]:
    nested = value.get(key)
    if (
        not isinstance(nested, list)
        or not nested
        or not all(isinstance(item, str) for item in nested)
    ):
        raise FeatureBuildError(f"config field must be a nonempty string list: {key}")
    return list(nested)


def positive_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FeatureBuildError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise FeatureBuildError(f"{label} must be positive and finite")
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
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
