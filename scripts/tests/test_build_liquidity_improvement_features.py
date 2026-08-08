from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "build-liquidity-improvement-features.py"
    spec = importlib.util.spec_from_file_location(
        "build_liquidity_improvement_features",
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
CONFIG_PATH = ROOT / "research/liquidity/liqimp1m-logdiff-v0.json"


def _config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _frames(
    *,
    codes: list[str],
    market_by_code: dict[str, str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    start = date(2022, 1, 1)
    bars: list[dict[str, object]] = []
    master: list[dict[str, object]] = []
    market_by_code = market_by_code or {}
    for code_index, code in enumerate(codes):
        close = 1000.0
        for index in range(41):
            current_date = start + timedelta(days=index)
            if index:
                close *= math.exp(0.01)
            turnover = 100_000_000.0 if index <= 20 else (110_000_000.0 + code_index * 10_000_000.0)
            bars.append(
                {
                    "date": current_date,
                    "code": code,
                    "adjusted_close": close,
                    "turnover_jpy": turnover,
                }
            )
        master.append(
            {
                "as_of_date": start + timedelta(days=40),
                "code": code,
                "company_name": code,
                "company_name_en": code,
                "product_category": "011",
                "market_code": market_by_code.get(code, "0111"),
                "market_name": "プライム",
                "margin_code": "2",
                "margin_name": "貸借",
                "sector17_code": "1",
                "sector17_name": "食品",
                "sector33_code": "0050",
                "sector33_name": "水産・農林業",
                "scale_category": "TOPIX Small 2",
            }
        )
    return pl.DataFrame(bars), pl.DataFrame(master)


def test_feature_is_log_difference_of_fixed_20_session_means() -> None:
    bars, master = _frames(codes=["10000"])

    cohort = builder.build_feature_cohort(bars=bars, master=master, config=_config())

    feature = cohort.get_column("liqimp1m_logdiff_v0").item()
    assert feature == pytest.approx(math.log(1.1))
    assert cohort.get_column("eligible").item() is True
    assert cohort.get_column("top20_candidate").item() is True


def test_top_20_count_uses_ceiling_and_deterministic_rank() -> None:
    codes = [f"100{i}0" for i in range(6)]
    bars, master = _frames(codes=codes)

    cohort = builder.build_feature_cohort(bars=bars, master=master, config=_config())
    selected = cohort.filter(pl.col("top20_candidate")).sort("selection_rank")

    assert selected.height == 2
    assert selected.get_column("top20_candidate_count").unique().item() == 2
    assert selected.get_column("code").to_list() == [codes[-1], codes[-2]]


def test_disallowed_market_is_kept_with_reason_but_not_ranked() -> None:
    bars, master = _frames(codes=["10000"], market_by_code={"10000": "0105"})

    cohort = builder.build_feature_cohort(bars=bars, master=master, config=_config())

    assert cohort.get_column("eligibility_reason").item() == "DISALLOWED_MARKET"
    assert cohort.get_column("eligible").item() is False
    assert cohort.get_column("selection_rank").null_count() == 1


def test_null_bar_is_not_filled_and_breaks_required_observation_count() -> None:
    bars, master = _frames(codes=["10000"])
    bars = bars.with_columns(
        pl.when(pl.col("date") == date(2022, 1, 15))
        .then(None)
        .otherwise(pl.col("adjusted_close"))
        .alias("adjusted_close")
    )

    cohort = builder.build_feature_cohort(bars=bars, master=master, config=_config())

    assert cohort.get_column("liqimp1m_logdiff_v0").null_count() == 1
    assert cohort.get_column("eligibility_reason").item() == "INSUFFICIENT_VALID_FEATURE_WINDOW"


def test_config_drift_is_rejected(tmp_path: Path) -> None:
    config = copy.deepcopy(_config())
    config["feature"]["alternative_windows_or_signs_authorized"] = True
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(builder.FeatureBuildError, match="alternative"):
        builder.load_config(path)


def test_outcome_like_columns_are_rejected() -> None:
    with pytest.raises(builder.FeatureBuildError, match="outcome-like"):
        builder.assert_outcome_blind_columns(["signal_date", "forward_return_20d"])


def test_audit_contains_counts_but_no_outcomes() -> None:
    bars, master = _frames(codes=["10000", "10010"])
    cohort = builder.build_feature_cohort(bars=bars, master=master, config=_config())

    audit = builder.build_audit(cohort)

    assert audit["outcomes_computed"] is False
    assert audit["forward_returns_computed"] is False
    assert audit["locked_oos_outcomes_inspected"] is False
    assert audit["row_count"] == 2
    assert audit["per_signal_date"][0]["eligible_rows"] == 2


def test_existing_feature_output_is_not_overwritten(tmp_path: Path) -> None:
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
