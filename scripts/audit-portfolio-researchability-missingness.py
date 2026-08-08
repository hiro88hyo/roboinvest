#!/usr/bin/env python3
"""Audit registered IMOM endpoint missingness without computing performance.

The command is deliberately limited to row/value existence, historical-master
membership, and archive provenance.  It never calculates a price ratio, return,
signal, trade, or portfolio statistic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import polars as pl

AUTHORIZATION_ID = (
    "portfolio_researchability_reset_2026_v0_phase1_existing_archive_missingness_once"
)
RESET_ID = "portfolio_researchability_reset_2026_v0"
CLASSIFIER_VERSION = "portfolio_researchability_missingness_existing_archive_v0_1"
OUTPUT_SCHEMA_VERSION = "portfolio_researchability_missingness_audit_v1"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
FEATURE_SCHEMA_VERSION = "imom6m_no_skip_feature_cohort_v1"
FEATURE_FILENAME = "feature-cohort.parquet"
RESULT_FILENAME = "missingness-audit.json"
RUN_MANIFEST_FILENAME = "run-manifest.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

REASONS = (
    "DELISTING_OR_TERMINAL_EVENT",
    "TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE",
    "ISSUE_CODE_OR_SECURITY_LINEAGE_CHANGE",
    "CORPORATE_ACTION",
    "HISTORICAL_MASTER_PRODUCT_CATEGORY_MISMATCH",
    "SOURCE_API_OR_PLAN_COVERAGE_LIMITATION",
    "ARCHIVE_FETCH_OR_INGEST_FAILURE",
    "UNEXPLAINED_SOURCE_DATA_ABSENCE",
    "UNKNOWN",
)
FORBIDDEN_OUTPUT_KEY_PARTS = (
    "symbol_return",
    "portfolio_return",
    "decile_return",
    "rank_ic",
    "pnl",
    "profit_factor",
    "drawdown",
    "price_value",
    "trade_pnl",
)


class MissingnessAuditError(ValueError):
    """Raised when Phase 1 authority, integrity, or audit invariants fail."""


def main() -> int:
    args = build_parser().parse_args()
    run_audit(
        normalized_dir=args.normalized_dir,
        feature_dir=args.feature_dir,
        authorization_path=args.authorization,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--feature-dir", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def run_audit(
    *,
    normalized_dir: Path,
    feature_dir: Path,
    authorization_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    authorization = load_and_verify_authorization(
        authorization_path,
        output_dir=output_dir,
    )
    bound = verify_bound_inputs(authorization)
    raw_manifest = load_json_object(bound["raw_archive_manifest"], label="raw manifest")
    normalized_manifest = load_json_object(
        bound["normalized_manifest"], label="normalized manifest"
    )
    validation_report = load_json_object(
        bound["normalization_validation_report"], label="normalization validation report"
    )
    feature_manifest = load_json_object(bound["feature_manifest"], label="feature manifest")
    gate_result = load_json_object(bound["gate_a_result"], label="Gate A result")

    bar_partitions, master_partitions = verify_archive_integrity(
        raw_manifest=raw_manifest,
        raw_manifest_path=bound["raw_archive_manifest"],
        normalized_manifest=normalized_manifest,
        validation_report=validation_report,
        normalized_dir=normalized_dir,
    )
    feature_path = verify_feature_artifact(
        feature_dir=feature_dir,
        feature_manifest=feature_manifest,
    )
    contract = required_mapping(authorization, "preexecution_contract")
    features, calendar = load_development_feature_scope(feature_path, gate_result=gate_result)
    attempted_dates, date_pairs = attempted_formation_dates(
        features=features,
        calendar=calendar,
        split_start=date.fromisoformat(required_text(gate_result, "split_start")),
        split_end=date.fromisoformat(required_text(gate_result, "split_end")),
    )
    expected_attempted = required_int(contract, "expected_attempted_formation_month_count")
    if len(attempted_dates) != expected_attempted:
        raise MissingnessAuditError(
            f"attempted formation count drifted: {len(attempted_dates)} != {expected_attempted}"
        )

    endpoint_dates = sorted(set(attempted_dates) | {date_pairs[item] for item in attempted_dates})
    selected_bar_paths = unique_paths_for_dates(endpoint_dates, bar_partitions)
    endpoint_bars = load_endpoint_bars(selected_bar_paths, endpoint_dates=endpoint_dates)
    assert_unique_keys(endpoint_bars, ("date", "code"), label="endpoint bars")
    date_provenance = build_date_provenance(endpoint_bars, endpoint_dates=endpoint_dates)
    missing = reconstruct_missing_cases(
        features=features,
        attempted_dates=attempted_dates,
        date_pairs=date_pairs,
        endpoint_bars=endpoint_bars,
    )
    verify_missing_set(missing=missing, gate_result=gate_result, contract=contract)

    missing_codes = sorted({required_text(row, "code") for row in missing})
    master_dates = sorted(
        {
            value
            for row in missing
            for value in (
                date.fromisoformat(required_text(row, "formation_date")),
                date.fromisoformat(required_text(row, "outcome_date")),
            )
        }
    )
    selected_master_paths = unique_paths_for_dates(master_dates, master_partitions)
    masters = load_relevant_masters(
        selected_master_paths,
        master_dates=master_dates,
        missing_codes=missing_codes,
    )
    assert_unique_keys(masters, ("as_of_date", "code"), label="historical masters")
    all_missing_code_bars = load_missing_code_bars(
        [record["absolute_path"] for record in bar_partitions],
        missing_codes=missing_codes,
    )
    assert_unique_keys(all_missing_code_bars, ("date", "code"), label="missing-code bars")

    cases = classify_cases(
        missing=missing,
        endpoint_bars=endpoint_bars,
        date_provenance=date_provenance,
        masters=masters,
        missing_code_bars=all_missing_code_bars,
        bar_partitions=bar_partitions,
        master_partitions=master_partitions,
        raw_manifest=raw_manifest,
        raw_manifest_path=bound["raw_archive_manifest"],
    )
    reason_counts = Counter(required_text(row, "reason") for row in cases)
    reason_count_map = {reason: reason_counts.get(reason, 0) for reason in REASONS}
    terminal_contract = {
        "explicit_terminal_event_dataset_bound": False,
        "explicit_security_lineage_dataset_bound": False,
        "explicit_corporate_action_event_dataset_bound": False,
        "terminal_outcome_handling_point_in_time_reproducible": False,
        "limitation": (
            "The bound archive has daily bars, adjustment factors, and monthly listed-info "
            "snapshots, but no explicit delisting, merger, share-exchange, code-lineage, cash "
            "consideration, or terminal-outcome event table with effective dates."
        ),
    }
    unknown_or_unexplained = (
        reason_count_map["UNKNOWN"] + reason_count_map["UNEXPLAINED_SOURCE_DATA_ABSENCE"]
    )
    no_go_reasons: list[str] = []
    if unknown_or_unexplained:
        no_go_reasons.append("UNKNOWN_OR_UNEXPLAINED_SOURCE_DATA_ABSENCE_REMAINS")
    if not terminal_contract["terminal_outcome_handling_point_in_time_reproducible"]:
        no_go_reasons.append("TERMINAL_OUTCOME_CONTRACT_NOT_POINT_IN_TIME_REPRODUCIBLE")
    decision = (
        "NO_GO_CURRENT_ARCHIVE_FOR_ALL_UNIVERSE_CROSS_SECTIONAL_RESEARCH"
        if no_go_reasons
        else "GO_DATA_FOUNDATION_ONLY_NO_STRATEGY_AUTHORITY"
    )
    monthly_counts = monthly_missing_counts(cases)
    audit = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "reset_id": RESET_ID,
        "phase": 1,
        "identity": "NON_ALPHA_NON_STRATEGY_CANDIDATE_DATA_RESEARCHABILITY_AUDIT",
        "classifier_version": CLASSIFIER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "external_or_additional_data_fetched": False,
        "performance_values_computed": False,
        "gate_a_recomputed": False,
        "validation_or_locked_oos_inspected": False,
        "source_identity": {
            "api_base": required_mapping(raw_manifest, "jquants").get("api_base"),
            "api_version": required_mapping(raw_manifest, "jquants").get("api_version"),
            "declared_plan": required_mapping(raw_manifest, "jquants").get("declared_plan"),
            "bars_client_method": "daily_quotes(target_date=exact_date)",
            "master_client_method": "listed_info(as_of=exact_month_end)",
            "archive_source_fidelity": raw_manifest.get("source_fidelity"),
        },
        "counts": {
            "attempted_formation_month_count": len(attempted_dates),
            "incomplete_formation_month_count": len(monthly_counts),
            "missing_case_count": len(cases),
            "by_reason": reason_count_map,
        },
        "monthly_missing_counts": monthly_counts,
        "terminal_outcome_contract": terminal_contract,
        "decision": decision,
        "decision_reasons": no_go_reasons,
        "authority_boundary": {
            "strategy_candidate_created": False,
            "phase2_or_later_started": False,
            "paper_or_live_modified": False,
            "next_action": (
                "Stop with the current archive at Phase 1. Any additional source acquisition "
                "or Phase 2 requires separate explicit authorization."
            ),
        },
        "cases": cases,
    }
    assert_no_prohibited_output_keys(audit)

    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    temporary_dir.mkdir(parents=True)
    result_path = temporary_dir / RESULT_FILENAME
    write_json(result_path, audit)
    run_manifest = build_run_manifest(
        authorization_path=authorization_path,
        authorization=authorization,
        bound=bound,
        normalized_manifest=normalized_manifest,
        feature_manifest=feature_manifest,
        result_path=result_path,
        audit=audit,
    )
    write_json(temporary_dir / RUN_MANIFEST_FILENAME, run_manifest)
    temporary_dir.rename(output_dir)
    return audit


def load_and_verify_authorization(
    path: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    value = load_json_object(path, label="Phase 1 authorization")
    if (
        value.get("authorization_id") != AUTHORIZATION_ID
        or value.get("reset_id") != RESET_ID
        or value.get("phase") != 1
    ):
        raise MissingnessAuditError("unexpected Phase 1 authorization identity")
    if (
        value.get("research_only") is not True
        or value.get("paper_live_enabled") is not False
        or value.get("counts_as_2026_09_30_kill_switch_evidence") is not False
    ):
        raise MissingnessAuditError("authorization research boundary is open")
    expected_scope = {
        "implement_deterministic_missingness_classifier": True,
        "run_synthetic_classifier_tests": True,
        "reconstruct_registered_57_missing_symbol_outcomes_once": True,
        "inspect_existing_archive_row_and_value_existence": True,
        "inspect_existing_historical_master_membership_and_descriptors": True,
        "inspect_existing_archive_receipt_and_partition_provenance": True,
        "write_phase1_audit_once": True,
        "fetch_external_or_additional_data": False,
        "compute_or_persist_symbol_returns": False,
        "compute_or_persist_portfolio_returns": False,
        "recompute_gate_a_or_complete_case_metrics": False,
        "compute_rank_ic_pnl_profit_factor_or_drawdown": False,
        "create_or_compare_strategy_candidates": False,
        "inspect_validation_or_locked_oos_outcomes": False,
        "modify_paper_live_watchlist_gateway_oms_supabase_or_pubsub": False,
        "start_phase2_or_later": False,
    }
    scope = required_mapping(value, "scope")
    for key, expected in expected_scope.items():
        if scope.get(key) is not expected:
            raise MissingnessAuditError(f"authorization scope drifted: {key}")
    expected_output = scope.get("expected_output_dir")
    if (
        not isinstance(expected_output, str)
        or Path(expected_output).resolve() != output_dir.resolve()
    ):
        raise MissingnessAuditError("output path differs from Phase 1 authorization")
    contract = required_mapping(value, "preexecution_contract")
    if contract.get("classifier_version") != CLASSIFIER_VERSION:
        raise MissingnessAuditError("classifier version drifted")
    if (
        required_int(contract, "expected_attempted_formation_month_count") != 28
        or required_int(contract, "expected_incomplete_formation_month_count") != 23
        or required_int(contract, "expected_missing_case_count") != 57
    ):
        raise MissingnessAuditError("registered missing-set counts drifted")
    precedence = contract.get("classification_precedence")
    expected_precedence = [
        "ARCHIVE_FETCH_OR_INGEST_FAILURE",
        "TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE",
        "HISTORICAL_MASTER_PRODUCT_CATEGORY_MISMATCH",
        "UNEXPLAINED_SOURCE_DATA_ABSENCE",
        "UNKNOWN",
    ]
    if (
        not isinstance(precedence, list)
        or [row.get("reason") for row in precedence] != expected_precedence
    ):
        raise MissingnessAuditError("classification precedence drifted")
    return value


def verify_bound_inputs(authorization: Mapping[str, Any]) -> dict[str, Path]:
    records = required_mapping(authorization, "bound_inputs")
    expected_labels = {
        "reset_plan",
        "cycle_closure",
        "imom_disposition",
        "gate_a_result",
        "gate_a_run_manifest",
        "imom_config",
        "feature_manifest",
        "raw_archive_manifest",
        "normalized_manifest",
        "normalization_validation_report",
    }
    if set(records) != expected_labels:
        raise MissingnessAuditError("bound input set drifted")
    paths: dict[str, Path] = {}
    for label in sorted(expected_labels):
        record = required_mapping(records, label)
        path = bound_repository_path(record, label=label)
        if not path.is_file():
            raise MissingnessAuditError(f"bound input is missing: {label}")
        if record.get("sha256") != file_sha256(path):
            raise MissingnessAuditError(f"bound input hash mismatch: {label}")
        paths[label] = path
    return paths


def verify_archive_integrity(
    *,
    raw_manifest: Mapping[str, Any],
    raw_manifest_path: Path,
    normalized_manifest: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    normalized_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if normalized_manifest.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise MissingnessAuditError("unexpected normalized archive schema")
    if (
        normalized_manifest.get("research_only") is not True
        or normalized_manifest.get("paper_live_enabled") is not False
        or normalized_manifest.get("factor_or_outcome_computed") is not False
        or validation_report.get("status") != "PASS"
        or validation_report.get("factor_or_outcome_computed") is not False
    ):
        raise MissingnessAuditError("normalized archive boundary or validation drifted")
    if validation_report.get("input_manifest_sha256") != file_sha256(raw_manifest_path):
        raise MissingnessAuditError("validation report raw-manifest binding drifted")
    raw_files = required_mapping(raw_manifest, "files")
    raw_root = raw_manifest_path.parent
    for filename in ("bars-daily-raw.jsonl", "master-month-end-raw.jsonl"):
        record = required_mapping(raw_files, filename)
        path = raw_root / filename
        if (
            not path.is_file()
            or record.get("byte_size") != path.stat().st_size
            or record.get("sha256") != file_sha256(path)
        ):
            raise MissingnessAuditError(f"raw archive artifact mismatch: {filename}")

    datasets = required_mapping(normalized_manifest, "datasets")
    bars = required_mapping(datasets, "equities_bars_daily")
    masters = required_mapping(datasets, "equities_master_month_end")
    raw_bars = required_mapping(raw_files, "bars-daily-raw.jsonl")
    raw_masters = required_mapping(raw_files, "master-month-end-raw.jsonl")
    if bars.get("source_row_count") != raw_bars.get("source_row_count"):
        raise MissingnessAuditError("raw/normalized bar row counts differ")
    if masters.get("source_row_count") != raw_masters.get("source_row_count"):
        raise MissingnessAuditError("raw/normalized master row counts differ")
    bar_partitions = verify_partition_records(
        bars,
        normalized_dir=normalized_dir,
        label="bar",
    )
    master_partitions = verify_partition_records(
        masters,
        normalized_dir=normalized_dir,
        label="master",
    )
    return bar_partitions, master_partitions


def verify_partition_records(
    dataset: Mapping[str, Any],
    *,
    normalized_dir: Path,
    label: str,
) -> list[dict[str, Any]]:
    raw_records = dataset.get("partitions")
    if not isinstance(raw_records, list) or not raw_records:
        raise MissingnessAuditError(f"normalized {label} partitions are missing")
    records: list[dict[str, Any]] = []
    row_count = 0
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise MissingnessAuditError(f"invalid {label} partition record")
        relative = raw.get("path")
        if not isinstance(relative, str) or not relative:
            raise MissingnessAuditError(f"invalid {label} partition path")
        path = normalized_dir / relative
        expected_rows = raw.get("row_count")
        if isinstance(expected_rows, bool) or not isinstance(expected_rows, int):
            raise MissingnessAuditError(f"invalid {label} partition row count")
        if not path.is_file() or raw.get("sha256") != file_sha256(path):
            raise MissingnessAuditError(f"normalized {label} partition mismatch: {relative}")
        if pl.scan_parquet(path).select(pl.len()).collect().item() != expected_rows:
            raise MissingnessAuditError(f"normalized {label} partition row count mismatch")
        row_count += expected_rows
        records.append({**raw, "absolute_path": path})
    if row_count != dataset.get("source_row_count"):
        raise MissingnessAuditError(f"normalized {label} total row count mismatch")
    return records


def verify_feature_artifact(
    *,
    feature_dir: Path,
    feature_manifest: Mapping[str, Any],
) -> Path:
    if (
        feature_manifest.get("output_schema_version") != FEATURE_SCHEMA_VERSION
        or feature_manifest.get("research_only") is not True
        or feature_manifest.get("paper_live_enabled") is not False
        or feature_manifest.get("next_month_returns_computed") is not False
    ):
        raise MissingnessAuditError("feature artifact boundary drifted")
    record = required_mapping(feature_manifest, "feature_file")
    if record.get("path") != FEATURE_FILENAME:
        raise MissingnessAuditError("unexpected feature filename")
    path = feature_dir / FEATURE_FILENAME
    if (
        not path.is_file()
        or record.get("sha256") != file_sha256(path)
        or record.get("row_count") != pl.scan_parquet(path).select(pl.len()).collect().item()
    ):
        raise MissingnessAuditError("feature artifact integrity failed")
    return path


def load_development_feature_scope(
    path: Path,
    *,
    gate_result: Mapping[str, Any],
) -> tuple[pl.DataFrame, list[date]]:
    split_start = date.fromisoformat(required_text(gate_result, "split_start"))
    split_end = date.fromisoformat(required_text(gate_result, "split_end"))
    columns = ["signal_date", "code", "imom6m_no_skip_v0", "eligible", "research_split"]
    schema = pl.read_parquet_schema(path)
    if missing := sorted(set(columns) - set(schema)):
        raise MissingnessAuditError(f"feature cohort columns missing: {missing}")
    frame = (
        pl.scan_parquet(path)
        .select(columns)
        .filter(
            (pl.col("research_split") == "development")
            & pl.col("signal_date").is_between(split_start, split_end, closed="both")
        )
        .collect()
    )
    assert_unique_keys(frame, ("signal_date", "code"), label="development features")
    calendar = sorted(frame.get_column("signal_date").unique().to_list())
    return frame, calendar


def attempted_formation_dates(
    *,
    features: pl.DataFrame,
    calendar: Sequence[date],
    split_start: date,
    split_end: date,
) -> tuple[list[date], dict[date, date]]:
    ordered = sorted(set(calendar))
    pairs = {
        formation: outcome
        for formation, outcome in pairwise(ordered)
        if split_start <= formation <= split_end and outcome <= split_end
    }
    available = set(
        features.filter(pl.col("imom6m_no_skip_v0").is_not_null())
        .get_column("signal_date")
        .unique()
        .to_list()
    )
    attempted = sorted(available & set(pairs))
    if not attempted:
        raise MissingnessAuditError("no registered development formations found")
    return attempted, pairs


def load_endpoint_bars(paths: Sequence[Path], *, endpoint_dates: Sequence[date]) -> pl.DataFrame:
    columns = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjusted_volume",
        "source_fetch_id",
        "source_received_at",
    ]
    return (
        pl.scan_parquet(paths)
        .select(columns)
        .filter(pl.col("date").is_in(endpoint_dates))
        .collect()
        .sort("date", "code")
    )


def reconstruct_missing_cases(
    *,
    features: pl.DataFrame,
    attempted_dates: Sequence[date],
    date_pairs: Mapping[date, date],
    endpoint_bars: pl.DataFrame,
) -> list[dict[str, Any]]:
    bar_by_key = {(row["date"], row["code"]): row for row in endpoint_bars.iter_rows(named=True)}
    missing: list[dict[str, Any]] = []
    attempted_set = set(attempted_dates)
    eligible = features.filter(
        pl.col("eligible") & pl.col("signal_date").is_in(attempted_dates)
    ).sort("signal_date", "code")
    for row in eligible.iter_rows(named=True):
        formation = row["signal_date"]
        if formation not in attempted_set:
            continue
        outcome = date_pairs[formation]
        formation_bar = bar_by_key.get((formation, row["code"]))
        outcome_bar = bar_by_key.get((outcome, row["code"]))
        formation_valid = formation_bar is not None and valid_positive_number(
            formation_bar.get("adjusted_close")
        )
        outcome_valid = outcome_bar is not None and valid_positive_number(
            outcome_bar.get("adjusted_close")
        )
        if not formation_valid or not outcome_valid:
            missing.append(
                {
                    "formation_date": formation.isoformat(),
                    "outcome_date": outcome.isoformat(),
                    "code": row["code"],
                    "formation_endpoint_valid": formation_valid,
                    "outcome_endpoint_valid": outcome_valid,
                }
            )
    return missing


def verify_missing_set(
    *,
    missing: Sequence[Mapping[str, Any]],
    gate_result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    if len(missing) != required_int(contract, "expected_missing_case_count"):
        raise MissingnessAuditError("registered missing-case count did not reproduce")
    if any(row.get("formation_endpoint_valid") is not True for row in missing):
        raise MissingnessAuditError("a missing case has an invalid formation endpoint")
    counts = Counter(required_text(row, "formation_date") for row in missing)
    if len(counts) != required_int(contract, "expected_incomplete_formation_month_count"):
        raise MissingnessAuditError("registered incomplete-month count did not reproduce")
    diagnostics = gate_result.get("monthly_diagnostics")
    if not isinstance(diagnostics, list):
        raise MissingnessAuditError("Gate A monthly diagnostics missing")
    expected = {
        required_text(row, "formation_date"): required_int(row, "missing_outcome_count")
        for row in diagnostics
        if isinstance(row, dict) and required_int(row, "missing_outcome_count") > 0
    }
    if dict(sorted(counts.items())) != dict(sorted(expected.items())):
        raise MissingnessAuditError("missing-set monthly counts differ from frozen Gate A result")


def build_date_provenance(
    endpoint_bars: pl.DataFrame,
    *,
    endpoint_dates: Sequence[date],
) -> dict[date, dict[str, str] | None]:
    provenance: dict[date, dict[str, str] | None] = {}
    for target in endpoint_dates:
        rows = endpoint_bars.filter(pl.col("date") == target)
        if rows.is_empty():
            provenance[target] = None
            continue
        fetch_ids = rows.get_column("source_fetch_id").unique().to_list()
        receipts = rows.get_column("source_received_at").unique().to_list()
        if len(fetch_ids) != 1 or len(receipts) != 1:
            raise MissingnessAuditError(f"date-level source provenance is not unique: {target}")
        provenance[target] = {
            "source_fetch_id": str(fetch_ids[0]),
            "source_received_at": receipts[0].isoformat(),
        }
    return provenance


def load_relevant_masters(
    paths: Sequence[Path],
    *,
    master_dates: Sequence[date],
    missing_codes: Sequence[str],
) -> pl.DataFrame:
    return (
        pl.scan_parquet(paths)
        .select(
            "as_of_date",
            "code",
            "product_category",
            "company_name",
            "company_name_en",
            "source_fetch_id",
            "source_received_at",
        )
        .filter(pl.col("as_of_date").is_in(master_dates) & pl.col("code").is_in(missing_codes))
        .collect()
        .sort("as_of_date", "code")
    )


def load_missing_code_bars(paths: Sequence[Path], *, missing_codes: Sequence[str]) -> pl.DataFrame:
    return (
        pl.scan_parquet(paths)
        .select("date", "code", "adjusted_close")
        .filter(pl.col("code").is_in(missing_codes))
        .collect()
        .sort("code", "date")
    )


def classify_cases(
    *,
    missing: Sequence[Mapping[str, Any]],
    endpoint_bars: pl.DataFrame,
    date_provenance: Mapping[date, Mapping[str, str] | None],
    masters: pl.DataFrame,
    missing_code_bars: pl.DataFrame,
    bar_partitions: Sequence[Mapping[str, Any]],
    master_partitions: Sequence[Mapping[str, Any]],
    raw_manifest: Mapping[str, Any],
    raw_manifest_path: Path,
) -> list[dict[str, Any]]:
    bar_by_key = {(row["date"], row["code"]): row for row in endpoint_bars.iter_rows(named=True)}
    master_by_key = {(row["as_of_date"], row["code"]): row for row in masters.iter_rows(named=True)}
    valid_dates_by_code: dict[str, list[date]] = {}
    for code, rows in missing_code_bars.group_by("code", maintain_order=True):
        code_value = code[0] if isinstance(code, tuple) else code
        valid_dates_by_code[str(code_value)] = [
            row["date"]
            for row in rows.iter_rows(named=True)
            if valid_positive_number(row["adjusted_close"])
        ]
    raw_files = required_mapping(raw_manifest, "files")
    raw_bar_record = required_mapping(raw_files, "bars-daily-raw.jsonl")
    cases: list[dict[str, Any]] = []
    for missing_row in missing:
        formation = date.fromisoformat(required_text(missing_row, "formation_date"))
        outcome = date.fromisoformat(required_text(missing_row, "outcome_date"))
        code = required_text(missing_row, "code")
        formation_bar = bar_by_key.get((formation, code))
        outcome_bar = bar_by_key.get((outcome, code))
        formation_master = master_by_key.get((formation, code))
        outcome_master = master_by_key.get((outcome, code))
        if formation_master is None:
            raise MissingnessAuditError(
                f"formation historical master row missing: {formation}/{code}"
            )
        formation_product = formation_master["product_category"]
        outcome_product = outcome_master["product_category"] if outcome_master else None
        reason = classify_reason(
            outcome_date_fetch_available=date_provenance.get(outcome) is not None,
            outcome_row_exists=outcome_bar is not None,
            outcome_ohlcv_state=ohlcv_state(outcome_bar),
            outcome_master_exists=outcome_master is not None,
            formation_product_category=str(formation_product),
            outcome_product_category=(
                str(outcome_product) if outcome_product is not None else None
            ),
        )
        prior_date, later_date = nearest_valid_dates(
            valid_dates_by_code.get(code, []),
            target=outcome,
        )
        formation_partition = partition_for_date(formation, bar_partitions)
        outcome_partition = partition_for_date(outcome, bar_partitions)
        formation_master_partition = partition_for_date(formation, master_partitions)
        outcome_master_partition = partition_for_date(outcome, master_partitions)
        cases.append(
            {
                "case_id": f"{formation.isoformat()}_{outcome.isoformat()}_{code}",
                "formation_date": formation.isoformat(),
                "outcome_date": outcome.isoformat(),
                "code": code,
                "reason": reason,
                "formation_evidence": endpoint_evidence(formation_bar),
                "outcome_evidence": endpoint_evidence(outcome_bar),
                "historical_master_evidence": {
                    "formation_member": True,
                    "formation_product_category": formation_product,
                    "outcome_member_same_code": outcome_master is not None,
                    "outcome_product_category": outcome_product,
                    "product_category_unchanged": (
                        outcome_master is not None and outcome_product == formation_product
                    ),
                    "company_name_unchanged": (
                        outcome_master is not None
                        and outcome_master["company_name"] == formation_master["company_name"]
                        and outcome_master["company_name_en"] == formation_master["company_name_en"]
                    ),
                    "formation_source_fetch_id": formation_master["source_fetch_id"],
                    "formation_source_received_at": formation_master[
                        "source_received_at"
                    ].isoformat(),
                    "outcome_source_fetch_id": (
                        outcome_master["source_fetch_id"] if outcome_master is not None else None
                    ),
                    "outcome_source_received_at": (
                        outcome_master["source_received_at"].isoformat()
                        if outcome_master is not None
                        else None
                    ),
                },
                "temporal_nonperformance_evidence": {
                    "nearest_prior_valid_bar_date": prior_date.isoformat() if prior_date else None,
                    "nearest_later_valid_bar_date": later_date.isoformat() if later_date else None,
                },
                "source_provenance": {
                    "outcome_date_fetch": date_provenance.get(outcome),
                    "raw_bars_artifact": {
                        "path": str(raw_manifest_path.parent / "bars-daily-raw.jsonl"),
                        "sha256": raw_bar_record.get("sha256"),
                    },
                    "formation_bar_partition": public_partition_record(formation_partition),
                    "outcome_bar_partition": public_partition_record(outcome_partition),
                    "formation_master_partition": public_partition_record(
                        formation_master_partition
                    ),
                    "outcome_master_partition": public_partition_record(outcome_master_partition),
                },
                "explicit_reason_metadata": {
                    "terminal_event_available": False,
                    "security_lineage_available": False,
                    "corporate_action_event_available": False,
                    "source_plan_limitation_available": False,
                    "classification_limit": (
                        "No explicit bound event/lineage/limitation dataset; disappearance and "
                        "adjustment factors are not substituted for reason metadata."
                    ),
                },
            }
        )
    return sorted(cases, key=lambda row: (row["formation_date"], row["code"]))


def classify_reason(
    *,
    outcome_date_fetch_available: bool,
    outcome_row_exists: bool,
    outcome_ohlcv_state: str,
    outcome_master_exists: bool,
    formation_product_category: str,
    outcome_product_category: str | None,
) -> str:
    if not outcome_date_fetch_available:
        return "ARCHIVE_FETCH_OR_INGEST_FAILURE"
    if outcome_row_exists and outcome_ohlcv_state == "COMPLETE_NULL":
        return "TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE"
    if (
        not outcome_row_exists
        and outcome_master_exists
        and outcome_product_category != formation_product_category
    ):
        return "HISTORICAL_MASTER_PRODUCT_CATEGORY_MISMATCH"
    if (
        not outcome_row_exists
        and outcome_master_exists
        and outcome_product_category == formation_product_category
    ):
        return "UNEXPLAINED_SOURCE_DATA_ABSENCE"
    return "UNKNOWN"


def endpoint_evidence(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {
            "source_row_exists": False,
            "adjusted_close_state": "ABSENT",
            "ohlcv_state": "ABSENT",
            "source_fetch_id": None,
            "source_received_at": None,
        }
    return {
        "source_row_exists": True,
        "adjusted_close_state": numeric_state(row.get("adjusted_close")),
        "ohlcv_state": ohlcv_state(row),
        "source_fetch_id": row.get("source_fetch_id"),
        "source_received_at": row["source_received_at"].isoformat(),
    }


def ohlcv_state(row: Mapping[str, Any] | None) -> str:
    if row is None:
        return "ABSENT"
    fields = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjusted_volume",
    )
    values = [row.get(field) for field in fields]
    if all(value is None for value in values):
        return "COMPLETE_NULL"
    if all(valid_positive_number(value) for value in values):
        return "COMPLETE_POSITIVE_FINITE"
    return "PARTIAL_OR_INVALID"


def numeric_state(value: Any) -> str:
    if value is None:
        return "NULL"
    if valid_positive_number(value):
        return "POSITIVE_FINITE"
    return "NONPOSITIVE_OR_NONFINITE"


def nearest_valid_dates(values: Sequence[date], *, target: date) -> tuple[date | None, date | None]:
    ordered = sorted(set(values))
    prior_index = bisect_left(ordered, target) - 1
    later_index = bisect_right(ordered, target)
    prior = ordered[prior_index] if prior_index >= 0 else None
    later = ordered[later_index] if later_index < len(ordered) else None
    return prior, later


def monthly_missing_counts(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter(
        (required_text(row, "formation_date"), required_text(row, "outcome_date")) for row in cases
    )
    return [
        {
            "formation_date": formation,
            "outcome_date": outcome,
            "missing_case_count": count,
        }
        for (formation, outcome), count in sorted(counts.items())
    ]


def unique_paths_for_dates(
    dates: Sequence[date],
    partitions: Sequence[Mapping[str, Any]],
) -> list[Path]:
    paths: dict[str, Path] = {}
    for target in dates:
        record = partition_for_date(target, partitions)
        path = record.get("absolute_path")
        if not isinstance(path, Path):
            raise MissingnessAuditError(f"partition absolute path missing: {target}")
        paths[str(path)] = path
    return [paths[key] for key in sorted(paths)]


def partition_for_date(
    target: date,
    partitions: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    matches = [
        record
        for record in partitions
        if date.fromisoformat(str(record["first_date"]))
        <= target
        <= date.fromisoformat(str(record["last_date"]))
    ]
    if len(matches) != 1:
        raise MissingnessAuditError(f"expected one partition for date {target}, got {len(matches)}")
    return matches[0]


def public_partition_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": record.get("path"),
        "sha256": record.get("sha256"),
        "first_date": record.get("first_date"),
        "last_date": record.get("last_date"),
    }


def build_run_manifest(
    *,
    authorization_path: Path,
    authorization: Mapping[str, Any],
    bound: Mapping[str, Path],
    normalized_manifest: Mapping[str, Any],
    feature_manifest: Mapping[str, Any],
    result_path: Path,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "auditor_sha256": file_sha256(Path(__file__)),
        "authorization": {
            "id": authorization.get("authorization_id"),
            "path": repository_relative(authorization_path),
            "sha256": file_sha256(authorization_path),
        },
        "bound_inputs": {
            label: {"path": repository_relative(path), "sha256": file_sha256(path)}
            for label, path in sorted(bound.items())
        },
        "normalized_schema_version": normalized_manifest.get("normalized_schema_version"),
        "feature_schema_version": feature_manifest.get("output_schema_version"),
        "classifier_version": audit.get("classifier_version"),
        "result": {
            "path": RESULT_FILENAME,
            "sha256": file_sha256(result_path),
            "missing_case_count": required_mapping(audit, "counts").get("missing_case_count"),
            "decision": audit.get("decision"),
        },
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
    }


def assert_no_prohibited_output_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = key.lower()
            if any(part in lowered for part in FORBIDDEN_OUTPUT_KEY_PARTS):
                raise MissingnessAuditError(f"prohibited performance-like output key: {path}.{key}")
            assert_no_prohibited_output_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            assert_no_prohibited_output_keys(nested, path=f"{path}[{index}]")


def assert_unique_keys(frame: pl.DataFrame, columns: Sequence[str], *, label: str) -> None:
    if frame.select(pl.struct(list(columns)).is_duplicated().any()).item():
        raise MissingnessAuditError(f"{label} contains duplicate keys")


def valid_positive_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    for path in (output_dir, temporary_dir):
        if path.exists():
            raise FileExistsError(f"Phase 1 output path already exists: {path}")


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingnessAuditError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise MissingnessAuditError(f"{label} must be a JSON object")
    return value


def required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise MissingnessAuditError(f"missing object: {key}")
    return nested


def required_text(value: Mapping[str, Any], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise MissingnessAuditError(f"missing text: {key}")
    return nested


def required_int(value: Mapping[str, Any], key: str) -> int:
    nested = value.get(key)
    if isinstance(nested, bool) or not isinstance(nested, int):
        raise MissingnessAuditError(f"missing integer: {key}")
    return nested


def bound_repository_path(value: Mapping[str, Any], *, label: str) -> Path:
    relative = Path(required_text(value, "path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise MissingnessAuditError(f"authorization/{label} path is not repository-relative")
    resolved = (REPOSITORY_ROOT / relative).resolve()
    if not resolved.is_relative_to(REPOSITORY_ROOT):
        raise MissingnessAuditError(f"authorization/{label} path escapes repository")
    return resolved


def repository_relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPOSITORY_ROOT))


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
