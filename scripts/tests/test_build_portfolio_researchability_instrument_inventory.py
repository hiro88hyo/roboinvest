from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "build-portfolio-researchability-instrument-inventory.py"
    )
    spec = importlib.util.spec_from_file_location(
        "build_portfolio_researchability_instrument_inventory",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module()
ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION_PATH = (
    ROOT / "research/portfolio-researchability-reset-2026-v0/phase2-authorization.json"
)
SOURCE_DIR = ROOT / "data/portfolio-researchability-reset-2026-v0/phase2-primary-sources-v0"
EXPECTED_OUTPUT = (
    ROOT / "out/portfolio-researchability-reset-2026-v0/phase2-instrument-inventory-v0"
)


def test_parse_official_english_etf_table_without_nested_link_text(tmp_path: Path) -> None:
    path = tmp_path / "market.html"
    path.write_text(
        """
        <table><tr>
        <th>Listing Date</th><th>Index</th><th>Code </th><th>Fund Name</th>
        <th>Management Company</th><th>Trading Unit</th><th>Trust Fee</th>
        <th>Market Maker(*)</th></tr>
        <tr><td>Jan. 09, 2002</td><td>TOPIX</td><td>1308</td>
        <td>Listed Index Fund TOPIX <div><a>Indicative NAV</a></div></td>
        <td>Example Manager</td><td>1</td><td>0.046(*3)</td><td>●</td></tr>
        </table>
        """,
        encoding="utf-8",
    )

    rows = builder.parse_jpx_english_etf_rows(path)

    assert rows == [
        {
            "listing_date": "2002-01-09",
            "tracked_index_name": "TOPIX",
            "source_code": "1308",
            "security_code": "13080",
            "fund_name": "Listed Index Fund TOPIX",
            "management_company": "Example Manager",
            "trading_unit": 1,
            "trust_fee_source_text": "0.046(*3)",
        }
    ]


def test_parse_official_japanese_bond_table(tmp_path: Path) -> None:
    path = tmp_path / "bond.html"
    path.write_text(
        """
        <table><tr><th>連動対象指標</th><th>コード </th><th>名称</th>
        <th>管理会社<br>\uff08検索コード\uff09 </th><th>信託<br>報酬</th><th>補足</th></tr>
        <tr><td>FTSE日本国債0-1年インデックス</td><td>570A</td>
        <td>iシェアーズ 日本国債0-1年 ETF</td><td>管理会社(12345)</td>
        <td>0.07%</td><td>●</td></tr></table>
        """,
        encoding="utf-8",
    )

    rows = builder.parse_jpx_japanese_bond_rows(path)

    assert rows[0]["source_code"] == "570A"
    assert rows[0]["security_code"] == "570A0"
    assert rows[0]["listing_date"] is None
    assert rows[0]["trading_unit"] is None


@pytest.mark.parametrize(
    ("index_name", "fund_name", "expected"),
    [
        ("TOPIX", "Plain ETF", True),
        ("TOPIX Total Return Index", "Yearly Dividend Type ETF", True),
        ("TSE Growth Market 250 Index", "Growth Market ETF", True),
        ("TOPIX High Dividend Yield 40 Index", "Dividend ETF", False),
        ("TOPIX Minimum Variance Index", "Minimum Variance ETF", False),
        ("TOPIX Leveraged Index", "Leveraged ETF", False),
        ("TOPIX", "TOPIX Bear ETF", False),
    ],
)
def test_market_category_inventory_filter_is_mechanical(
    index_name: str, fund_name: str, expected: bool
) -> None:
    assert (
        builder.is_market_category_inventory_row(
            {"tracked_index_name": index_name, "fund_name": fund_name}
        )
        is expected
    )


def test_sector_classifier_requires_topix17_and_rejects_gearing() -> None:
    assert builder.is_sector_row(
        {"tracked_index_name": "TOPIX-17 FOODS Total Return Index", "fund_name": "ETF"}
    )
    assert builder.is_sector_row(
        {"tracked_index_name": "TOPIX Banks Total Return Index", "fund_name": "ETF"}
    )
    assert not builder.is_sector_row(
        {
            "tracked_index_name": "TOPIX-17 FOODS Leveraged Index",
            "fund_name": "ETF",
        }
    )
    assert not builder.is_sector_row({"tracked_index_name": "TOPIX", "fund_name": "ETF"})


def test_non_topix17_official_sector_row_is_kept_with_incomplete_methodology() -> None:
    fields = builder.sector_methodology_fields(
        {"tracked_index_name": "TOPIX Banks Total Return Index"}
    )

    assert fields["methodology_current_document_captured"] is False
    assert fields["methodology_pit_version_coverage_complete"] is False
    assert fields["benchmark_lineage_complete"] is False
    assert fields["methodology_source_ids"] == ["JPX_INDEX_LINEUP_HTML"]


def test_short_jgb_inventory_row_requires_exact_product_fact_and_rejects_fx_overlay() -> None:
    product = {
        "security_code": "570A",
        "maximum_remaining_maturity": "LESS_THAN_ONE_YEAR",
    }
    assert builder.is_short_jpy_government_bond_0_1y_row(
        {
            "source_code": "570A",
            "tracked_index_name": "FTSE日本国債0-1年インデックス",
            "fund_name": "iシェアーズ 日本国債0-1年 ETF",
        },
        product_570a=product,
    )
    assert not builder.is_short_jpy_government_bond_0_1y_row(
        {
            "source_code": "488A",
            "tracked_index_name": "FTSE円ドルロング日本国債0-1年(含短国)インデックス",
            "fund_name": "円高フォーカス ETF",
        },
        product_570a=product,
    )
    assert not builder.is_short_jpy_government_bond_0_1y_row(
        {
            "source_code": "570A",
            "tracked_index_name": "FTSE日本国債1-3年インデックス",
            "fund_name": "ETF",
        },
        product_570a=product,
    )


def test_classification_intersects_exact_snapshot_and_has_no_duplicate_class() -> None:
    base = {
        "listing_date": "2000-01-01",
        "fund_name": "ETF",
        "management_company": "Manager",
        "trading_unit": 1,
        "trust_fee_source_text": "fee text",
    }
    classified = builder.classify_source_rows(
        market_rows=[
            {
                **base,
                "tracked_index_name": "TOPIX",
                "source_code": "1308",
                "security_code": "13080",
            },
            {
                **base,
                "tracked_index_name": "Nikkei 225",
                "source_code": "9999",
                "security_code": "99990",
            },
        ],
        sector_rows=[
            {
                **base,
                "tracked_index_name": "TOPIX Banks Total Return Index",
                "source_code": "1615",
                "security_code": "16150",
            },
            {
                **base,
                "tracked_index_name": "TOPIX-17 FOODS Total Return Index",
                "source_code": "1617",
                "security_code": "16170",
            },
        ],
        bond_rows=[],
        product_570a={
            "security_code": "570A",
            "listing_date": "2026-05-27",
            "trading_unit": 10,
            "maximum_remaining_maturity": "LESS_THAN_ONE_YEAR",
        },
        snapshot_etf_codes={"13080", "16150", "16170"},
    )

    assert [(row["security_code"], row["class_id"]) for row in classified] == [
        ("13080", builder.CLASS_MARKET_CATEGORY),
        ("16150", builder.CLASS_SECTOR),
        ("16170", builder.CLASS_SECTOR),
    ]


def test_class_decision_stays_no_go_when_kabu_product_support_is_unverified() -> None:
    instrument = {
        "class_id": builder.CLASS_MARKET_CATEGORY,
        "classification_status": "PENDING",
        "index_methodology_identifier": {
            "point_in_time_version_coverage_complete": False,
            "benchmark_lineage_complete": False,
        },
    }

    classes = builder.build_classes([instrument])
    market = next(item for item in classes if item["class_id"] == builder.CLASS_MARKET_CATEGORY)

    assert market["decision"] == "NO_GO_PHASE3_DATA_FOUNDATION"
    assert "KABU_K1_PRODUCTION_SYMBOL_NOT_VERIFIED_PER_PRODUCT" in market["decision_reasons"]


def test_market_category_does_not_claim_broad_market_role() -> None:
    fields = builder.semantic_classification_fields(
        {
            "class_id": builder.CLASS_MARKET_CATEGORY,
        }
    )

    assert fields["source_category"] == "JPX_JAPANESE_EQUITY_MARKET"
    assert fields["economic_classification"]["value"] == "CLASSIFICATION_PENDING"
    assert fields["portfolio_role"]["value"] == "CLASSIFICATION_PENDING"
    assert fields["classification_status"] == "PENDING"


def test_topix17_is_sector_exposure_and_market_segment_is_rejected() -> None:
    classified = {
        "class_id": builder.CLASS_SECTOR,
        "source_code": "1617",
        "tracked_index_name": "TOPIX-17 FOODS Total Return Index",
    }
    fields = builder.semantic_classification_fields(classified)

    assert fields["economic_classification"]["value"] == "SECTOR_EXPOSURE"
    assert fields["economic_classification"]["exposure_dimension"] == "INDUSTRY_SECTOR"
    assert fields["economic_classification"]["index_family"] == "TOPIX_17"
    assert fields["economic_classification"]["sector_name"] == "FOODS"
    assert fields["portfolio_role"]["value"] == "SECTOR_EXPOSURE"
    assert fields["classification_status"] == "CONFIRMED"

    drifted = copy.deepcopy(fields)
    drifted["portfolio_role"]["value"] = "MARKET_SEGMENT"
    with pytest.raises(builder.InstrumentInventoryError, match="MARKET_SEGMENT"):
        builder.validate_semantic_classification(
            classified_row=classified,
            fields=drifted,
        )


def test_1615_confirms_sector_exposure_but_keeps_role_and_lineage_blocked() -> None:
    classified = {
        "class_id": builder.CLASS_SECTOR,
        "source_code": "1615",
        "tracked_index_name": "TOPIX Banks Total Return Index",
    }
    fields = builder.semantic_classification_fields(classified)

    assert fields["economic_classification"] == {
        "value": "SECTOR_EXPOSURE",
        "exposure_dimension": "INDUSTRY_SECTOR",
        "index_family": "TOPIX_33_SECTOR",
        "sector_name": "BANKS",
        "sector_name_source_text": "TOPIX Banks Total Return Index",
        "status": "CONFIRMED",
    }
    assert fields["portfolio_role"]["value"] == "CLASSIFICATION_PENDING"
    assert fields["portfolio_role"]["reason"] == "METHODOLOGY_PIT_LINEAGE_INCOMPLETE"
    assert fields["methodology_status"] == "PIT_INCOMPLETE"
    assert fields["benchmark_lineage_status"] == "INCOMPLETE"


def test_570a_is_unvalidated_cash_proxy_candidate_not_cash() -> None:
    fields = builder.semantic_classification_fields(
        {
            "class_id": builder.CLASS_SHORT_JGB,
        }
    )

    assert fields["economic_classification"]["value"] == "JPY_JAPAN_GOVERNMENT_BOND_0_1Y"
    assert fields["portfolio_role"]["value"] == "CASH_PROXY_CANDIDATE"
    assert fields["settlement_cash"] is False
    assert fields["strict_cash_equivalent"] is False
    assert fields["cash_proxy_status"] == "UNVALIDATED"


def test_kabu_gates_require_k1_to_k4_before_phase3_and_defer_k5() -> None:
    gates = builder.kabu_compatibility_gates()

    assert all(gates[key]["required_before_phase3"] is True for key in ("K1", "K2", "K3", "K4"))
    assert gates["K5"]["required_before_phase3"] is False
    assert gates["K5"]["required_before_live"] is True
    assert gates["K5"]["status"] == "NOT_STARTED_NOT_AUTHORIZED"
    assert gates["K5"]["stages"]["K5A"]["market_visible_and_accidental_fill_possible"] is True
    assert gates["K5"]["stages"]["K5B"]["actual_capital_at_risk"] is True
    assert gates["documented_shared_rest_push_registered_symbol_cap"] == 50
    assert gates["broker_endpoint_call_performed"] is False


def test_pending_or_incomplete_instrument_is_ineligible_for_active_subset() -> None:
    classified = {"class_id": builder.CLASS_MARKET_CATEGORY}
    semantic = builder.semantic_classification_fields(classified)

    eligibility = builder.phase3_eligibility_fields(
        classified_row=classified,
        semantic_fields=semantic,
    )

    assert eligibility["eligible_for_active_subset"] is False
    assert eligibility["eligible_for_phase3_input"] is False
    assert "ECONOMIC_CLASSIFICATION_PENDING" in eligibility["reasons"]


def test_prohibited_positive_output_key_is_rejected_but_false_boundary_flag_is_allowed() -> None:
    builder.assert_no_prohibited_output_keys({"portfolio_weight": False})
    with pytest.raises(builder.InstrumentInventoryError, match="prohibited output key"):
        builder.assert_no_prohibited_output_keys({"portfolio_weight": 0.5})


def test_authorization_scope_is_closed_and_path_bound() -> None:
    value = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    loaded = builder.load_and_verify_authorization(
        AUTHORIZATION_PATH,
        source_dir=SOURCE_DIR,
        output_dir=EXPECTED_OUTPUT,
    )

    assert loaded == value
    assert loaded["scope"]["compute_or_persist_returns_signals_or_portfolio_performance"] is False
    assert loaded["scope"]["start_phase3_or_later"] is False
    assert loaded["preexecution_contract"]["inventory_version"] == (
        builder.AUTHORIZED_BUILDER_VERSION
    )


def test_semantic_correction_is_bound_without_modifying_original_authorization() -> None:
    correction = builder.load_and_verify_semantic_correction(builder.SEMANTIC_CORRECTION_PATH)

    assert correction["record_id"] == builder.SEMANTIC_CORRECTION_ID
    assert correction["correction_boundary"]["original_authorization_record_modified"] is False


def test_postreview_correction_excludes_superseded_artifact_from_active_use() -> None:
    correction = builder.load_and_verify_postreview_correction(builder.POSTREVIEW_CORRECTION_PATH)

    assert correction["record_id"] == builder.POSTREVIEW_CORRECTION_ID
    assert correction["superseded_artifact"]["eligible_for_active_manifest"] is False
    assert correction["superseded_artifact"]["eligible_for_phase3_input"] is False


def test_active_manifest_rejects_superseded_output_path() -> None:
    with pytest.raises(builder.InstrumentInventoryError, match="superseded artifact"):
        builder.assert_active_manifest_excludes_superseded_artifacts(
            {
                "outputs": {
                    "instrument-inventory.json": {
                        "path": "out/example-superseded/instrument-inventory.json"
                    }
                }
            }
        )


def test_authorization_rejects_scope_or_output_drift(tmp_path: Path) -> None:
    value = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    drifted = copy.deepcopy(value)
    drifted["scope"]["rank_or_recommend_instruments"] = True
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")

    with pytest.raises(builder.InstrumentInventoryError, match="scope drifted"):
        builder.load_and_verify_authorization(
            path,
            source_dir=SOURCE_DIR,
            output_dir=EXPECTED_OUTPUT,
        )
    with pytest.raises(builder.InstrumentInventoryError, match="output directory"):
        builder.load_and_verify_authorization(
            AUTHORIZATION_PATH,
            source_dir=SOURCE_DIR,
            output_dir=tmp_path / "different-output",
        )
