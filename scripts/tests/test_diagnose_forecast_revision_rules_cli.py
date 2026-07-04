from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_contracts.event_research import (
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
    ValuationFeaturesV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "diagnose-forecast-revision-rules.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("diagnose_forecast_revision_rules", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


diagnose_forecast_revision_rules = _load_module()


def _observation(idx: int, *, profit_revision: Decimal, return_10d: float) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        event_type=EventType.FORECAST_REVISION,
        event_subtype="ForecastRevision_Consolidated_JP",
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=profit_revision, valid=True),
            operating_profit_revision_pct=FeatureValue(value=profit_revision, valid=True),
            sales_revision_pct=FeatureValue(value=profit_revision, valid=True),
            forecast_eps_revision_absolute=FeatureValue(value=profit_revision, valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value=Decimal("12"), valid=True),
            forecast_per_valid=True,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000, valid=True),
            atr_pct_14d=FeatureValue(value=Decimal("0.02"), valid=True),
            return_20d=FeatureValue(value=Decimal("0.05"), valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": return_10d / 5,
            "forward_return_5d": return_10d / 2,
            "forward_return_10d": return_10d,
            "forward_return_20d": return_10d,
            "catastrophic_stop_return_10d": return_10d,
            "catastrophic_stop_return_20d": return_10d,
        },
    )


def _write_jsonl(path: Path, rows: list[ObservationRecord]) -> None:
    path.write_text("\n".join(row.model_dump_json() for row in rows) + "\n", encoding="utf-8")


def _write_ohlcv_csv(path: Path) -> None:
    lines = ["symbol,date,open,high,low,close,volume,turnover"]
    start = datetime(2025, 10, 1, tzinfo=UTC).date()
    for idx in range(100):
        day = start + timedelta(days=idx)
        price = Decimal("1000") + Decimal(idx)
        lines.append(
            ",".join(
                [
                    "7203",
                    day.isoformat(),
                    str(price),
                    str(price + Decimal("10")),
                    str(price - Decimal("10")),
                    str(price + Decimal("2")),
                    "1000000",
                    str((price + Decimal("2")) * Decimal("1000000")),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_forecast_revision_rule_diagnostics_cli(tmp_path: Path, monkeypatch) -> None:
    observations_path = tmp_path / "observations.jsonl"
    output_json = tmp_path / "diagnostics.json"
    output_csv = tmp_path / "diagnostics.csv"
    _write_jsonl(
        observations_path,
        [
            _observation(0, profit_revision=Decimal("0.20"), return_10d=0.05),
            _observation(1, profit_revision=Decimal("-0.10"), return_10d=-0.03),
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose-forecast-revision-rules.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
            "--min-trades",
            "1",
        ],
    )

    assert diagnose_forecast_revision_rules.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    rows = {
        (row["rule_name"], row["exit_arm"]): row
        for row in report["rows"]
        if row["exit_arm"] == "fixed_10d"
    }
    assert report["summary"]["selected_observation_count"] == 2
    assert rows[("current_fundamental_plus_technical", "fixed_10d")]["trade_count"] == 1
    assert rows[("profit_op_sales_positive_plus_technical", "fixed_10d")]["trade_count"] == 1
    assert output_csv.exists()


def test_forecast_revision_rule_diagnostics_cli_with_exit_random_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "diagnostics-random.json"
    output_csv = tmp_path / "diagnostics-random.csv"
    _write_jsonl(
        observations_path,
        [
            _observation(0, profit_revision=Decimal("0.20"), return_10d=0.05),
            _observation(1, profit_revision=Decimal("-0.10"), return_10d=-0.03),
        ],
    )
    _write_ohlcv_csv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose-forecast-revision-rules.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
            "--min-trades",
            "1",
            "--ohlcv",
            str(ohlcv_path),
            "--random-baseline-rule",
            "current_fundamental_plus_technical",
            "--random-seeds",
            "3",
        ],
    )

    assert diagnose_forecast_revision_rules.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    random_report = report["exit_random_baselines"]
    rule_report = random_report["rules"]["current_fundamental_plus_technical"]
    fixed_5d = rule_report["baselines_by_exit"]["fixed_5d"]["same_symbol_random_date"]
    assert random_report["enabled"] is True
    assert rule_report["selected_observation_count"] == 1
    assert fixed_5d["random_count"] == 3
    assert fixed_5d["selected_percentile"] is not None
    assert rule_report["coverage"]["same_symbol_random_date"]["matched"] == 1
