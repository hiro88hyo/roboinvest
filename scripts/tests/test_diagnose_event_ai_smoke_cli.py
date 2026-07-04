from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_contracts.event_research import (
    EventAiLabel,
    EventAiLabeledRecord,
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "diagnose-event-ai-smoke.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("diagnose_event_ai_smoke", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


diagnose_event_ai_smoke = _load_module()


def _observation(idx: int, *, forward_20d: float) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        event_type=EventType.FORECAST_REVISION,
        event_subtype="EarnForecastRevision",
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=0.1, valid=True)
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000, valid=True),
            atr_pct_14d=FeatureValue(value=0.02, valid=True),
            return_20d=FeatureValue(value=0.05, valid=True),
            symbol_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": 0.01,
            "forward_return_5d": 0.01,
            "forward_return_10d": forward_20d / 2,
            "forward_return_20d": forward_20d,
            "catastrophic_stop_return_10d": forward_20d / 2,
            "catastrophic_stop_return_20d": forward_20d,
        },
    )


def _label(
    idx: int,
    *,
    direction: str,
    strength: int,
    horizon: str = "5d",
    confidence: float = 0.8,
) -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.FORECAST_REVISION,
        fundamental_direction=direction,
        fundamental_strength=strength,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon=horizon,
        risk_flags=[],
        confidence=confidence,
        rationale="fixture",
    )
    return EventAiLabeledRecord(
        job_id=f"job-{idx}",
        event_id=f"event-{idx}",
        prompt_hash=f"hash-{idx}",
        model_provider="fixture",
        model_id="fixture-model",
        raw_response=label.model_dump_json(),
        label=label,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )


def test_event_ai_smoke_diagnostics_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_json = tmp_path / "diagnostics.json"
    output_csv = tmp_path / "diagnostics.csv"
    _write_jsonl(
        observations_path,
        [
            _observation(0, forward_20d=0.02),
            _observation(1, forward_20d=0.12),
            _observation(2, forward_20d=-0.08),
        ],
    )
    _write_jsonl(
        labels_path,
        [
            _label(0, direction="positive", strength=2),
            _label(1, direction="negative", strength=1, horizon="avoid"),
            _label(2, direction="positive", strength=3),
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose-event-ai-smoke.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    assert diagnose_event_ai_smoke.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    positive_vs_negative = next(
        item for item in report["findings"] if item["name"] == "positive_vs_negative_label_fixed20"
    )
    ai_pass = next(
        row
        for row in report["rows"]
        if row["category"] == "entry_selection"
        and row["value"] == "ai_pass"
        and row["exit_arm"] == "fixed_20d"
    )
    feature_positive = next(
        row
        for row in report["rows"]
        if row["category"] == "feature_profit_revision_pct"
        and row["value"] == "positive"
        and row["exit_arm"] == "fixed_20d"
    )
    assert report["summary"]["matched_observation_count"] == 3
    assert positive_vs_negative["passed"] is False
    assert ai_pass["trade_count"] == 2
    assert feature_positive["trade_count"] == 3
    assert output_csv.read_text(encoding="utf-8").startswith("category,value,exit_arm")
