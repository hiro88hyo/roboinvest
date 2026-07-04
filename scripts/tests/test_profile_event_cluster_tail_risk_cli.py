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
    path = Path(__file__).resolve().parents[1] / "profile-event-cluster-tail-risk.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("profile_event_cluster_tail_risk", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


profile_event_cluster_tail_risk = _load_module()


def _observation(
    idx: int,
    *,
    trade_group: str,
    event_type: EventType,
    subtype: str | None,
    avg_turnover: Decimal,
    forecast_per: Decimal | None,
    forward_return: float,
) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(minutes=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=trade_group,
        trade_group_id=trade_group,
        symbol="7203",
        event_type=event_type,
        event_subtype=subtype,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value=forecast_per, valid=forecast_per is not None),
            forecast_per_valid=forecast_per is not None,
            forecast_dividend_yield=FeatureValue(value=Decimal("0.04"), valid=True),
            dividend_yield_valid=True,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=avg_turnover, valid=True),
            atr_pct_14d=FeatureValue(value=Decimal("0.02"), valid=True),
            return_20d=FeatureValue(value=Decimal("0.05"), valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_20d": forward_return,
            "catastrophic_stop_return_20d": forward_return,
            "catastrophic_stop_exit_reason_20d": "fixed_exit",
        },
    )


def test_event_cluster_tail_risk_profile_cli(tmp_path: Path, monkeypatch) -> None:
    observations_path = tmp_path / "observations.jsonl"
    output_json = tmp_path / "tail-risk.json"
    output_csv = tmp_path / "tail-risk.csv"
    rows = [
        _observation(
            0,
            trade_group="trade-a",
            event_type=EventType.EARNINGS_RESULT,
            subtype=None,
            avg_turnover=Decimal("300000000"),
            forecast_per=Decimal("12"),
            forward_return=0.05,
        ),
        _observation(
            1,
            trade_group="trade-a",
            event_type=EventType.DIVIDEND_REVISION,
            subtype="increase",
            avg_turnover=Decimal("300000000"),
            forecast_per=Decimal("12"),
            forward_return=0.05,
        ),
        _observation(
            2,
            trade_group="trade-b",
            event_type=EventType.EARNINGS_RESULT,
            subtype=None,
            avg_turnover=Decimal("10000000"),
            forecast_per=None,
            forward_return=-0.10,
        ),
        _observation(
            3,
            trade_group="trade-b",
            event_type=EventType.DIVIDEND_REVISION,
            subtype="increase",
            avg_turnover=Decimal("10000000"),
            forecast_per=None,
            forward_return=-0.10,
        ),
    ]
    observations_path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile-event-cluster-tail-risk.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
        ],
    )

    assert profile_event_cluster_tail_risk.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["paper_live_enabled"] is False
    assert report["selected_trade_count"] == 2
    rows_by_key = {(row["dimension"], row["bucket"]): row for row in report["rows"]}
    assert rows_by_key[("avg_turnover_20d", "gte_200m")]["trade_count"] == 1
    assert rows_by_key[("forecast_per", "missing")]["trade_count"] == 1
    assert output_csv.exists()
