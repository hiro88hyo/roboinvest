from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "build-imom6m-features.py"
    spec = importlib.util.spec_from_file_location("build_imom6m_features", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module()
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "research/imom/imom6m-top5-fixed20-v0.json"
AUTHORIZATION_PATH = ROOT / "research/imom/imom6m-top5-fixed20-v0-phase1-authorization.json"
NORMALIZED_MANIFEST_PATH = ROOT / "data/liquidity-research-normalized-v0/normalized-manifest.json"
EXPECTED_OUTPUT = ROOT / "data/imom6m-features-v0"
MONTH_ENDS = [
    date(2022, 1, 31),
    date(2022, 2, 28),
    date(2022, 3, 31),
    date(2022, 4, 28),
    date(2022, 5, 31),
    date(2022, 6, 30),
    date(2022, 7, 29),
]
JULY_SESSIONS = [date(2022, 7, day) for day in range(1, 20)] + [MONTH_ENDS[-1]]


def _config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _master_row(
    code: str,
    *,
    market_code: str = "0111",
    product_category: str = "011",
) -> dict[str, object]:
    return {
        "as_of_date": MONTH_ENDS[-1],
        "code": code,
        "company_name": code,
        "company_name_en": code,
        "product_category": product_category,
        "market_code": market_code,
        "market_name": "プライム",
        "margin_code": "2",
        "margin_name": "貸借",
        "sector17_code": "1",
        "sector17_name": "食品",
        "sector33_code": "0050",
        "sector33_name": "水産・農林業",
        "scale_category": "TOPIX Small 2",
    }


def _frames(
    *,
    returns_by_code: dict[str, list[float]],
    turnover_by_code: dict[str, float] | None = None,
    market_by_code: dict[str, str] | None = None,
    product_by_code: dict[str, str] | None = None,
    omit_month_end_by_code: dict[str, date] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    turnover_by_code = turnover_by_code or {}
    market_by_code = market_by_code or {}
    product_by_code = product_by_code or {}
    omit_month_end_by_code = omit_month_end_by_code or {}
    bars: list[dict[str, object]] = []
    master: list[dict[str, object]] = []
    for code, monthly_returns in returns_by_code.items():
        assert len(monthly_returns) == 6
        closes = [1000.0]
        for monthly_return in monthly_returns:
            closes.append(closes[-1] * (1 + monthly_return))
        month_end_close = dict(zip(MONTH_ENDS, closes, strict=True))
        dates = sorted(set(MONTH_ENDS + JULY_SESSIONS))
        for current_date in dates:
            if omit_month_end_by_code.get(code) == current_date:
                continue
            close = month_end_close.get(current_date, closes[-2])
            bars.append(
                {
                    "date": current_date,
                    "code": code,
                    "adjusted_close": close,
                    "turnover_jpy": turnover_by_code.get(code, 100_000_000.0),
                }
            )
        master.append(
            _master_row(
                code,
                market_code=market_by_code.get(code, "0111"),
                product_category=product_by_code.get(code, "011"),
            )
        )
    return pl.DataFrame(bars), pl.DataFrame(master)


def _cohort(
    bars: pl.DataFrame,
    master: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    return builder.build_feature_cohort(bars=bars, master=master, config=_config())


def test_feature_matches_registered_formula_without_skipping_latest_month() -> None:
    monthly_returns = [0.10, -0.05, 0.03, 0.02, -0.01, 0.04]
    bars, master = _frames(returns_by_code={"10000": monthly_returns})

    cohort, _ = _cohort(bars, master)

    expected_mom = 100 * (math.prod(1 + value for value in monthly_returns) - 1)
    expected_sum = 100 * sum(monthly_returns)
    row = cohort.row(0, named=True)
    assert row["valid_monthly_return_count"] == 6
    assert row["mom6m_no_skip_v0"] == pytest.approx(expected_mom)
    assert row["sum6m_no_skip_v0"] == pytest.approx(expected_sum)
    assert row["imom6m_no_skip_v0"] == pytest.approx(expected_mom - expected_sum)
    assert row["eligible"] is True

    changed_bars, changed_master = _frames(returns_by_code={"10000": [*monthly_returns[:-1], 0.20]})
    changed, _ = _cohort(changed_bars, changed_master)
    assert changed.get_column("imom6m_no_skip_v0").item() != pytest.approx(row["imom6m_no_skip_v0"])


def test_missing_actual_global_month_end_is_not_filled_from_prior_date() -> None:
    returns = [0.01] * 6
    bars, master = _frames(
        returns_by_code={"10000": returns, "20000": returns},
        omit_month_end_by_code={"10000": MONTH_ENDS[-1]},
    )
    bars = bars.vstack(
        pl.DataFrame(
            [
                {
                    "date": date(2022, 7, 28),
                    "code": "10000",
                    "adjusted_close": 1060.0,
                    "turnover_jpy": 100_000_000.0,
                }
            ]
        )
    )

    cohort, _ = _cohort(bars, master)
    target = cohort.filter(pl.col("code") == "10000").row(0, named=True)

    assert target["imom6m_no_skip_v0"] is None
    assert target["eligibility_reason"] == "INSUFFICIENT_CONSECUTIVE_MONTH_END_RETURNS"


def test_gap_in_global_month_index_is_not_bridged() -> None:
    returns = [0.01] * 6
    bars, master = _frames(
        returns_by_code={"10000": returns, "20000": returns},
        omit_month_end_by_code={"10000": MONTH_ENDS[3]},
    )

    cohort, _ = _cohort(bars, master)
    target = cohort.filter(pl.col("code") == "10000").row(0, named=True)

    assert target["valid_monthly_return_count"] < 6
    assert target["imom6m_no_skip_v0"] is None


@pytest.mark.parametrize(
    ("code", "turnover", "market", "product", "reason"),
    [
        ("10000", 49_999_999.0, "0111", "011", "BELOW_MINIMUM_MEDIAN_TURNOVER"),
        ("20000", 100_000_000.0, "0105", "011", "DISALLOWED_MARKET"),
        ("30000", 100_000_000.0, "0111", "ETF", "DISALLOWED_PRODUCT_CATEGORY"),
    ],
)
def test_universe_exclusions_are_preserved_with_reason(
    code: str,
    turnover: float,
    market: str,
    product: str,
    reason: str,
) -> None:
    bars, master = _frames(
        returns_by_code={code: [0.01] * 6},
        turnover_by_code={code: turnover},
        market_by_code={code: market},
        product_by_code={code: product},
    )

    cohort, _ = _cohort(bars, master)

    assert cohort.get_column("eligibility_reason").item() == reason
    assert cohort.get_column("eligible").item() is False
    assert cohort.get_column("selection_rank").null_count() == 1


def test_deciles_use_registered_formula_and_code_tie_breaker() -> None:
    codes = [f"{index:04d}0" for index in range(1000, 1011)]
    bars, master = _frames(returns_by_code={code: [0.01] * 6 for code in codes})

    cohort, _ = _cohort(bars, master)
    ranked = cohort.sort("selection_rank")

    assert ranked.get_column("code").to_list() == codes
    assert ranked.get_column("imom_decile").to_list() == [10, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert ranked.filter(pl.col("decile_10_candidate")).height == 2
    builder.validate_built_cohort(cohort)


def test_calendar_uses_actual_max_available_date_per_month() -> None:
    bars, _ = _frames(returns_by_code={"10000": [0.01] * 6})

    calendar = builder.derive_global_month_end_calendar(bars)

    assert calendar.get_column("signal_date").to_list() == MONTH_ENDS
    assert calendar.get_column("global_month_index").to_list() == list(range(7))


def test_audit_has_features_and_counts_but_no_outcomes() -> None:
    bars, master = _frames(returns_by_code={"10000": [0.01] * 6})
    cohort, calendar = _cohort(bars, master)

    audit = builder.build_audit(cohort=cohort, calendar=calendar)

    assert audit["feature_computed"] is True
    assert audit["next_month_returns_computed"] is False
    assert audit["gate_a_computed"] is False
    assert audit["gate_b_computed"] is False
    assert audit["development_outcomes_inspected"] is False
    assert audit["validation_outcomes_inspected"] is False
    assert audit["locked_oos_outcomes_inspected"] is False
    assert audit["row_count"] == 1


@pytest.mark.parametrize(
    "column",
    ["next_month_return", "forward_return_20d", "rank_ic", "profit_factor"],
)
def test_outcome_like_columns_are_rejected(column: str) -> None:
    with pytest.raises(builder.ImomFeatureBuildError, match="outcome-like"):
        builder.assert_outcome_blind_columns(["signal_date", column])


def test_config_feature_or_trial_limit_drift_is_rejected(tmp_path: Path) -> None:
    skip_config = copy.deepcopy(_config())
    skip_config["feature"]["skip_most_recent_month"] = True
    skip_path = tmp_path / "skip.json"
    skip_path.write_text(json.dumps(skip_config), encoding="utf-8")
    with pytest.raises(builder.ImomFeatureBuildError, match="feature definition"):
        builder.load_config(skip_path)

    trial_config = copy.deepcopy(_config())
    trial_config["research_cycle"]["maximum_candidates"] = 3
    trial_path = tmp_path / "trial.json"
    trial_path.write_text(json.dumps(trial_config), encoding="utf-8")
    with pytest.raises(builder.ImomFeatureBuildError, match="trial-limit"):
        builder.load_config(trial_path)


def test_authorization_verifies_all_three_bound_inputs(tmp_path: Path) -> None:
    authorization = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    authorization["bound_inputs"]["trial_registry"]["sha256"] = "0" * 64
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(authorization), encoding="utf-8")

    with pytest.raises(builder.ImomFeatureBuildError, match="trial-registry hash"):
        builder.load_and_verify_authorization(
            path,
            config_path=CONFIG_PATH,
            normalized_manifest_path=NORMALIZED_MANIFEST_PATH,
            output_dir=EXPECTED_OUTPUT,
        )


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "feature"
    output.mkdir()
    sentinel = output / "owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        builder.ensure_new_output_paths(
            output_dir=output,
            temporary_dir=tmp_path / "feature.tmp",
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
