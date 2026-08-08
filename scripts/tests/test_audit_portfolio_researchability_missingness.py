from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "audit-portfolio-researchability-missingness.py"
    spec = importlib.util.spec_from_file_location(
        "audit_portfolio_researchability_missingness",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


auditor = _load_module()
ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION_PATH = (
    ROOT / "research/portfolio-researchability-reset-2026-v0/phase1-authorization.json"
)
EXPECTED_OUTPUT = (
    ROOT / "out/portfolio-researchability-reset-2026-v0/phase1-missingness-existing-archive-v0"
)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            {
                "outcome_date_fetch_available": False,
                "outcome_row_exists": False,
                "outcome_ohlcv_state": "ABSENT",
                "outcome_master_exists": False,
                "formation_product_category": "011",
                "outcome_product_category": None,
            },
            "ARCHIVE_FETCH_OR_INGEST_FAILURE",
        ),
        (
            {
                "outcome_date_fetch_available": True,
                "outcome_row_exists": True,
                "outcome_ohlcv_state": "COMPLETE_NULL",
                "outcome_master_exists": True,
                "formation_product_category": "011",
                "outcome_product_category": "011",
            },
            "TRADING_SUSPENSION_OR_NO_MONTH_END_TRADE",
        ),
        (
            {
                "outcome_date_fetch_available": True,
                "outcome_row_exists": False,
                "outcome_ohlcv_state": "ABSENT",
                "outcome_master_exists": True,
                "formation_product_category": "011",
                "outcome_product_category": "014",
            },
            "HISTORICAL_MASTER_PRODUCT_CATEGORY_MISMATCH",
        ),
        (
            {
                "outcome_date_fetch_available": True,
                "outcome_row_exists": False,
                "outcome_ohlcv_state": "ABSENT",
                "outcome_master_exists": True,
                "formation_product_category": "011",
                "outcome_product_category": "011",
            },
            "UNEXPLAINED_SOURCE_DATA_ABSENCE",
        ),
        (
            {
                "outcome_date_fetch_available": True,
                "outcome_row_exists": False,
                "outcome_ohlcv_state": "ABSENT",
                "outcome_master_exists": False,
                "formation_product_category": "011",
                "outcome_product_category": None,
            },
            "UNKNOWN",
        ),
    ],
)
def test_classification_precedence(arguments: dict[str, object], expected: str) -> None:
    assert auditor.classify_reason(**arguments) == expected


def test_disappearance_is_not_relabelled_as_delisting() -> None:
    reason = auditor.classify_reason(
        outcome_date_fetch_available=True,
        outcome_row_exists=False,
        outcome_ohlcv_state="ABSENT",
        outcome_master_exists=False,
        formation_product_category="011",
        outcome_product_category=None,
    )

    assert reason == "UNKNOWN"
    assert reason != "DELISTING_OR_TERMINAL_EVENT"


def test_reconstruction_uses_exact_endpoint_without_ratio() -> None:
    formation = date(2023, 1, 31)
    outcome = date(2023, 2, 28)
    features = pl.DataFrame(
        [
            {
                "signal_date": formation,
                "code": "10010",
                "imom6m_no_skip_v0": 1.0,
                "eligible": True,
                "research_split": "development",
            },
            {
                "signal_date": formation,
                "code": "10020",
                "imom6m_no_skip_v0": 2.0,
                "eligible": True,
                "research_split": "development",
            },
        ]
    )
    bars = pl.DataFrame(
        [
            {"date": formation, "code": "10010", "adjusted_close": 100.0},
            {"date": outcome, "code": "10010", "adjusted_close": 110.0},
            {"date": formation, "code": "10020", "adjusted_close": 200.0},
            {"date": outcome, "code": "10020", "adjusted_close": None},
        ]
    )

    missing = auditor.reconstruct_missing_cases(
        features=features,
        attempted_dates=[formation],
        date_pairs={formation: outcome},
        endpoint_bars=bars,
    )

    assert missing == [
        {
            "formation_date": "2023-01-31",
            "outcome_date": "2023-02-28",
            "code": "10020",
            "formation_endpoint_valid": True,
            "outcome_endpoint_valid": False,
        }
    ]
    assert all("return" not in key and "price" not in key for key in missing[0])


def test_nearest_valid_dates_never_substitutes_endpoint() -> None:
    prior, later = auditor.nearest_valid_dates(
        [date(2023, 2, 27), date(2023, 3, 1)],
        target=date(2023, 2, 28),
    )

    assert prior == date(2023, 2, 27)
    assert later == date(2023, 3, 1)


def test_ohlcv_state_requires_complete_null_or_positive_group() -> None:
    null_row = {
        key: None
        for key in (
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
    }
    complete_row = {key: 1.0 for key in null_row}
    partial_row = {**complete_row, "adjusted_close": None}

    assert auditor.ohlcv_state(null_row) == "COMPLETE_NULL"
    assert auditor.ohlcv_state(complete_row) == "COMPLETE_POSITIVE_FINITE"
    assert auditor.ohlcv_state(partial_row) == "PARTIAL_OR_INVALID"


def test_authorization_scope_is_closed_and_path_bound() -> None:
    value = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    loaded = auditor.load_and_verify_authorization(
        AUTHORIZATION_PATH,
        output_dir=EXPECTED_OUTPUT,
    )

    assert loaded == value
    assert loaded["scope"]["fetch_external_or_additional_data"] is False
    assert loaded["scope"]["compute_or_persist_symbol_returns"] is False


def test_authorization_rejects_open_scope(tmp_path: Path) -> None:
    value = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    drifted = copy.deepcopy(value)
    drifted["scope"]["fetch_external_or_additional_data"] = True
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(drifted), encoding="utf-8")

    with pytest.raises(auditor.MissingnessAuditError, match="scope drifted"):
        auditor.load_and_verify_authorization(path, output_dir=EXPECTED_OUTPUT)


@pytest.mark.parametrize(
    "key",
    ["symbol_return", "rank_ic", "trade_pnl", "profit_factor", "price_value"],
)
def test_performance_like_output_keys_are_rejected(key: str) -> None:
    with pytest.raises(auditor.MissingnessAuditError, match="prohibited"):
        auditor.assert_no_prohibited_output_keys({"case": {key: 1}})
