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
    ObservationRecord,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "compare-event-ai-placebo.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("compare_event_ai_placebo", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compare_event_ai_placebo = _load_module()


def _observation(idx: int, *, forward_20d: float) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol=f"72{idx:02d}",
        event_type=EventType.EARNINGS_RESULT,
        event_subtype="FYFinancialStatements_Consolidated_JP",
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        labels={
            "forward_return_2d": forward_20d / 10,
            "forward_return_5d": forward_20d / 4,
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
    confidence: float = 0.9,
) -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.EARNINGS_RESULT,
        fundamental_direction=direction,
        fundamental_strength=strength,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon="10d",
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


def test_compare_event_ai_placebo_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    real_labels_path = tmp_path / "real.jsonl"
    placebo_labels_path = tmp_path / "placebo.jsonl"
    output_json = tmp_path / "compare.json"
    output_csv = tmp_path / "compare.csv"
    _write_jsonl(
        observations_path,
        [
            _observation(0, forward_20d=0.10),
            _observation(1, forward_20d=-0.03),
            _observation(2, forward_20d=0.05),
            _observation(3, forward_20d=0.20),
        ],
    )
    _write_jsonl(
        real_labels_path,
        [
            _label(0, direction="positive", strength=2),
            _label(1, direction="positive", strength=2),
            _label(2, direction="negative", strength=1),
            _label(3, direction="positive", strength=2),
        ],
    )
    _write_jsonl(
        placebo_labels_path,
        [
            _label(0, direction="positive", strength=2),
            _label(1, direction="negative", strength=1),
            _label(2, direction="positive", strength=2),
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare-event-ai-placebo.py",
            "--observations",
            str(observations_path),
            "--real-labels",
            str(real_labels_path),
            "--placebo-labels",
            str(placebo_labels_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--top-n",
            "2",
        ],
    )

    assert compare_event_ai_placebo.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["real_label_count"] == 4
    assert report["summary"]["placebo_label_count"] == 3
    assert report["summary"]["common_label_count"] == 3
    assert report["summary"]["real_pass_count"] == 2
    assert report["summary"]["placebo_pass_count"] == 2
    assert report["summary"]["both_pass_count"] == 1
    assert report["summary"]["real_only_pass_count"] == 1
    assert report["summary"]["placebo_only_pass_count"] == 1
    assert report["summary"]["missing_from_placebo"] == 1
    assert report["cohort_profiles"]["both_pass"]["event_count"] == 1
    assert report["cohort_random_baselines"]["seed_count"] == 300
    assert report["cohort_random_baselines"]["uses_true_random_date_pool"] is False
    assert (
        report["cohort_random_baselines"]["cohorts"]["both_pass"]["fixed_20d"][
            "same_symbol_random_event_date"
        ]["random_count"]
        == 300
    )
    assert report["distribution_warnings"][0]["name"] == "confidence_distribution_collapsed"
    real_only_positive = report["top_contributors"]["real_only_pass"]["top_positive_fixed20"]
    assert real_only_positive[0]["event_id"] == "event-1"
    assert output_csv.read_text(encoding="utf-8").startswith("cohort,exit_arm")
