from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import (
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
    ValuationFeaturesV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scan-event-rule-only-train.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("scan_event_rule_only_train", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan_event_rule_only_train = _load_module()


def _observation(
    idx: int,
    *,
    event_type: EventType = EventType.FORECAST_REVISION,
    event_subtype: str | None = None,
    trade_group_id: str | None = None,
) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=trade_group_id or f"cluster-{idx}",
        trade_group_id=trade_group_id,
        symbol="7203",
        event_type=event_type,
        event_subtype=event_subtype,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            revised_forecast_eps=FeatureValue(value="100", valid=True),
            profit_revision_pct=FeatureValue(value="0.10", valid=True),
            operating_profit_revision_pct=FeatureValue(value="0.08", valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value="12", valid=True),
            forecast_dividend_yield=FeatureValue(value="0.03", valid=True),
            forecast_per_valid=True,
            dividend_yield_valid=True,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value="300000000", valid=True),
            atr_pct_14d=FeatureValue(value="0.03", valid=True),
            return_20d=FeatureValue(value="0.05", valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": 0.01,
            "exit_date_2d": (at.date() + timedelta(days=2)).isoformat(),
            "forward_return_5d": 0.02,
            "exit_date_5d": (at.date() + timedelta(days=5)).isoformat(),
            "forward_return_10d": 0.03,
            "exit_date_10d": (at.date() + timedelta(days=10)).isoformat(),
            "forward_return_20d": 0.04,
            "exit_date_20d": (at.date() + timedelta(days=20)).isoformat(),
            "catastrophic_stop_return_10d": 0.03,
            "catastrophic_stop_return_20d": 0.04,
        },
    )


def _write_jsonl(path: Path, rows: list[ObservationRecord]) -> None:
    path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )


def test_rule_only_train_scan_reports_train_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = [_observation(idx) for idx in range(120)]
    observations[0] = _observation(
        0,
        event_type=EventType.EARNINGS_RESULT,
        trade_group_id="trade-cluster",
    )
    observations[1] = _observation(
        1,
        event_type=EventType.DIVIDEND_REVISION,
        event_subtype="increase",
        trade_group_id="trade-cluster",
    )
    observations_path = tmp_path / "observations.jsonl"
    output_json = tmp_path / "scan.json"
    output_csv = tmp_path / "scan.csv"
    _write_jsonl(observations_path, observations)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scan-event-rule-only-train.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--min-trades",
            "1",
        ],
    )

    assert scan_event_rule_only_train.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["purpose"] == "train_only_rule_screen_not_registered_strategy"
    assert report["summary"]["selected_train_observations"] < len(observations)
    assert report["summary"]["multi_event_train_cluster_count"] == 1
    cluster_fixed20 = next(
        row
        for row in report["rows"]
        if row["rule_name"] == "cluster_earnings_dividend_value_guard"
        and row["exit_arm"] == "fixed_20d"
    )
    assert cluster_fixed20["trade_count"] == 1
    assert "validation" not in output_csv.read_text(encoding="utf-8")


def test_raw_observation_split_purges_train_boundary_overlap(tmp_path: Path) -> None:
    observations = [_observation(idx) for idx in range(120)]
    observations_path = tmp_path / "observations.jsonl"
    _write_jsonl(observations_path, observations)
    manifest = scan_event_rule_only_train.split_manifest_from_raw(observations_path)

    assert (
        scan_event_rule_only_train.raw_observation_split(
            observations[72].model_dump(mode="json"),
            manifest,
        )
        == "purge_train_validation"
    )
