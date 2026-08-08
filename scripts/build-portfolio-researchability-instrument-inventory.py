#!/usr/bin/env python3
"""Build the authorized Phase 2 instrument/data inventory without strategy analysis.

The command binds official source artifacts to the exact 2026-06-30 historical
master snapshot, describes non-performance data coverage, and stops at the Phase 3
boundary.  It never reads price columns, calculates outcomes, ranks instruments, or
calls a broker endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import polars as pl

AUTHORIZATION_ID = (
    "portfolio_researchability_reset_2026_v0_phase2_investable_instrument_inventory_once"
)
RESET_ID = "portfolio_researchability_reset_2026_v0"
AUTHORIZED_BUILDER_VERSION = "portfolio_researchability_instrument_inventory_v0_1"
BUILDER_VERSION = "portfolio_researchability_instrument_inventory_v0_3"
OUTPUT_SCHEMA_VERSION = "portfolio_researchability_instrument_inventory_v3"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
SOURCE_SET_ID = "portfolio_researchability_reset_2026_v0_phase2_primary_sources_v0"
SEMANTIC_CORRECTION_ID = (
    "portfolio_researchability_reset_2026_v0_phase2_semantic_classification_correction"
)
POSTREVIEW_CORRECTION_ID = (
    "portfolio_researchability_reset_2026_v0_phase2_postreview_semantic_correction"
)
SNAPSHOT_DATE = date(2026, 6, 30)
ETF_PRODUCT_CATEGORY = "014"
ORDINARY_EQUITY_PRODUCT_CATEGORY = "011"
INVENTORY_FILENAME = "instrument-inventory.json"
RUN_MANIFEST_FILENAME = "run-manifest.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_CORRECTION_PATH = (
    REPOSITORY_ROOT
    / "research/portfolio-researchability-reset-2026-v0/phase2-semantic-correction-record.json"
)
POSTREVIEW_CORRECTION_PATH = (
    REPOSITORY_ROOT / "research/portfolio-researchability-reset-2026-v0/"
    "phase2-postreview-semantic-correction-record.json"
)

CLASS_MARKET_CATEGORY = "JPX_JAPANESE_EQUITY_MARKET_CATEGORY_ETF"
CLASS_SECTOR = "JAPAN_EQUITY_SECTOR_OR_INDUSTRY_INDEX"
CLASS_SHORT_JGB = "JPY_JAPAN_GOVERNMENT_BOND_0_1Y_ETF"
CLASS_IDS = (CLASS_MARKET_CATEGORY, CLASS_SECTOR, CLASS_SHORT_JGB)

EXPECTED_SCOPE = {
    "discover_and_read_primary_public_sources": True,
    "capture_downloadable_primary_source_artifacts_once": True,
    "implement_deterministic_inventory_builder": True,
    "run_synthetic_inventory_tests": True,
    "build_instrument_inventory_once": True,
    "inspect_bound_local_master_and_ohlcv_nonperformance_fields": True,
    "compute_descriptive_data_coverage_and_tradability_fields": True,
    "use_secondary_sources_or_advisory_articles_as_evidence": False,
    "call_live_or_test_broker_quote_board_or_order_endpoints": False,
    "compute_or_persist_returns_signals_or_portfolio_performance": False,
    "rank_or_recommend_instruments": False,
    "select_instruments_by_fee_liquidity_or_historical_outcome": False,
    "create_or_compare_strategy_candidates": False,
    "modify_project_kill_switch": False,
    "modify_paper_live_watchlist_gateway_oms_supabase_or_pubsub": False,
    "start_phase3_or_later": False,
}
ALLOWED_SOURCE_DOMAINS = {
    "jpx.co.jp",
    "www.jpx.co.jp",
    "jpx-jquants.com",
    "www.jpx-jquants.com",
    "kabu.com",
    "www.kabu.com",
    "kabucom.github.io",
}
GEARED_TOKENS = (
    "LEVERAGED",
    "INVERSE",
    "DOUBLE INVERSE",
    "DOUBLE-INVERSE",
    "BEAR",
    "-2X",
    "-1X",
    "2X ",
)
MARKET_CATEGORY_EXCLUSION_TOKENS = (
    "ACTIVE",
    "HIGH DIVIDEND",
    "DIVIDEND FOCUS",
    "DIVIDEND YIELD",
    "MINIMUM VARIANCE",
    "MINIMUM-VARIANCE",
    "LOW VOLATILITY",
    "COVERED CALL",
    "QUALITY",
    "VALUE INDEX",
    "VALUE FACTOR",
    "MOMENTUM",
    "CLIMATE",
    "CARBON",
    "ESG",
    "REIT",
    "COMMODITY",
    "BOND",
    "CURRENCY",
    "TOPIX-17",
)
EQUITY_PORTFOLIO_ROLES = (
    "BROAD_MARKET_CORE",
    "LARGE_CAP_MARKET_PROXY",
    "SECTOR_EXPOSURE",
    "SELECTED_MARKET_INDEX",
    "CLASSIFICATION_PENDING",
)
FORBIDDEN_OUTPUT_KEY_PARTS = (
    "symbol_return",
    "portfolio_return",
    "historical_return",
    "future_return",
    "risk_adjusted",
    "volatility",
    "drawdown",
    "rank_ic",
    "pnl",
    "profit_factor",
    "price_value",
    "adjusted_close",
    "recommendation",
    "ranking",
    "preferred_instrument",
    "portfolio_weight",
    "lot_rounding",
)


class InstrumentInventoryError(ValueError):
    """Raised when Phase 2 authority, integrity, or inventory invariants fail."""


class TableParser(HTMLParser):
    """Collect textual cells from every HTML table without third-party parsers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell_parts is not None:
            assert self._row is not None
            self._row.append(normalize_space(" ".join(self._cell_parts)))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            assert self._table is not None
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def main() -> int:
    args = build_parser().parse_args()
    build_inventory(
        normalized_dir=args.normalized_dir,
        source_dir=args.source_dir,
        authorization_path=args.authorization,
        semantic_correction_path=args.semantic_correction_record,
        postreview_correction_path=args.postreview_correction_record,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument(
        "--semantic-correction-record",
        type=Path,
        default=SEMANTIC_CORRECTION_PATH,
    )
    parser.add_argument(
        "--postreview-correction-record",
        type=Path,
        default=POSTREVIEW_CORRECTION_PATH,
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def build_inventory(
    *,
    normalized_dir: Path,
    source_dir: Path,
    authorization_path: Path,
    semantic_correction_path: Path = SEMANTIC_CORRECTION_PATH,
    postreview_correction_path: Path = POSTREVIEW_CORRECTION_PATH,
    output_dir: Path,
) -> dict[str, Any]:
    authorization = load_and_verify_authorization(
        authorization_path,
        source_dir=source_dir,
        output_dir=output_dir,
    )
    semantic_correction = load_and_verify_semantic_correction(semantic_correction_path)
    postreview_correction = load_and_verify_postreview_correction(postreview_correction_path)
    bound_inputs = verify_bound_inputs(authorization)
    normalized_manifest = load_json_object(
        bound_inputs["normalized_manifest"], label="normalized manifest"
    )
    source_manifest_path = source_dir / "source-manifest.json"
    source_manifest = verify_source_manifest(
        source_manifest_path,
        source_dir=source_dir,
        authorization_path=authorization_path,
    )
    bar_partitions, master_partitions = verify_archive_integrity(
        normalized_dir=normalized_dir,
        normalized_manifest=normalized_manifest,
        normalized_manifest_path=bound_inputs["normalized_manifest"],
    )
    latest_master_record = latest_partition(master_partitions)
    if latest_master_record["last_date"] != SNAPSHOT_DATE.isoformat():
        raise InstrumentInventoryError(
            f"latest master snapshot drifted: {latest_master_record['last_date']}"
        )
    latest_master = pl.read_parquet(
        latest_master_record["absolute_path"],
        columns=["as_of_date", "code", "company_name", "product_category"],
    )
    assert_unique_keys(latest_master, ("as_of_date", "code"), label="latest master")
    if latest_master["as_of_date"].unique().to_list() != [SNAPSHOT_DATE]:
        raise InstrumentInventoryError("latest master contains an unexpected as_of_date")

    source_by_id = source_records_by_id(source_manifest)
    market_rows = parse_jpx_english_etf_rows(
        source_dir / source_by_id["JPX_ETF_MARKET_LIST_HTML"]["path"]
    )
    sector_rows = parse_jpx_english_etf_rows(
        source_dir / source_by_id["JPX_ETF_SECTOR_LIST_HTML"]["path"]
    )
    bond_rows = parse_jpx_japanese_bond_rows(
        source_dir / source_by_id["JPX_ETF_BOND_LIST_HTML"]["path"]
    )
    product_570a = required_mapping(
        source_by_id["JPX_ETF_570A_PRODUCT_PDF"], "permitted_inventory_facts"
    )
    verify_570a_product_facts(product_570a)
    verify_documented_capabilities(source_dir=source_dir, source_by_id=source_by_id)

    snapshot_etfs = latest_master.filter(pl.col("product_category") == ETF_PRODUCT_CATEGORY)
    snapshot_etf_codes = set(snapshot_etfs["code"].to_list())
    classified = classify_source_rows(
        market_rows=market_rows,
        sector_rows=sector_rows,
        bond_rows=bond_rows,
        product_570a=product_570a,
        snapshot_etf_codes=snapshot_etf_codes,
    )
    candidate_codes = sorted(row["security_code"] for row in classified)
    if not candidate_codes:
        raise InstrumentInventoryError("no candidate instruments matched the bound snapshot")
    if len(candidate_codes) != len(set(candidate_codes)):
        raise InstrumentInventoryError("an instrument was assigned to more than one class")

    master_coverage = load_master_coverage(
        [record["absolute_path"] for record in master_partitions],
        candidate_codes=candidate_codes,
    )
    bar_coverage, session_dates = load_bar_coverage(
        [record["absolute_path"] for record in bar_partitions],
        candidate_codes=candidate_codes,
        snapshot_date=SNAPSHOT_DATE,
    )
    inventory_rows = [
        build_instrument_record(
            classified_row=row,
            master_coverage=master_coverage[row["security_code"]],
            bar_coverage=bar_coverage[row["security_code"]],
            source_by_id=source_by_id,
        )
        for row in sorted(classified, key=lambda item: (item["class_id"], item["security_code"]))
    ]
    classes = build_classes(inventory_rows)
    class_counts = {item["class_id"]: item["snapshot_member_count"] for item in classes}
    overall_reasons = sorted({reason for item in classes for reason in item["decision_reasons"]})
    overall_reasons.append("KABU_API_REGISTERED_SYMBOL_CAP_PLAN_NOT_APPROVED")
    overall_reasons.sort()
    inventory = {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "reset_id": RESET_ID,
        "phase": 2,
        "identity": "NON_ALPHA_NON_STRATEGY_CANDIDATE_INSTRUMENT_AND_DATA_INVENTORY",
        "builder_version": BUILDER_VERSION,
        "phase_status": "COMPLETE_NO_PERFORMANCE_USED",
        "generated_at": datetime.now(UTC).isoformat(),
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "strategy_candidate_created": False,
        "phase3_started": False,
        "broker_endpoint_called": False,
        "price_columns_read": False,
        "performance_values_computed": False,
        "instruments_ranked_or_recommended": False,
        "snapshot_date": SNAPSHOT_DATE.isoformat(),
        "population_contract": {
            "historical_master_product_category": ETF_PRODUCT_CATEGORY,
            "snapshot_etf_count": snapshot_etfs.height,
            "classification_source": (
                "Exact official JPX category rows intersected with the exact historical-master "
                "snapshot; no current membership backfill."
            ),
            "geared_products_excluded_before_description": True,
            "selection_by_cost_turnover_or_outcome": False,
            "semantic_corrections": [
                {
                    "record_id": required_text(semantic_correction, "record_id"),
                    "record": file_record(semantic_correction_path),
                },
                {
                    "record_id": required_text(postreview_correction, "record_id"),
                    "record": file_record(postreview_correction_path),
                },
            ],
            "original_authorization_or_correction_record_modified": False,
        },
        "source_parsing": {
            "jpx_market_page_row_count": len(market_rows),
            "jpx_sector_page_row_count": len(sector_rows),
            "jpx_bond_page_row_count": len(bond_rows),
            "snapshot_class_counts": class_counts,
            "snapshot_etf_not_in_three_classes_count": snapshot_etfs.height - len(candidate_codes),
            "classification_is_exhaustive_within_each_definition": True,
            "classification_does_not_assert_all_etfs_belong_to_a_class": True,
        },
        "descriptive_window": {
            "session_count": len(session_dates),
            "first_session": session_dates[0].isoformat(),
            "last_session": session_dates[-1].isoformat(),
            "fields_read": ["date", "code", "turnover_jpy"],
            "price_fields_read": [],
            "statistic_used_for_selection_or_ranking": False,
        },
        "individual_stock_baseline": {
            "snapshot_product_category": ORDINARY_EQUITY_PRODUCT_CATEGORY,
            "snapshot_instrument_count": latest_master.filter(
                pl.col("product_category") == ORDINARY_EQUITY_PRODUCT_CATEGORY
            ).height,
            "instrument_rows_enumerated_as_candidates": False,
            "trading_unit_coverage": "NOT_AVAILABLE_IN_BOUND_HISTORICAL_MASTER",
            "point_in_time_terminal_and_lineage_coverage": ("INCOMPLETE_AS_ESTABLISHED_BY_PHASE1"),
        },
        "primary_source_registry": normalized_source_registry(source_manifest),
        "classes": classes,
        "kabu_registered_symbol_capacity": {
            "constraint_code": "KABU_REST_PUSH_SHARED_REGISTERED_SYMBOL_CAP_50",
            "current_inventory_count": len(candidate_codes),
            "documented_shared_rest_push_registered_symbol_cap": 50,
            "static_inventory_blocked": False,
            "sequential_compatibility_verification_blocked": False,
            "full_inventory_simultaneous_runtime_monitoring_possible": len(candidate_codes) <= 50,
            "full_inventory_simultaneous_auction_observation_possible": len(candidate_codes) <= 50,
            "registration_rotation_assumed_compatible_with_requirements": False,
            "status": "BLOCKS_SIMULTANEOUS_RUNTIME_AND_AUCTION_ONLY",
            "phase3_resolution_options": [
                "FROZEN_SUBSET_AT_OR_BELOW_50",
                "APPROVED_ALTERNATIVE_MARKET_DATA_SOURCE",
                "PROVEN_REGISTRATION_ROTATION_COMPATIBLE_WITH_REQUIREMENTS",
            ],
        },
        "phase3_reopen_gates": {
            "status": "BLOCKED",
            "all_required_gates_must_pass": True,
            "scope": {
                "status": "NOT_FROZEN",
                "one_of": [
                    "STATIC_LOT_GEOMETRY",
                    "LOOKTHROUGH_PORTFOLIO_GEOMETRY",
                    "EXECUTION_AWARE_FEASIBILITY",
                ],
            },
            "governance": {
                "inventory_asof_2026_06_30_frozen": True,
                "selected_subset_frozen_before_price_return_or_outcome_access": False,
                "project_kill_switch_untouched": True,
                "existing_oos_untouched": True,
            },
            "classification": {
                "source_category_confirmed": True,
                "economic_classification_confirmed": False,
                "methodology_version_coverage_complete": False,
                "benchmark_lineage_complete": False,
                "index_variant_pr_tr_ntr_fixed": False,
            },
            "instrument_identity": {
                "listing_and_delisting_lineage_complete": False,
                "code_and_name_lineage_complete": False,
                "trading_unit_lineage_complete": False,
                "redemption_and_termination_complete": False,
                "unknown_events_zero": False,
            },
            "outcome": {
                "total_outcome_contract_complete": False,
                "distribution_cashflows_complete": False,
                "corporate_actions_complete": False,
                "settlement_contract_complete": False,
            },
            "lookthrough": {
                "required_if_overlap_concentration_or_effective_bets_are_evaluated": True,
                "constituent_or_pcf_coverage_complete": False,
            },
            "broker": {
                "k1_pass_for_every_selected_instrument": False,
                "k2_pass_for_every_selected_instrument": False,
                "k3_pass_for_every_required_order_profile": False,
                "k4_pass_for_every_selected_instrument": False,
                "k4_evidence_not_expired": False,
                "api_registered_symbol_cap_plan_approved": False,
            },
            "execution_data": {
                "mode_fixed": False,
                "allowed_modes": ["HISTORICAL_JPX_MARKET_DATA", "FORWARD_ONLY_COLLECTION"],
                "venue_policy_fixed": False,
                "sor_claims_limited_to_available_evidence": False,
                "forward_only_prohibits_historical_execution_claims": True,
            },
            "provenance": {
                "raw_primary_artifacts_preserved": True,
                "hashes_complete": True,
                "effective_and_retrieved_times_complete": False,
                "parser_versions_complete": False,
                "license_status_complete": False,
                "superseded_artifacts_excluded_from_active_path": True,
            },
            "live_only_after_phase3_and_paper": {
                "k5a_production_submit_cancel_passed": False,
                "k5b_minimum_lot_execution_and_exit_passed": False,
                "separate_live_approval_granted": False,
            },
        },
        "decision": "NO_GO_PHASE3_CURRENT_INSTRUMENT_DATA_FOUNDATION",
        "decision_reasons": overall_reasons,
        "decision_meaning": (
            "NO_GO concerns the data foundation and authorizes neither instrument selection "
            "nor strategy, paper, or live work."
        ),
        "authority_boundary": {
            "phase2_completed_by_this_artifact": True,
            "phase3_or_later_authorized": False,
            "paper_or_live_modified": False,
            "project_kill_switch_modified": False,
            "next_action": (
                "Stop at Phase 2. Remediation or Phase 3 requires separate explicit user "
                "authorization and does not alter the 2026-09-30 kill switch."
            ),
        },
    }
    assert_no_prohibited_output_keys(inventory)

    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)
    temporary_dir.mkdir(parents=True)
    inventory_path = temporary_dir / INVENTORY_FILENAME
    write_json(inventory_path, inventory)
    run_manifest = build_run_manifest(
        authorization_path=authorization_path,
        source_manifest_path=source_manifest_path,
        normalized_manifest_path=bound_inputs["normalized_manifest"],
        inventory_path=inventory_path,
        semantic_correction_path=semantic_correction_path,
        postreview_correction_path=postreview_correction_path,
        source_manifest=source_manifest,
        bar_partitions=bar_partitions,
        master_partitions=master_partitions,
    )
    write_json(temporary_dir / RUN_MANIFEST_FILENAME, run_manifest)
    temporary_dir.rename(output_dir)
    return inventory


def load_and_verify_authorization(
    path: Path,
    *,
    source_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    authorization = load_json_object(path, label="authorization")
    if required_text(authorization, "authorization_id") != AUTHORIZATION_ID:
        raise InstrumentInventoryError("authorization_id drifted")
    if required_text(authorization, "reset_id") != RESET_ID:
        raise InstrumentInventoryError("reset_id drifted")
    if required_int(authorization, "phase") != 2:
        raise InstrumentInventoryError("authorization phase drifted")
    if authorization.get("research_only") is not True:
        raise InstrumentInventoryError("authorization research_only must be true")
    if authorization.get("paper_live_enabled") is not False:
        raise InstrumentInventoryError("authorization paper_live_enabled must be false")
    if authorization.get("counts_as_2026_09_30_kill_switch_evidence") is not False:
        raise InstrumentInventoryError("authorization kill-switch evidence flag drifted")
    scope = required_mapping(authorization, "scope")
    for key, expected in EXPECTED_SCOPE.items():
        if scope.get(key) is not expected:
            raise InstrumentInventoryError(f"authorization scope drifted: {key}")
    domains = scope.get("allowed_primary_source_domains")
    if not isinstance(domains, list) or set(domains) != ALLOWED_SOURCE_DOMAINS:
        raise InstrumentInventoryError("authorization source-domain allowlist drifted")
    expected_source = Path(required_text(scope, "expected_source_dir")).resolve()
    expected_output = Path(required_text(scope, "expected_output_dir")).resolve()
    if source_dir.resolve() != expected_source:
        raise InstrumentInventoryError("source directory is not authorization-bound")
    if output_dir.resolve() != expected_output:
        raise InstrumentInventoryError("output directory is not authorization-bound")
    contract = required_mapping(authorization, "preexecution_contract")
    if required_text(contract, "inventory_version") != AUTHORIZED_BUILDER_VERSION:
        raise InstrumentInventoryError("inventory builder version drifted")
    return authorization


def load_and_verify_semantic_correction(path: Path) -> dict[str, Any]:
    correction = load_json_object(path, label="semantic correction")
    if required_text(correction, "record_id") != SEMANTIC_CORRECTION_ID:
        raise InstrumentInventoryError("semantic correction record_id drifted")
    if required_int(correction, "phase") != 2:
        raise InstrumentInventoryError("semantic correction phase drifted")
    if required_text(correction, "authorization_id") != AUTHORIZATION_ID:
        raise InstrumentInventoryError("semantic correction authorization_id drifted")
    superseded = required_mapping(correction, "superseded_artifact")
    if required_sha256(superseded, "inventory_sha256") != (
        "43e845456fbcdfeef112f0f96bdc6cdc98dbea99123bf6a3738b5d44ff588bca"
    ):
        raise InstrumentInventoryError("semantic correction inventory binding drifted")
    if required_sha256(superseded, "run_manifest_sha256") != (
        "a5fa8237f2d0964bd683704fcd86c518075afa0127d858e8fbb60469c11cc241"
    ):
        raise InstrumentInventoryError("semantic correction run-manifest binding drifted")
    boundary = required_mapping(correction, "correction_boundary")
    expected_false = (
        "source_or_snapshot_membership_changed",
        "classified_instrument_count_changed",
        "price_or_outcome_value_inspected",
        "performance_value_computed",
        "instrument_ranked_or_recommended",
        "broker_endpoint_called",
        "phase3_or_later_started",
        "paper_or_live_modified",
        "original_authorization_record_modified",
    )
    if boundary.get("semantic_classification_and_gate_decomposition_only") is not True:
        raise InstrumentInventoryError("semantic correction scope drifted")
    if any(boundary.get(key) is not False for key in expected_false):
        raise InstrumentInventoryError("semantic correction safety boundary drifted")
    return correction


def load_and_verify_postreview_correction(path: Path) -> dict[str, Any]:
    correction = load_json_object(path, label="postreview semantic correction")
    if required_text(correction, "record_id") != POSTREVIEW_CORRECTION_ID:
        raise InstrumentInventoryError("postreview correction record_id drifted")
    if required_int(correction, "phase") != 2:
        raise InstrumentInventoryError("postreview correction phase drifted")
    if required_text(correction, "authorization_id") != AUTHORIZATION_ID:
        raise InstrumentInventoryError("postreview correction authorization_id drifted")
    superseded = required_mapping(correction, "superseded_artifact")
    if required_sha256(superseded, "inventory_sha256") != (
        "a7bbddf3f96c8d0599fca7a22769ba745d962fc7ceb5876418776be575f105bf"
    ):
        raise InstrumentInventoryError("postreview correction inventory binding drifted")
    if required_sha256(superseded, "run_manifest_sha256") != (
        "08f2f6b27eb6994a4299abfda7ac50ef7e5cf26d5e0844c432fd1a24bb16f02e"
    ):
        raise InstrumentInventoryError("postreview correction run-manifest binding drifted")
    if superseded.get("eligible_for_active_manifest") is not False:
        raise InstrumentInventoryError("superseded artifact active-manifest eligibility drifted")
    if superseded.get("eligible_for_phase3_input") is not False:
        raise InstrumentInventoryError("superseded artifact Phase 3 eligibility drifted")
    boundary = required_mapping(correction, "correction_boundary")
    expected_false = (
        "source_or_snapshot_membership_changed",
        "classified_instrument_count_changed",
        "price_or_outcome_value_inspected",
        "performance_value_computed",
        "instrument_ranked_or_recommended",
        "broker_endpoint_called",
        "phase3_or_later_started",
        "paper_or_live_modified",
        "prior_authorization_or_correction_record_modified",
    )
    if boundary.get("semantic_classification_gate_and_constraint_scope_only") is not True:
        raise InstrumentInventoryError("postreview correction scope drifted")
    if any(boundary.get(key) is not False for key in expected_false):
        raise InstrumentInventoryError("postreview correction safety boundary drifted")
    return correction


def verify_bound_inputs(authorization: Mapping[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    bound = required_mapping(authorization, "bound_inputs")
    for label, raw_record in bound.items():
        record = as_mapping(raw_record, label=f"bound input {label}")
        path = resolve_repository_path(required_text(record, "path"))
        expected_hash = required_sha256(record, "sha256")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise InstrumentInventoryError(
                f"bound input hash mismatch for {label}: {actual_hash} != {expected_hash}"
            )
        result[label] = path
    return result


def verify_source_manifest(
    manifest_path: Path,
    *,
    source_dir: Path,
    authorization_path: Path,
) -> dict[str, Any]:
    manifest = load_json_object(manifest_path, label="source manifest")
    if required_text(manifest, "source_set_id") != SOURCE_SET_ID:
        raise InstrumentInventoryError("source_set_id drifted")
    if manifest.get("research_only") is not True or manifest.get("paper_live_enabled") is not False:
        raise InstrumentInventoryError("source manifest research boundary drifted")
    auth = required_mapping(manifest, "authorization")
    if resolve_repository_path(required_text(auth, "path")) != authorization_path.resolve():
        raise InstrumentInventoryError("source manifest authorization path drifted")
    if sha256_file(authorization_path) != required_sha256(auth, "sha256"):
        raise InstrumentInventoryError("source manifest authorization hash drifted")
    records = source_records_by_id(manifest)
    required_ids = {
        "JPX_ETF_ALL_LIST_PDF",
        "JPX_ETF_MARKET_LIST_HTML",
        "JPX_ETF_SECTOR_LIST_HTML",
        "JPX_ETF_BOND_LIST_HTML",
        "JPX_ETF_570A_PRODUCT_PDF",
        "JPX_ETF_TRADING_RULES_HTML",
        "JPX_ETF_DELISTING_HTML",
        "JPX_INDEX_LINEUP_HTML",
        "JPX_TOPIX17_METHODOLOGY_PDF",
        "JQUANTS_HOME_HTML",
        "KABU_API_REFERENCE_HTML",
        "KABU_API_OPENAPI_YAML",
        "KABU_API_SERVICE_HTML",
    }
    if set(records) != required_ids:
        raise InstrumentInventoryError("source manifest set drifted")
    manifest_paths: set[Path] = set()
    for source_id, record in records.items():
        source_path = (source_dir / required_text(record, "path")).resolve()
        if source_path.parent != source_dir.resolve():
            raise InstrumentInventoryError(f"source path escaped source directory: {source_id}")
        manifest_paths.add(source_path)
        if source_path.stat().st_size != required_int(record, "byte_size"):
            raise InstrumentInventoryError(f"source size mismatch: {source_id}")
        if sha256_file(source_path) != required_sha256(record, "sha256"):
            raise InstrumentInventoryError(f"source hash mismatch: {source_id}")
        host = re.sub(r"^www\.", "", source_host(required_text(record, "url")))
        allowed_hosts = {re.sub(r"^www\.", "", item) for item in ALLOWED_SOURCE_DOMAINS}
        if host not in allowed_hosts:
            raise InstrumentInventoryError(f"source host is outside authorization: {source_id}")
    actual_paths = {path.resolve() for path in source_dir.iterdir() if path != manifest_path}
    if actual_paths != manifest_paths:
        raise InstrumentInventoryError("source directory contains an unmanifested or missing file")
    return manifest


def verify_archive_integrity(
    *,
    normalized_dir: Path,
    normalized_manifest: Mapping[str, Any],
    normalized_manifest_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if normalized_manifest_path.parent.resolve() != normalized_dir.resolve():
        raise InstrumentInventoryError("normalized directory is not bound by the manifest")
    if required_text(normalized_manifest, "normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise InstrumentInventoryError("normalized schema version drifted")
    if normalized_manifest.get("research_only") is not True:
        raise InstrumentInventoryError("normalized archive is not research-only")
    datasets = required_mapping(normalized_manifest, "datasets")
    bars = verify_partition_set(
        required_mapping(datasets, "equities_bars_daily"), normalized_dir=normalized_dir
    )
    masters = verify_partition_set(
        required_mapping(datasets, "equities_master_month_end"), normalized_dir=normalized_dir
    )
    return bars, masters


def verify_partition_set(
    dataset: Mapping[str, Any], *, normalized_dir: Path
) -> list[dict[str, Any]]:
    raw_partitions = dataset.get("partitions")
    if not isinstance(raw_partitions, list) or not raw_partitions:
        raise InstrumentInventoryError("normalized partition list is empty")
    records: list[dict[str, Any]] = []
    for raw in raw_partitions:
        record = as_mapping(raw, label="partition")
        path = (normalized_dir / required_text(record, "path")).resolve()
        if normalized_dir.resolve() not in path.parents:
            raise InstrumentInventoryError("normalized partition escaped its directory")
        if path.stat().st_size != required_int(record, "byte_size"):
            raise InstrumentInventoryError(f"partition size mismatch: {path}")
        expected_hash = required_sha256(record, "sha256")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise InstrumentInventoryError(f"partition hash mismatch: {path}")
        records.append(
            {
                "path": required_text(record, "path"),
                "absolute_path": path,
                "first_date": required_text(record, "first_date"),
                "last_date": required_text(record, "last_date"),
                "row_count": required_int(record, "row_count"),
                "byte_size": required_int(record, "byte_size"),
                "sha256": expected_hash,
            }
        )
    if len(records) != required_int(dataset, "partition_count"):
        raise InstrumentInventoryError("normalized partition count drifted")
    return records


def parse_jpx_english_etf_rows(path: Path) -> list[dict[str, Any]]:
    tables = parse_tables(path)
    expected_headers = (
        "Listing Date",
        "Index",
        "Code",
        "Fund Name",
        "Management Company",
        "Trading Unit",
        "Trust Fee",
        "Market Maker(*)",
    )
    rows: list[dict[str, Any]] = []
    for table in tables:
        if not table or tuple(cell.rstrip() for cell in table[0]) != expected_headers:
            continue
        for cells in table[1:]:
            if len(cells) != len(expected_headers):
                raise InstrumentInventoryError(f"unexpected JPX English table row width in {path}")
            source_code = normalize_source_code(cells[2])
            rows.append(
                {
                    "listing_date": parse_english_listing_date(cells[0]).isoformat(),
                    "tracked_index_name": cells[1],
                    "source_code": source_code,
                    "security_code": jquants_code(source_code),
                    "fund_name": clean_fund_name(cells[3]),
                    "management_company": cells[4],
                    "trading_unit": positive_int(cells[5], label="trading unit"),
                    "trust_fee_source_text": cells[6],
                }
            )
    if not rows:
        raise InstrumentInventoryError(f"official ETF table was not found: {path}")
    assert_unique_records(rows, key="security_code", label=str(path))
    return rows


def parse_jpx_japanese_bond_rows(path: Path) -> list[dict[str, Any]]:
    tables = parse_tables(path)
    expected_prefix = (
        "連動対象指標",
        "コード",
        "名称",
        "管理会社 \uff08検索コード\uff09",
        "信託 報酬",
    )
    rows: list[dict[str, Any]] = []
    for table in tables:
        if not table or tuple(table[0][:5]) != expected_prefix:
            continue
        for cells in table[1:]:
            if len(cells) < 5:
                raise InstrumentInventoryError(f"unexpected JPX bond table row width in {path}")
            source_code = normalize_source_code(cells[1])
            rows.append(
                {
                    "listing_date": None,
                    "tracked_index_name": cells[0],
                    "source_code": source_code,
                    "security_code": jquants_code(source_code),
                    "fund_name": clean_fund_name(cells[2]),
                    "management_company": cells[3],
                    "trading_unit": None,
                    "trust_fee_source_text": cells[4],
                }
            )
    if not rows:
        raise InstrumentInventoryError(f"official bond ETF table was not found: {path}")
    assert_unique_records(rows, key="security_code", label=str(path))
    return rows


def parse_tables(path: Path) -> list[list[list[str]]]:
    parser = TableParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.tables


def classify_source_rows(
    *,
    market_rows: Sequence[Mapping[str, Any]],
    sector_rows: Sequence[Mapping[str, Any]],
    bond_rows: Sequence[Mapping[str, Any]],
    product_570a: Mapping[str, Any],
    snapshot_etf_codes: set[str],
) -> list[dict[str, Any]]:
    classified: list[dict[str, Any]] = []
    for row in market_rows:
        if (
            row["security_code"] in snapshot_etf_codes
            and source_listing_on_or_before_snapshot(row)
            and is_market_category_inventory_row(row)
        ):
            classified.append(
                {
                    **row,
                    "class_id": CLASS_MARKET_CATEGORY,
                    "product_source_id": "JPX_ETF_MARKET_LIST_HTML",
                    "methodology_source_ids": ["JPX_INDEX_LINEUP_HTML"],
                    "methodology_identifier": (
                        "OFFICIAL_JPX_JAPANESE_EQUITY_MARKET_INDEX_NAME::"
                        + str(row["tracked_index_name"])
                    ),
                    "methodology_coverage": (
                        "OFFICIAL_CATEGORY_AND_INDEX_NAME_BOUND; PRODUCT_SPECIFIC_FULL_"
                        "CALCULATION_METHODOLOGY_NOT_CAPTURED"
                    ),
                    "methodology_current_document_captured": False,
                    "methodology_pit_version_coverage_complete": False,
                    "benchmark_lineage_complete": False,
                }
            )
    for row in sector_rows:
        if (
            row["security_code"] in snapshot_etf_codes
            and source_listing_on_or_before_snapshot(row)
            and is_sector_row(row)
        ):
            methodology = sector_methodology_fields(row)
            classified.append(
                {
                    **row,
                    "class_id": CLASS_SECTOR,
                    "product_source_id": "JPX_ETF_SECTOR_LIST_HTML",
                    **methodology,
                }
            )
    for row in bond_rows:
        if (
            row["security_code"] in snapshot_etf_codes
            and is_short_jpy_government_bond_0_1y_row(row, product_570a=product_570a)
            and date.fromisoformat(required_text(product_570a, "listing_date")) <= SNAPSHOT_DATE
        ):
            enriched = dict(row)
            enriched["listing_date"] = required_text(product_570a, "listing_date")
            enriched["trading_unit"] = required_int(product_570a, "trading_unit")
            classified.append(
                {
                    **enriched,
                    "class_id": CLASS_SHORT_JGB,
                    "product_source_id": "JPX_ETF_570A_PRODUCT_PDF",
                    "methodology_source_ids": [
                        "JPX_ETF_BOND_LIST_HTML",
                        "JPX_ETF_570A_PRODUCT_PDF",
                    ],
                    "methodology_identifier": (
                        "FTSE_JAPAN_GOVERNMENT_BOND_0_1_YEAR_INDEX_PRODUCT_DESCRIPTION"
                    ),
                    "methodology_coverage": (
                        "OFFICIAL_PRODUCT_DESCRIPTION_EXPLICITLY_STATES_REMAINING_MATURITY_"
                        "LESS_THAN_ONE_YEAR; FULL_CALCULATION_METHODOLOGY_NOT_CAPTURED"
                    ),
                    "methodology_current_document_captured": False,
                    "methodology_pit_version_coverage_complete": False,
                    "benchmark_lineage_complete": False,
                }
            )
    assert_unique_records(classified, key="security_code", label="classified instruments")
    return classified


def is_market_category_inventory_row(row: Mapping[str, Any]) -> bool:
    text = f"{row.get('tracked_index_name', '')} {row.get('fund_name', '')}".upper()
    if any(token in text for token in GEARED_TOKENS):
        return False
    index_text = str(row.get("tracked_index_name", "")).upper()
    return not any(token in index_text for token in MARKET_CATEGORY_EXCLUSION_TOKENS)


def is_sector_row(row: Mapping[str, Any]) -> bool:
    text = f"{row.get('tracked_index_name', '')} {row.get('fund_name', '')}".upper()
    index_text = str(row.get("tracked_index_name", "")).upper()
    official_sector_identity = "TOPIX-17" in index_text or index_text.startswith("TOPIX BANKS")
    return official_sector_identity and not any(token in text for token in GEARED_TOKENS)


def sector_methodology_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    index_name = required_text(row, "tracked_index_name")
    if "TOPIX-17" in index_name.upper():
        return {
            "methodology_source_ids": ["JPX_TOPIX17_METHODOLOGY_PDF"],
            "methodology_identifier": "JPX_TOPIX_17_CALCULATION_METHODOLOGY",
            "methodology_coverage": (
                "OFFICIAL_TOPIX17_CLASS_METHODOLOGY_AND_PRODUCT_INDEX_MAPPING_BOUND"
            ),
            "methodology_current_document_captured": True,
            "methodology_pit_version_coverage_complete": False,
            "benchmark_lineage_complete": False,
        }
    return {
        "methodology_source_ids": ["JPX_INDEX_LINEUP_HTML"],
        "methodology_identifier": "OFFICIAL_JPX_SECTOR_INDEX_NAME::" + index_name,
        "methodology_coverage": (
            "OFFICIAL_SECTOR_CATEGORY_AND_INDEX_NAME_BOUND; PRODUCT_SPECIFIC_FULL_"
            "CALCULATION_METHODOLOGY_NOT_CAPTURED"
        ),
        "methodology_current_document_captured": False,
        "methodology_pit_version_coverage_complete": False,
        "benchmark_lineage_complete": False,
    }


def is_short_jpy_government_bond_0_1y_row(
    row: Mapping[str, Any], *, product_570a: Mapping[str, Any]
) -> bool:
    if row.get("source_code") != product_570a.get("security_code"):
        return False
    text = f"{row.get('tracked_index_name', '')} {row.get('fund_name', '')}".upper()
    if any(token in text for token in ("円ドル", "米国", "外国", "CURRENCY", "LONG")):
        return False
    return (
        "日本国債0-1年" in normalize_space(str(row.get("tracked_index_name", "")))
        and product_570a.get("maximum_remaining_maturity") == "LESS_THAN_ONE_YEAR"
    )


def verify_570a_product_facts(record: Mapping[str, Any]) -> None:
    expected = {
        "security_code": "570A",
        "listing_date": "2026-05-27",
        "trading_unit": 10,
        "tracked_index_name": "FTSE Japan Government Bond 0-1 Year Index",
        "maximum_remaining_maturity": "LESS_THAN_ONE_YEAR",
    }
    if dict(record) != expected:
        raise InstrumentInventoryError("570A permitted inventory facts drifted")


def verify_documented_capabilities(
    *,
    source_dir: Path,
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    openapi_record = source_by_id["KABU_API_OPENAPI_YAML"]
    openapi = (source_dir / required_text(openapi_record, "path")).read_text(encoding="utf-8")
    required_fragments = (
        'version: "1.5"',
        "  /sendorder:",
        "  /board/{symbol}:",
        "  /symbol/{symbol}:",
        "  /register:",
        "  /unregister:",
        "最大50銘柄",
    )
    if any(fragment not in openapi for fragment in required_fragments):
        raise InstrumentInventoryError("bound kabu OpenAPI capabilities drifted")
    jquants_record = source_by_id["JQUANTS_HOME_HTML"]
    jquants = (source_dir / required_text(jquants_record, "path")).read_text(encoding="utf-8")
    if "配当" not in jquants or "分割" not in jquants or "売買代金" not in jquants:
        raise InstrumentInventoryError("bound J-Quants service capabilities drifted")


def source_listing_on_or_before_snapshot(row: Mapping[str, Any]) -> bool:
    return date.fromisoformat(required_text(row, "listing_date")) <= SNAPSHOT_DATE


def load_master_coverage(
    paths: Sequence[Path], *, candidate_codes: Sequence[str]
) -> dict[str, dict[str, Any]]:
    frame = (
        pl.scan_parquet(paths)
        .select(["as_of_date", "code", "product_category"])
        .filter(pl.col("code").is_in(candidate_codes))
        .collect()
    )
    assert_unique_keys(frame, ("as_of_date", "code"), label="candidate master history")
    grouped = frame.group_by("code").agg(
        pl.len().alias("snapshot_count"),
        pl.col("as_of_date").min().alias("first_snapshot"),
        pl.col("as_of_date").max().alias("last_snapshot"),
        pl.col("product_category").n_unique().alias("product_category_count"),
    )
    result: dict[str, dict[str, Any]] = {}
    for row in grouped.iter_rows(named=True):
        if row["product_category_count"] != 1:
            raise InstrumentInventoryError(f"product category changed for {row['code']}")
        result[row["code"]] = {
            "historical_master_snapshot_count": row["snapshot_count"],
            "first_historical_master_snapshot": row["first_snapshot"].isoformat(),
            "last_historical_master_snapshot": row["last_snapshot"].isoformat(),
            "snapshot_product_category": ETF_PRODUCT_CATEGORY,
        }
    ensure_code_coverage(result, candidate_codes, label="master")
    return result


def load_bar_coverage(
    paths: Sequence[Path],
    *,
    candidate_codes: Sequence[str],
    snapshot_date: date,
) -> tuple[dict[str, dict[str, Any]], list[date]]:
    scan = pl.scan_parquet(paths)
    sessions = (
        scan.select("date")
        .filter(pl.col("date") <= snapshot_date)
        .unique()
        .sort("date")
        .tail(60)
        .collect()["date"]
        .to_list()
    )
    if len(sessions) != 60 or sessions[-1] != snapshot_date:
        raise InstrumentInventoryError("60-session descriptor calendar is incomplete")
    frame = (
        scan.select(["date", "code", "turnover_jpy"])
        .filter((pl.col("date") <= snapshot_date) & pl.col("code").is_in(candidate_codes))
        .collect()
    )
    assert_unique_keys(frame, ("date", "code"), label="candidate bar history")
    full = frame.group_by("code").agg(
        pl.len().alias("source_row_count"),
        pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"),
    )
    window = (
        frame.filter(pl.col("date").is_in(sessions))
        .group_by("code")
        .agg(
            pl.len().alias("window_source_row_count"),
            pl.col("turnover_jpy").is_not_null().sum().alias("nonnull_turnover_count"),
            pl.col("turnover_jpy").median().alias("median_turnover_jpy"),
        )
    )
    joined = full.join(window, on="code", how="left")
    result: dict[str, dict[str, Any]] = {}
    for row in joined.iter_rows(named=True):
        median = row["median_turnover_jpy"]
        result[row["code"]] = {
            "daily_ohlcv_source_row_count": row["source_row_count"],
            "first_daily_ohlcv_source_row_date": row["first_date"].isoformat(),
            "last_daily_ohlcv_source_row_date": row["last_date"].isoformat(),
            "descriptor_window_source_row_count": row["window_source_row_count"] or 0,
            "descriptor_window_nonnull_turnover_count": row["nonnull_turnover_count"] or 0,
            "median_turnover_jpy": float(median) if median is not None else None,
        }
    ensure_code_coverage(result, candidate_codes, label="bar")
    return result, sessions


def semantic_classification_fields(classified_row: Mapping[str, Any]) -> dict[str, Any]:
    class_id = required_text(classified_row, "class_id")
    if class_id == CLASS_MARKET_CATEGORY:
        fields = {
            "source_category": "JPX_JAPANESE_EQUITY_MARKET",
            "economic_classification": {
                "value": "CLASSIFICATION_PENDING",
                "exposure_dimension": "CLASSIFICATION_PENDING",
                "index_family": "CLASSIFICATION_PENDING",
                "status": "PENDING_FULL_OFFICIAL_METHODOLOGY_AND_VERSION_REVIEW",
            },
            "portfolio_role": {
                "value": "CLASSIFICATION_PENDING",
                "status": "PENDING",
                "allowed_equity_values": list(EQUITY_PORTFOLIO_ROLES),
                "assignment_uses_price_return_or_performance": False,
            },
            "classification_status": "PENDING",
            "methodology_status": "PIT_INCOMPLETE",
            "benchmark_lineage_status": "INCOMPLETE",
            "settlement_cash": False,
            "strict_cash_equivalent": False,
            "cash_proxy_status": "NOT_APPLICABLE",
        }
    elif class_id == CLASS_SECTOR:
        is_topix_banks = required_text(classified_row, "source_code") == "1615"
        fields = {
            "source_category": "JPX_JAPANESE_EQUITY_SECTOR",
            "economic_classification": {
                "value": "SECTOR_EXPOSURE",
                "exposure_dimension": "INDUSTRY_SECTOR",
                "index_family": "TOPIX_33_SECTOR" if is_topix_banks else "TOPIX_17",
                "sector_name": sector_name(classified_row),
                "sector_name_source_text": required_text(classified_row, "tracked_index_name"),
                "status": "CONFIRMED",
            },
            "portfolio_role": {
                "value": "CLASSIFICATION_PENDING" if is_topix_banks else "SECTOR_EXPOSURE",
                "status": "PENDING" if is_topix_banks else "CONFIRMED",
                "reason": (
                    "METHODOLOGY_PIT_LINEAGE_INCOMPLETE"
                    if is_topix_banks
                    else "OFFICIAL_SECTOR_CLASSIFICATION_CONFIRMED"
                ),
                "allowed_equity_values": list(EQUITY_PORTFOLIO_ROLES),
                "assignment_uses_price_return_or_performance": False,
            },
            "classification_status": "CONFIRMED",
            "methodology_status": "PIT_INCOMPLETE",
            "benchmark_lineage_status": "INCOMPLETE",
            "settlement_cash": False,
            "strict_cash_equivalent": False,
            "cash_proxy_status": "NOT_APPLICABLE",
        }
    elif class_id == CLASS_SHORT_JGB:
        fields = {
            "source_category": "JPX_BOND_ETF",
            "economic_classification": {
                "value": "JPY_JAPAN_GOVERNMENT_BOND_0_1Y",
                "exposure_dimension": "FIXED_INCOME_SHORT_SOVEREIGN",
                "index_family": "FTSE_JAPAN_GOVERNMENT_BOND_0_1Y",
                "status": "OFFICIAL_PRODUCT_DESCRIPTION_BOUND",
            },
            "portfolio_role": {
                "value": "CASH_PROXY_CANDIDATE",
                "status": "UNVALIDATED",
                "assignment_uses_price_return_or_performance": False,
            },
            "classification_status": "CONFIRMED",
            "methodology_status": "PIT_INCOMPLETE",
            "benchmark_lineage_status": "INCOMPLETE",
            "settlement_cash": False,
            "strict_cash_equivalent": False,
            "cash_proxy_status": "UNVALIDATED",
        }
    else:
        raise InstrumentInventoryError(f"unknown semantic class: {class_id}")
    validate_semantic_classification(classified_row=classified_row, fields=fields)
    return fields


def sector_name(classified_row: Mapping[str, Any]) -> str:
    if required_text(classified_row, "source_code") == "1615":
        return "BANKS"
    index_name = required_text(classified_row, "tracked_index_name")
    prefix = "TOPIX-17 "
    suffix = " Total Return Index"
    if not index_name.startswith(prefix) or not index_name.endswith(suffix):
        raise InstrumentInventoryError("TOPIX-17 sector name cannot be parsed")
    value = index_name[len(prefix) : -len(suffix)].strip()
    if not value:
        raise InstrumentInventoryError("TOPIX-17 sector name is empty")
    return re.sub(r"\s+", "_", value.upper())


def validate_semantic_classification(
    *, classified_row: Mapping[str, Any], fields: Mapping[str, Any]
) -> None:
    class_id = required_text(classified_row, "class_id")
    role = required_text(required_mapping(fields, "portfolio_role"), "value")
    economic = required_mapping(fields, "economic_classification")
    if role == "MARKET_SEGMENT":
        raise InstrumentInventoryError("MARKET_SEGMENT is prohibited for sector exposure")
    if class_id == CLASS_MARKET_CATEGORY and role != "CLASSIFICATION_PENDING":
        raise InstrumentInventoryError("market-category role must remain pending")
    if class_id == CLASS_SECTOR:
        if required_text(economic, "value") != "SECTOR_EXPOSURE":
            raise InstrumentInventoryError("sector economic classification drifted")
        if required_text(economic, "status") != "CONFIRMED":
            raise InstrumentInventoryError("sector classification must be confirmed")
        is_topix_banks = required_text(classified_row, "source_code") == "1615"
        expected_role = "CLASSIFICATION_PENDING" if is_topix_banks else "SECTOR_EXPOSURE"
        if role != expected_role:
            raise InstrumentInventoryError("sector portfolio role drifted")


def phase3_eligibility_fields(
    *, classified_row: Mapping[str, Any], semantic_fields: Mapping[str, Any]
) -> dict[str, Any]:
    reasons = [
        "METHODOLOGY_PIT_VERSION_COVERAGE_INCOMPLETE",
        "BENCHMARK_LINEAGE_INCOMPLETE",
        "TOTAL_OUTCOME_CONTRACT_INCOMPLETE",
        "KABU_K1_TO_K4_NOT_VERIFIED",
    ]
    if semantic_fields.get("classification_status") != "CONFIRMED":
        reasons.append("ECONOMIC_CLASSIFICATION_PENDING")
    if required_text(classified_row, "class_id") == CLASS_SHORT_JGB:
        reasons.append("CASH_PROXY_STATUS_UNVALIDATED")
    return {
        "eligible_for_active_subset": False,
        "eligible_for_phase3_input": False,
        "status": "BLOCKED",
        "reasons": reasons,
    }


def kabu_compatibility_gates() -> dict[str, Any]:
    return {
        "openapi_version": "1.5",
        "documented_shared_rest_push_registered_symbol_cap": 50,
        "broker_endpoint_call_performed": False,
        "K1": {
            "name": "PRODUCTION_SYMBOL_PER_PRODUCT",
            "scope": "EACH_SELECTED_PRODUCT",
            "endpoint": "/symbol",
            "required_before_phase3": True,
            "status": "NOT_VERIFIED",
        },
        "K2": {
            "name": "PRODUCTION_BOARD_REGISTER_AND_PUSH_PER_PRODUCT",
            "scope": "EACH_SELECTED_PRODUCT",
            "endpoints": ["/board", "/register", "PUSH"],
            "required_before_phase3": True,
            "status": "NOT_VERIFIED",
        },
        "K3": {
            "name": "VALIDATION_ORDER_SCHEMA_PER_ORDER_PROFILE",
            "scope": "EVERY_FROZEN_ORDER_PROFILE",
            "endpoint": "/sendorder",
            "order_profiles_frozen": False,
            "required_before_phase3": True,
            "status": "NOT_VERIFIED",
        },
        "K4": {
            "name": "PRODUCT_ACCOUNT_CASH_ORDER_AND_SOR_ELIGIBILITY",
            "scope": "EACH_SELECTED_PRODUCT_AND_TARGET_ACCOUNT",
            "required_before_phase3": True,
            "status": "NOT_VERIFIED",
            "product_cash_order_eligible": None,
            "sor_eligible": None,
            "account_agreements_complete": None,
            "verified_at": None,
            "valid_for_trading_date": None,
            "evidence_source": None,
            "evidence_sha256": None,
        },
        "K5": {
            "name": "PRODUCTION_ORDER_LIFECYCLE_BEFORE_LIVE",
            "scope": "EACH_FROZEN_PRODUCTION_ORDER_PROFILE",
            "required_before_phase3": False,
            "required_before_live": True,
            "earliest_authorized_stage": "AFTER_PAPER_AND_SEPARATE_EXPLICIT_AUTHORIZATION",
            "status": "NOT_STARTED_NOT_AUTHORIZED",
            "stages": {
                "K5A": {
                    "name": "PRODUCTION_SUBMIT_AND_CANCEL_WITHOUT_INTENDED_FILL",
                    "approval_required": True,
                    "market_visible_and_accidental_fill_possible": True,
                },
                "K5B": {
                    "name": "MINIMUM_LOT_INTENTIONAL_EXECUTION_AND_EXIT",
                    "approval_required": True,
                    "actual_capital_at_risk": True,
                },
            },
        },
        "required_sequence": ["SHADOW", "OMS_PAPER", "K5A", "K5B", "SEPARATE_LIVE_APPROVAL"],
    }


def build_instrument_record(
    *,
    classified_row: Mapping[str, Any],
    master_coverage: Mapping[str, Any],
    bar_coverage: Mapping[str, Any],
    source_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    product_source_id = required_text(classified_row, "product_source_id")
    methodology_source_ids = classified_row.get("methodology_source_ids")
    if not isinstance(methodology_source_ids, list) or not all(
        isinstance(item, str) for item in methodology_source_ids
    ):
        raise InstrumentInventoryError("methodology source ids are invalid")
    provenance_ids = list(
        dict.fromkeys(
            [
                product_source_id,
                *methodology_source_ids,
                "JPX_ETF_TRADING_RULES_HTML",
                "JPX_ETF_DELISTING_HTML",
                "JQUANTS_HOME_HTML",
                "KABU_API_OPENAPI_YAML",
                "KABU_API_SERVICE_HTML",
            ]
        )
    )
    semantic_fields = semantic_classification_fields(classified_row)
    return {
        "class_id": required_text(classified_row, "class_id"),
        "security_code": required_text(classified_row, "security_code"),
        "source_security_code": required_text(classified_row, "source_code"),
        "fund_name": required_text(classified_row, "fund_name"),
        "management_company": required_text(classified_row, "management_company"),
        "legal_product_type": {
            "value": "EXCHANGE_TRADED_FUND",
            "historical_master_product_category": ETF_PRODUCT_CATEGORY,
            "point_in_time_snapshot_confirmed": True,
        },
        **semantic_fields,
        "phase3_eligibility": phase3_eligibility_fields(
            classified_row=classified_row,
            semantic_fields=semantic_fields,
        ),
        "listing_date": required_text(classified_row, "listing_date"),
        "termination_date_or_active_status": {
            "status": (
                "PRESENT_IN_BOUND_HISTORICAL_MASTER_AT_SNAPSHOT_AND_OFFICIAL_PRODUCT_"
                "PAGE_AT_RECEIPT"
            ),
            "termination_date": None,
            "point_in_time_termination_history_complete": False,
        },
        "tracked_index_name": required_text(classified_row, "tracked_index_name"),
        "index_methodology_identifier": {
            "value": required_text(classified_row, "methodology_identifier"),
            "coverage": required_text(classified_row, "methodology_coverage"),
            "current_document_captured": (
                classified_row.get("methodology_current_document_captured") is True
            ),
            "point_in_time_version_coverage_complete": (
                classified_row.get("methodology_pit_version_coverage_complete") is True
            ),
            "benchmark_lineage_complete": (
                classified_row.get("benchmark_lineage_complete") is True
            ),
            "current_index_variant": (
                "TOTAL_RETURN"
                if "TOTAL RETURN" in required_text(classified_row, "tracked_index_name").upper()
                else "NOT_FIXED"
            ),
        },
        "trading_unit": {
            "value": required_int(classified_row, "trading_unit"),
            "snapshot_effective_status": "OFFICIAL_PRODUCT_FACT_BOUND",
        },
        "tick_size_rule": {
            "status": "GENERIC_JPX_ETF_RULE_BOUND",
            "treatment": (
                "Applicable table depends on order level and trading unit; no instrument-level "
                "tick amount was calculated."
            ),
        },
        "daily_price_limit_treatment": {
            "status": "GENERIC_JPX_ETF_RULE_BOUND",
            "treatment": (
                "Official ETF trading-rule reference applies; no instrument-specific limit "
                "amount was calculated."
            ),
        },
        "management_fee_and_known_holding_costs": {
            "trust_fee_source_text": required_text(classified_row, "trust_fee_source_text"),
            "other_holding_costs": "NOT_CAPTURED",
            "used_for_selection_or_ranking": False,
        },
        "distribution_and_total_outcome_adjustment_availability": {
            "jquants_current_service_page_mentions_dividend_and_split_data": True,
            "bound_archive_distribution_cash_event_dataset": False,
            "bound_archive_adjustment_factor_field": True,
            "distribution_ex_dates_complete": False,
            "distribution_payment_dates_complete": False,
            "delisting_and_redemption_cash_complete": False,
            "fund_termination_complete": False,
            "fees_policy_fixed": False,
            "tax_policy_fixed": False,
            "settlement_timing_fixed": False,
            "total_outcome_point_in_time_reproducible": False,
            "status": "INCOMPLETE",
        },
        "split_consolidation_redemption_delisting_and_code_change_history_availability": {
            "bound_archive_adjustment_factor_field": True,
            "explicit_split_or_consolidation_event_table_bound": False,
            "explicit_redemption_or_delisting_event_table_bound": False,
            "explicit_security_code_lineage_table_bound": False,
            "explicit_trading_unit_change_history_bound": False,
            "current_jpx_delisting_reference_captured": True,
            "point_in_time_complete": False,
        },
        "historical_ohlcv_and_point_in_time_master_coverage": {
            **master_coverage,
            "daily_ohlcv": {
                "source_row_count": bar_coverage["daily_ohlcv_source_row_count"],
                "first_source_row_date": bar_coverage["first_daily_ohlcv_source_row_date"],
                "last_source_row_date": bar_coverage["last_daily_ohlcv_source_row_date"],
                "price_columns_read_for_phase2": [],
            },
        },
        "median_traded_value_60_session_descriptor": {
            "unit": "JPY",
            "window_source_row_count": bar_coverage["descriptor_window_source_row_count"],
            "nonnull_turnover_count": bar_coverage["descriptor_window_nonnull_turnover_count"],
            "median_turnover_jpy": bar_coverage["median_turnover_jpy"],
            "used_for_selection_or_ranking": False,
        },
        "quoted_spread_depth_and_auction_observability": {
            "quoted_spread_in_bound_archive": False,
            "order_book_depth_in_bound_archive": False,
            "opening_or_closing_auction_book_in_bound_archive": False,
            "generic_kabu_board_endpoint_documented": True,
            "product_specific_observation_performed": False,
            "status": "NOT_AVAILABLE_IN_BOUND_ARCHIVE",
        },
        "kabu_compatibility_gates": kabu_compatibility_gates(),
        "primary_source_url_effective_date_receipt_date_and_sha256": [
            source_provenance(source_by_id[source_id]) for source_id in provenance_ids
        ],
    }


def build_classes(instruments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    definitions = {
        CLASS_MARKET_CATEGORY: (
            "Official JPX Japanese Equity (Market) category ETF rows after the frozen mechanical "
            "exclusions. Category membership alone does not establish broad-market exposure; "
            "economic exposure and portfolio role remain pending until full methodology review."
        ),
        CLASS_SECTOR: (
            "Official JPX Japanese Equity (Sector) category ETF rows whose tracked index is "
            "TOPIX-17 or TOPIX Banks, excluding geared exposure."
        ),
        CLASS_SHORT_JGB: (
            "ETF exposure to the FTSE Japan Government Bond 0-1 Year Index. It is neither "
            "settlement cash nor a validated strict cash equivalent."
        ),
    }
    result: list[dict[str, Any]] = []
    for class_id in CLASS_IDS:
        members = [dict(item) for item in instruments if item["class_id"] == class_id]
        reasons = [
            "POINT_IN_TIME_EXIT_TERMINATION_AND_SECURITY_LINEAGE_CONTRACT_INCOMPLETE",
            "DISTRIBUTION_AND_TOTAL_OUTCOME_CONTRACT_INCOMPLETE",
            "QUOTED_SPREAD_DEPTH_AND_AUCTION_HISTORY_NOT_AVAILABLE_IN_BOUND_ARCHIVE",
            "MICROSTRUCTURE_EVIDENCE_MODE_NOT_FROZEN",
            "KABU_K1_PRODUCTION_SYMBOL_NOT_VERIFIED_PER_PRODUCT",
            "KABU_K2_PRODUCTION_BOARD_REGISTER_AND_PUSH_NOT_VERIFIED_PER_PRODUCT",
            "KABU_K3_VALIDATION_ORDER_SCHEMA_NOT_VERIFIED_FOR_ALL_FROZEN_PROFILES",
            "KABU_K4_PRODUCT_ACCOUNT_CASH_ORDER_AND_SOR_ELIGIBILITY_NOT_VERIFIED",
        ]
        if any(
            not required_mapping(item, "index_methodology_identifier").get(
                "point_in_time_version_coverage_complete"
            )
            for item in members
        ):
            reasons.append("METHODOLOGY_PIT_VERSION_COVERAGE_INCOMPLETE_FOR_EVERY_MEMBER")
        if any(
            not required_mapping(item, "index_methodology_identifier").get(
                "benchmark_lineage_complete"
            )
            for item in members
        ):
            reasons.append("BENCHMARK_LINEAGE_INCOMPLETE_FOR_EVERY_MEMBER")
        if any(item.get("classification_status") != "CONFIRMED" for item in members):
            reasons.append("ECONOMIC_CLASSIFICATION_PENDING_FOR_SOME_MEMBERS")
        if not members:
            reasons.append("NO_EXACT_SNAPSHOT_MEMBER")
        result.append(
            {
                "class_id": class_id,
                "definition": definitions[class_id],
                "snapshot_member_count": len(members),
                "decision": "NO_GO_PHASE3_DATA_FOUNDATION",
                "decision_reasons": reasons,
                "instruments": members,
            }
        )
    return result


def build_run_manifest(
    *,
    authorization_path: Path,
    source_manifest_path: Path,
    normalized_manifest_path: Path,
    inventory_path: Path,
    semantic_correction_path: Path,
    postreview_correction_path: Path,
    source_manifest: Mapping[str, Any],
    bar_partitions: Sequence[Mapping[str, Any]],
    master_partitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "manifest_version": 1,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "research_only": True,
        "paper_live_enabled": False,
        "performance_values_computed": False,
        "broker_endpoint_called": False,
        "inputs": {
            "authorization": file_record(authorization_path),
            "semantic_correction": file_record(semantic_correction_path),
            "postreview_semantic_correction": file_record(postreview_correction_path),
            "source_manifest": file_record(source_manifest_path),
            "normalized_manifest": file_record(normalized_manifest_path),
            "official_primary_sources": [
                {
                    "source_id": required_text(record, "source_id"),
                    "path": required_text(record, "path"),
                    "sha256": required_sha256(record, "sha256"),
                    "byte_size": required_int(record, "byte_size"),
                }
                for record in required_source_list(source_manifest)
            ],
            "bar_partitions": serializable_partitions(bar_partitions),
            "master_partitions": serializable_partitions(master_partitions),
        },
        "builder": file_record(Path(__file__).resolve()),
        "outputs": {
            INVENTORY_FILENAME: file_record_with_logical_path(
                inventory_path,
                inventory_path.parent.with_name(inventory_path.parent.name.removesuffix(".tmp"))
                / inventory_path.name,
            )
        },
        "authority_boundary": {
            "phase3_or_later_started": False,
            "paper_or_live_modified": False,
        },
    }
    assert_active_manifest_excludes_superseded_artifacts(manifest)
    return manifest


def assert_active_manifest_excludes_superseded_artifacts(
    manifest: Mapping[str, Any],
) -> None:
    outputs = required_mapping(manifest, "outputs")
    for raw_record in outputs.values():
        record = as_mapping(raw_record, label="active manifest output")
        path = required_text(record, "path")
        if "superseded" in path.lower() or "invalid" in path.lower():
            raise InstrumentInventoryError("superseded artifact cannot enter active manifest")


def normalized_source_registry(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [source_provenance(record) for record in required_source_list(manifest)]


def source_provenance(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": required_text(record, "source_id"),
        "url": required_text(record, "url"),
        "effective_date": record.get("effective_date"),
        "effective_date_status": required_text(record, "effective_date_status"),
        "receipt_timestamp": required_text(record, "receipt_timestamp"),
        "sha256": required_sha256(record, "sha256"),
    }


def source_records_by_id(
    manifest: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in required_source_list(manifest):
        source_id = required_text(record, "source_id")
        if source_id in result:
            raise InstrumentInventoryError(f"duplicate source_id: {source_id}")
        result[source_id] = record
    return result


def required_source_list(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InstrumentInventoryError("source manifest has no sources")
    return [as_mapping(record, label="source") for record in sources]


def serializable_partitions(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": record["path"],
            "first_date": record["first_date"],
            "last_date": record["last_date"],
            "row_count": record["row_count"],
            "byte_size": record["byte_size"],
            "sha256": record["sha256"],
        }
        for record in records
    ]


def assert_no_prohibited_output_keys(value: Any, *, prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).lower()
            path = f"{prefix}.{key}" if prefix else key
            prohibited = any(part in key for part in FORBIDDEN_OUTPUT_KEY_PARTS)
            if prohibited and child is not False and child != []:
                raise InstrumentInventoryError(f"prohibited output key: {path}")
            assert_no_prohibited_output_keys(child, prefix=path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_prohibited_output_keys(child, prefix=f"{prefix}[{index}]")


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    if output_dir.exists():
        raise InstrumentInventoryError(f"refusing to overwrite existing output: {output_dir}")
    if temporary_dir.exists():
        raise InstrumentInventoryError(f"temporary output already exists: {temporary_dir}")


def latest_partition(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(records, key=lambda item: str(item["last_date"]))


def assert_unique_records(records: Sequence[Mapping[str, Any]], *, key: str, label: str) -> None:
    values = [record.get(key) for record in records]
    if len(values) != len(set(values)):
        raise InstrumentInventoryError(f"duplicate {key} in {label}")


def assert_unique_keys(frame: pl.DataFrame, keys: tuple[str, ...], *, label: str) -> None:
    duplicates = frame.group_by(list(keys)).len().filter(pl.col("len") != 1)
    if not duplicates.is_empty():
        raise InstrumentInventoryError(f"{label} contains duplicate keys")


def ensure_code_coverage(
    records: Mapping[str, Any], candidate_codes: Sequence[str], *, label: str
) -> None:
    missing = sorted(set(candidate_codes) - set(records))
    if missing:
        raise InstrumentInventoryError(f"{label} coverage missing codes: {missing}")


def parse_english_listing_date(value: str) -> date:
    normalized = value.replace("Sept.", "Sep.").replace(".", "")
    try:
        return datetime.strptime(normalized, "%b %d, %Y").date()
    except ValueError as exc:
        raise InstrumentInventoryError(f"invalid listing date: {value}") from exc


def normalize_source_code(value: str) -> str:
    code = normalize_space(value).upper()
    if not re.fullmatch(r"[0-9A-Z]{4}", code):
        raise InstrumentInventoryError(f"invalid JPX source code: {value}")
    return code


def jquants_code(source_code: str) -> str:
    return normalize_source_code(source_code) + "0"


def clean_fund_name(value: str) -> str:
    result = re.sub(r"\s+(?:Indicative NAV|iNAV)$", "", normalize_space(value))
    if not result:
        raise InstrumentInventoryError("empty fund name")
    return result


def positive_int(value: str, *, label: str) -> int:
    normalized = normalize_space(value).replace(",", "")
    if not normalized.isdigit() or int(normalized) <= 0:
        raise InstrumentInventoryError(f"invalid {label}: {value}")
    return int(normalized)


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def source_host(url: str) -> str:
    match = re.fullmatch(r"https://([^/]+)(?:/.*)?", url)
    if match is None:
        raise InstrumentInventoryError(f"source URL is not HTTPS: {url}")
    return match.group(1).lower()


def resolve_repository_path(value: str) -> Path:
    path = Path(value)
    resolved = (REPOSITORY_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if resolved != REPOSITORY_ROOT and REPOSITORY_ROOT not in resolved.parents:
        raise InstrumentInventoryError(f"repository path escaped root: {value}")
    return resolved


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise InstrumentInventoryError(f"{label} does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InstrumentInventoryError(f"{label} must be a JSON object")
    return value


def as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstrumentInventoryError(f"{label} must be an object")
    return value


def required_mapping(record: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    return as_mapping(record.get(key), label=key)


def required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise InstrumentInventoryError(f"{key} must be non-empty text")
    return value


def required_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InstrumentInventoryError(f"{key} must be an integer")
    return value


def required_sha256(record: Mapping[str, Any], key: str) -> str:
    value = required_text(record, key)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InstrumentInventoryError(f"{key} is not a SHA-256 hex digest")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": repository_relative(path),
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def file_record_with_logical_path(path: Path, logical_path: Path) -> dict[str, Any]:
    return {
        "path": repository_relative(logical_path),
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def repository_relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(resolved)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
