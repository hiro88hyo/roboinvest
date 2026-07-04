from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import (
    EventAiJob,
    EventAiLabel,
    EventAiLabeledRecord,
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "report-event-ai-train-labels.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("report_event_ai_train_labels", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report_event_ai_train_labels = _load_module()


def _observation(
    idx: int,
    *,
    forward_return_2d: float = 0.01,
    forward_return_5d: float = 0.02,
    forward_return_10d: float = 0.03,
    forward_return_20d: float = 0.04,
) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        trade_group_id=f"trade-{idx}",
        symbol="7203",
        event_type=EventType.EARNINGS_RESULT,
        signal_date=at.date().isoformat(),
        entry_date=(at.date() + timedelta(days=1)).isoformat(),
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value="0.10", valid=True),
            operating_profit_revision_pct=FeatureValue(value="0.05", valid=True),
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value="300000000", valid=True),
            atr_pct_14d=FeatureValue(value="0.03", valid=True),
            return_20d=FeatureValue(value="0.05", valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": forward_return_2d,
            "forward_return_5d": forward_return_5d,
            "forward_return_10d": forward_return_10d,
            "forward_return_20d": forward_return_20d,
            "catastrophic_stop_return_10d": forward_return_10d,
            "catastrophic_stop_return_20d": forward_return_20d,
        },
    )


def _label(idx: int, *, confidence: float = 0.8) -> EventAiLabeledRecord:
    label = EventAiLabel(
        event_type=EventType.EARNINGS_RESULT,
        fundamental_direction="positive",
        fundamental_strength=2,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon="5d",
        risk_flags=["fixture_flag"] if idx == 0 else [],
        confidence=confidence,
        rationale="fixture",
    )
    return EventAiLabeledRecord(
        job_id=f"job-{idx}",
        event_id=f"event-{idx}",
        prompt_version="event_ai_v0",
        prompt_hash=f"hash-{idx}",
        feature_schema_version="event_research_v0",
        feature_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        dataset_hash="dataset-hash",
        split_manifest_hash="split-manifest-hash",
        split_label="train",
        model_provider="fixture",
        model_id="fixture-model",
        temperature=Decimal("0"),
        seed=1,
        raw_response=label.model_dump_json(),
        label=label,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _job(idx: int) -> EventAiJob:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    return EventAiJob(
        job_id=f"job-{idx}",
        event_id=f"event-{idx}",
        prompt_version="event_ai_v0",
        prompt_hash=f"hash-{idx}",
        prompt=f"prompt {idx}",
        feature_schema_version="event_research_v0",
        feature_cutoff_at=at,
        dataset_hash="dataset-hash",
        split_manifest_hash="split-manifest-hash",
        split_label="train",
        model_provider="fixture",
        model_id="fixture-model",
        temperature=Decimal("0"),
        seed=1,
        created_at=at,
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(
            row.model_dump_json() if hasattr(row, "model_dump_json") else json.dumps(row)
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def test_train_report_is_train_only_and_reports_ai_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    jobs_path = tmp_path / "jobs.jsonl"
    failures_path = tmp_path / "failures.jsonl"
    output_json = tmp_path / "train-report.json"
    output_csv = tmp_path / "train-report.csv"
    _write_jsonl(observations_path, [_observation(idx) for idx in range(80)])
    _write_jsonl(labels_path, [_label(0), _label(1, confidence=0.4)])
    _write_jsonl(jobs_path, [_job(idx) for idx in range(4)])
    _write_jsonl(
        failures_path,
        [{"job_id": "job-2", "error": "EventAiParseError: invalid json"}],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-ai-train-labels.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--jobs",
            str(jobs_path),
            "--failures",
            str(failures_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    assert report_event_ai_train_labels.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["split"] == "train"
    assert report["summary"]["partial_train_report"] is True
    assert report["job_progress"]["total_train_jobs"] == 4
    assert report["job_progress"]["completed_train_jobs"] == 2
    assert report["job_progress"]["parse_failure_records"] == 1
    assert report["label_distribution"]["risk_flags"]["fixture_flag"] == 1
    assert report["confidence_buckets"] == {
        "0.0..0.5": 1,
        "0.5..0.7": 0,
        "0.7..1.0": 1,
    }
    assert report["ai_selection"]["ai_pass"] == 1
    assert report["ai_selection"]["ai_reject"] == 1
    assert report["train_minimum_effect_gate"]["status"] == "INSUFFICIENT_LABELS"
    assert report["train_minimum_effect_gate"]["reason"] == "train_labels_not_100pct_complete"

    fixed_2d_rule_ai = next(
        row
        for row in report["rows"]
        if row["entry_arm"] == "event_plus_ai_plus_fundamental_plus_technical"
        and row["exit_arm"] == "fixed_2d"
    )
    assert fixed_2d_rule_ai["trade_count"] == 1
    assert "labels_shuffled_within_event_type" in report["placebos"]
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "fixed_20d" not in csv_text
    assert "train_minimum_effect_gate" in csv_text


def test_train_minimum_effect_gate_passes_on_complete_train_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    jobs_path = tmp_path / "jobs.jsonl"
    output_json = tmp_path / "train-report.json"
    output_csv = tmp_path / "train-report.csv"
    _write_jsonl(
        observations_path,
        [
            _observation(0, forward_return_2d=0.03, forward_return_5d=0.03),
            _observation(1, forward_return_2d=-0.005, forward_return_5d=-0.005),
            _observation(2, forward_return_2d=-0.02, forward_return_5d=-0.02),
            _observation(3, forward_return_2d=-0.03, forward_return_5d=-0.03),
        ],
    )
    _write_jsonl(
        labels_path,
        [
            _label(0, confidence=0.8),
            _label(1, confidence=0.8),
            _label(2, confidence=0.4),
            _label(3, confidence=0.4),
        ],
    )
    _write_jsonl(jobs_path, [_job(idx) for idx in range(4)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-ai-train-labels.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--jobs",
            str(jobs_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    assert report_event_ai_train_labels.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    gate = report["train_minimum_effect_gate"]
    assert gate["status"] == "PASS"
    assert gate["candidate_exit"] == "fixed_2d"
    fixed_2d = next(row for row in gate["exit_checks"] if row["exit_arm"] == "fixed_2d")
    assert fixed_2d["pf_improvement_pass"] is True
    assert fixed_2d["net_pnl_not_below_rule_pass"] is True
    assert fixed_2d["ai_rejected_pf_below_1_pass"] is True
    assert fixed_2d["ai_rejected_rule_pass"]["profit_factor"] == 0.0
    assert "PASS" in output_csv.read_text(encoding="utf-8")


def test_train_report_rejects_non_train_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_json = tmp_path / "train-report.json"
    output_csv = tmp_path / "train-report.csv"
    _write_jsonl(observations_path, [_observation(idx) for idx in range(5)])
    _write_jsonl(labels_path, [_label(0)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-ai-train-labels.py",
            "--observations",
            str(observations_path),
            "--labels",
            str(labels_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "validation",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        report_event_ai_train_labels.main()

    assert exc.value.code == 2
