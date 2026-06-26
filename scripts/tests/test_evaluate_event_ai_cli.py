from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import (
    EventAiLabel,
    EventAiLabeledRecord,
    EventType,
    ObservationRecord,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "evaluate-event-ai.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("evaluate_event_ai", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluate_event_ai = _load_module()


def _observation(idx: int) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        symbol="7203",
        event_type=EventType.FORECAST_REVISION,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        labels={
            "forward_return_2d": 0.01,
            "forward_return_5d": 0.02,
            "forward_return_10d": 0.03,
            "forward_return_20d": 0.04,
            "catastrophic_stop_return_10d": 0.03,
            "catastrophic_stop_return_20d": 0.04,
        },
    )


def _label(idx: int) -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.FORECAST_REVISION,
        fundamental_direction="positive",
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


def test_event_ai_evaluator_defaults_to_development_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = [_observation(idx) for idx in range(40)]
    labels = [_label(idx) for idx in range(40)]
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_dir = tmp_path / "out"
    _write_jsonl(observations_path, observations)
    _write_jsonl(labels_path, labels)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate-event-ai.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert evaluate_event_ai.main() == 0

    report = json.loads((output_dir / "event-ai-report.json").read_text(encoding="utf-8"))
    assert report["evaluation_split"]["requested_split"] == "development"
    assert report["evaluation_split"]["split_counts"]["locked_oos"] > 0
    assert report["evaluation_split"]["selected_observation_count"] < len(observations)


def test_event_ai_evaluator_requires_locked_oos_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    _write_jsonl(observations_path, [_observation(idx) for idx in range(40)])
    _write_jsonl(labels_path, [_label(idx) for idx in range(40)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate-event-ai.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--split",
            "locked-oos",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        evaluate_event_ai.main()

    assert exc.value.code == 2
