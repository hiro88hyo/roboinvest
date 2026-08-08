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
    path = Path(__file__).resolve().parents[1] / "evaluate-imom6m-gate-a-development.py"
    spec = importlib.util.spec_from_file_location(
        "evaluate_imom6m_gate_a_development",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = _load_module()
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "research/imom/imom6m-top5-fixed20-v0.json"
AUTHORIZATION_PATH = ROOT / "research/imom/imom6m-top5-fixed20-v0-gate-a-authorization.json"
FEATURE_MANIFEST_PATH = ROOT / "data/imom6m-features-v0/feature-manifest.json"
NORMALIZED_MANIFEST_PATH = ROOT / "data/liquidity-research-normalized-v0/normalized-manifest.json"
EXPECTED_OUTPUT = ROOT / "out/imom6m-gate-a-development-v0"


def _config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _formation_features(
    formation_date: date,
    *,
    count: int = 10,
    available: bool = True,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for index in range(count):
        rank = index + 1
        rows.append(
            {
                "signal_date": formation_date,
                "code": f"{1000 + index:04d}0",
                "imom6m_no_skip_v0": float(count - index) if available else None,
                "eligible": available,
                "selection_rank": rank if available else None,
                "eligible_cross_section_count": count if available else None,
                "imom_decile": (10 - ((rank - 1) * 10 // count) if available else None),
                "decile_10_candidate": rank <= -(-count // 10) if available else False,
                "research_split": "development",
            }
        )
    return pl.DataFrame(rows)


def _prices(
    formation_date: date,
    outcome_date: date,
    *,
    count: int = 10,
    missing_outcome_index: int | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for index in range(count):
        code = f"{1000 + index:04d}0"
        next_return = (count - index) / 100
        rows.append({"date": formation_date, "code": code, "adjusted_close": 100.0})
        rows.append(
            {
                "date": outcome_date,
                "code": code,
                "adjusted_close": (
                    None if index == missing_outcome_index else 100.0 * (1 + next_return)
                ),
            }
        )
    return pl.DataFrame(rows)


def test_month_uses_equal_weight_extreme_deciles_and_average_rank_ic() -> None:
    formation = date(2023, 1, 31)
    outcome = date(2023, 2, 28)

    monthly = evaluator.evaluate_months(
        features=_formation_features(formation),
        prices=_prices(formation, outcome),
        calendar=[formation, outcome],
        split_start=formation,
        split_end=outcome,
        minimum_eligible=10,
    )

    assert len(monthly) == 1
    row = monthly[0]
    assert row["complete"] is True
    assert row["decile_10_count"] == 1
    assert row["decile_1_count"] == 1
    assert row["decile_10_return"] == pytest.approx(0.10)
    assert row["decile_1_return"] == pytest.approx(0.01)
    assert row["decile_10_minus_decile_1_return"] == pytest.approx(0.09)
    assert row["rank_ic"] == pytest.approx(1.0)


def test_missing_exact_endpoint_makes_entire_month_incomplete() -> None:
    formation = date(2023, 1, 31)
    prior_to_outcome = date(2023, 2, 27)
    outcome = date(2023, 2, 28)
    prices = _prices(formation, outcome, missing_outcome_index=0).vstack(
        pl.DataFrame(
            [
                {
                    "date": prior_to_outcome,
                    "code": "10000",
                    "adjusted_close": 999.0,
                }
            ]
        )
    )

    row = evaluator.evaluate_months(
        features=_formation_features(formation),
        prices=prices,
        calendar=[formation, outcome],
        split_start=formation,
        split_end=outcome,
        minimum_eligible=10,
    )[0]

    assert row["complete"] is False
    assert row["missing_outcome_count"] == 1
    assert row["incomplete_reason"] == "MISSING_OR_NONPOSITIVE_EXACT_ENDPOINT"
    assert row["decile_10_return"] is None
    assert row["rank_ic"] is None


def test_burn_in_and_post_development_formations_are_not_evaluated() -> None:
    burn_in = date(2022, 1, 31)
    formation = date(2022, 2, 28)
    outcome = date(2022, 3, 31)
    post_split = date(2022, 4, 28)
    features = pl.concat(
        [
            _formation_features(burn_in, available=False),
            _formation_features(formation),
            _formation_features(outcome),
        ],
        how="vertical_relaxed",
    )
    prices = pl.concat(
        [
            _prices(formation, outcome),
            _prices(outcome, post_split),
        ]
    ).unique(["date", "code"], keep="first")

    monthly = evaluator.evaluate_months(
        features=features,
        prices=prices,
        calendar=[burn_in, formation, outcome, post_split],
        split_start=burn_in,
        split_end=outcome,
        minimum_eligible=10,
    )

    assert [row["formation_date"] for row in monthly] == [formation.isoformat()]


def test_rank_ties_use_average_ranks() -> None:
    assert evaluator.average_ranks([1.0, 1.0, 3.0, 3.0]) == [1.5, 1.5, 3.5, 3.5]
    assert evaluator.spearman_average_rank(
        [1.0, 1.0, 3.0, 3.0],
        [10.0, 10.0, 20.0, 20.0],
    ) == pytest.approx(1.0)
    assert (
        evaluator.spearman_average_rank(
            [1.0, 1.0],
            [2.0, 3.0],
        )
        is None
    )


def test_aggregate_halves_and_largest_spread_removal_are_deterministic() -> None:
    spreads = [0.03, 0.02, -0.01, 0.03]
    monthly = [
        {
            "formation_date": date(2023, month, 28).isoformat(),
            "complete": True,
            "decile_10_return": 0.04,
            "decile_10_minus_decile_1_return": spread,
            "rank_ic": 0.10,
        }
        for month, spread in enumerate(spreads, start=1)
    ]

    metrics, removed = evaluator.aggregate_complete_months(monthly)

    assert removed == "2023-01-28"
    assert metrics["mean_decile_10_return"] == pytest.approx(0.04)
    assert metrics["mean_decile_10_minus_decile_1_return"] == pytest.approx(0.0175)
    assert metrics["mean_spread_first_half"] == pytest.approx(0.025)
    assert metrics["mean_spread_second_half"] == pytest.approx(0.01)
    assert metrics["mean_spread_after_largest_month_removed"] == pytest.approx(
        (0.02 - 0.01 + 0.03) / 3
    )


def test_gate_comparisons_are_strict() -> None:
    contract = _config()["gate_a_source_structure_diagnostic"]["pass_requires_all"]
    zero_metrics = {key: 0.0 for key in evaluator.empty_metrics()}

    failed = evaluator.evaluate_gates(
        metrics=zero_metrics,
        complete_month_count=24,
        pass_contract=contract,
    )
    passed = evaluator.evaluate_gates(
        metrics={key: 0.001 for key in evaluator.empty_metrics()},
        complete_month_count=24,
        pass_contract=contract,
    )

    assert failed["checks"]["minimum_boundary_complete_months"]["passed"] is True
    assert failed["all_passed"] is False
    assert passed["all_passed"] is True


def test_formation_rank_or_decile_drift_is_rejected() -> None:
    features = _formation_features(date(2023, 1, 31)).with_columns(
        pl.when(pl.col("selection_rank") == 1)
        .then(9)
        .otherwise(pl.col("imom_decile"))
        .alias("imom_decile")
    )

    with pytest.raises(evaluator.GateAEvaluationError, match="deciles drifted"):
        evaluator.validate_formation_ranks(features)


@pytest.mark.parametrize(
    "column",
    ["next_month_return", "rank_ic", "profit_factor", "maximum_drawdown"],
)
def test_outcome_columns_in_feature_artifact_are_rejected(column: str) -> None:
    with pytest.raises(evaluator.GateAEvaluationError, match="outcome-like"):
        evaluator.assert_outcome_blind_columns(["signal_date", column])


def test_config_and_trial_limit_drift_are_rejected(tmp_path: Path) -> None:
    config = copy.deepcopy(_config())
    config["gate_a_source_structure_diagnostic"]["portfolio"][
        "minimum_boundary_complete_months"
    ] = 12
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(evaluator.GateAEvaluationError, match="portfolio contract"):
        evaluator.load_config(gate_path)

    config = copy.deepcopy(_config())
    config["research_cycle"]["maximum_candidates"] = 3
    cycle_path = tmp_path / "cycle.json"
    cycle_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(evaluator.GateAEvaluationError, match="trial-limit"):
        evaluator.load_config(cycle_path)


def test_authorization_hash_drift_is_rejected(tmp_path: Path) -> None:
    authorization = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    authorization["bound_inputs"]["phase1_completion"]["sha256"] = "0" * 64
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(authorization), encoding="utf-8")

    with pytest.raises(evaluator.GateAEvaluationError, match="phase1_completion hash"):
        evaluator.load_and_verify_authorization(
            path,
            config_path=CONFIG_PATH,
            feature_manifest_path=FEATURE_MANIFEST_PATH,
            normalized_manifest_path=NORMALIZED_MANIFEST_PATH,
            output_dir=EXPECTED_OUTPUT,
        )


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "gate-a"
    output.mkdir()
    sentinel = output / "owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        evaluator.ensure_new_output_paths(
            output_dir=output,
            temporary_dir=tmp_path / "gate-a.tmp",
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
