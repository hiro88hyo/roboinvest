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
    ObservationRecord,
    TechnicalContextV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "diagnose-event-ai-selection.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("diagnose_event_ai_selection", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


diagnose_event_ai_selection = _load_module()


def _observation(
    idx: int,
    *,
    event_type: EventType = EventType.FORECAST_REVISION,
) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        event_type=event_type,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000),
            atr_pct_14d=FeatureValue(value=0.02),
            return_20d=FeatureValue(value=0.05),
            market_regime=FeatureValue(value="broad_uptrend"),
        ),
        labels={
            "forward_return_2d": 0.01,
            "forward_return_5d": 0.02,
            "forward_return_10d": 0.03,
            "forward_return_20d": 0.04,
            "catastrophic_stop_return_10d": 0.03,
            "catastrophic_stop_return_20d": 0.04,
        },
    )


def _label(idx: int, *, direction: str = "positive") -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.FORECAST_REVISION,
        fundamental_direction=direction,
        fundamental_strength=2,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon="10d",
        risk_flags=[],
        confidence=0.8,
        rationale="fixture",
    )
    return EventAiLabeledRecord(
        job_id=f"job-{idx}",
        event_id=f"event-{idx}",
        prompt_version="event_ai_label_v0",
        prompt_hash=f"hash-{idx}",
        feature_schema_version="event_research_v0",
        split_label="train",
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


def test_event_ai_selection_diagnostics_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_json = tmp_path / "diagnostics.json"
    output_csv = tmp_path / "diagnostics.csv"
    _write_jsonl(observations_path, [_observation(0), _observation(1)])
    _write_jsonl(labels_path, [_label(0), _label(1, direction="neutral")])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose-event-ai-selection.py",
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

    assert diagnose_event_ai_selection.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    ai_pass = next(
        row
        for row in report["rows"]
        if row["group"] == "ai_pass"
        and row["event_type"] == "all"
        and row["exit_arm"] == "fixed_10d"
    )
    technical_pass = next(
        row
        for row in report["rows"]
        if row["group"] == "technical_pass"
        and row["event_type"] == "all"
        and row["exit_arm"] == "fixed_10d"
    )
    assert report["summary"]["matched_observation_count"] == 2
    assert ai_pass["trade_count"] == 1
    assert technical_pass["trade_count"] == 2
    assert output_csv.read_text(encoding="utf-8").startswith("group,event_type,exit_arm")
