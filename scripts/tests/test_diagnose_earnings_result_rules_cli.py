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
    path = Path(__file__).resolve().parents[1] / "diagnose-earnings-result-rules.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("diagnose_earnings_result_rules", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


diagnose_earnings_result_rules = _load_module()


def _observation(idx: int, *, forecast_per: Decimal, return_20d: float) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        event_type=EventType.EARNINGS_RESULT,
        event_subtype="3QFinancialStatements_Consolidated_JP",
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            revised_forecast_eps=FeatureValue(value=Decimal("100"), valid=True),
            eps_latest=FeatureValue(value=Decimal("80"), valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value=forecast_per, valid=True),
            forecast_per_valid=True,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000, valid=True),
            atr_pct_14d=FeatureValue(value=Decimal("0.02"), valid=True),
            return_20d=FeatureValue(value=Decimal("0.05"), valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": return_20d / 10,
            "forward_return_5d": return_20d / 4,
            "forward_return_10d": return_20d / 2,
            "forward_return_20d": return_20d,
            "catastrophic_stop_return_10d": return_20d / 2,
            "catastrophic_stop_return_20d": return_20d,
        },
    )


def test_earnings_result_rule_diagnostics_cli(tmp_path: Path, monkeypatch) -> None:
    observations_path = tmp_path / "observations.jsonl"
    output_json = tmp_path / "diagnostics.json"
    output_csv = tmp_path / "diagnostics.csv"
    rows = [
        _observation(0, forecast_per=Decimal("12"), return_20d=0.10),
        _observation(1, forecast_per=Decimal("35"), return_20d=-0.05),
    ]
    observations_path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose-earnings-result-rules.py",
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

    assert diagnose_earnings_result_rules.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    rows_by_key = {
        (row["rule_name"], row["exit_arm"]): row
        for row in report["rows"]
        if row["exit_arm"] == "fixed_20d"
    }
    assert report["summary"]["selected_observation_count"] == 2
    assert (
        rows_by_key[("earnings_quality_deep_value_plus_technical", "fixed_20d")]["trade_count"] == 1
    )
    assert output_csv.exists()
