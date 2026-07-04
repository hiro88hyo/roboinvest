from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_ai.event.evaluator import ai_arm_allows
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabeledRecord,
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
    ValuationFeaturesV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "build-event-ai-feature-proxy-labels.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("build_event_ai_feature_proxy_labels", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_feature_proxy_labels = _load_module()


def _observation(
    idx: int,
    *,
    profit_revision_pct: str,
    split_label: str = "train",
) -> ObservationRecord:
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
        split_label=split_label,
        dataset_hash="dataset-hash",
        split_manifest_hash="split-hash",
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=profit_revision_pct, valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value="12.5", valid=True),
            forecast_per_valid=True,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value="300000000", valid=True),
            atr_pct_14d=FeatureValue(value="0.03", valid=True),
            return_20d=FeatureValue(value="0.05", valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
    )


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )


def test_build_event_ai_feature_proxy_labels_defaults_to_development(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    allowlist_labels_path = tmp_path / "allowlist-labels.jsonl"
    output_path = tmp_path / "feature-proxy-labels.jsonl"
    _write_jsonl(
        observations_path,
        [
            _observation(0, profit_revision_pct="0.15", split_label="train"),
            _observation(1, profit_revision_pct="-0.10", split_label="validation"),
            _observation(2, profit_revision_pct="0.25", split_label="locked_oos"),
        ],
    )
    _write_jsonl(
        allowlist_labels_path,
        [
            EventAiLabeledRecord(
                job_id="allow-0",
                event_id="event-0",
                prompt_hash="allow-hash-0",
                model_provider="fixture",
                model_id="fixture",
                raw_response="{}",
                label=build_feature_proxy_labels.feature_bundle_proxy_label(
                    _observation(0, profit_revision_pct="0.15")
                ),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            EventAiLabeledRecord(
                job_id="allow-1",
                event_id="event-1",
                prompt_hash="allow-hash-1",
                model_provider="fixture",
                model_id="fixture",
                raw_response="{}",
                label=build_feature_proxy_labels.feature_bundle_proxy_label(
                    _observation(1, profit_revision_pct="-0.10")
                ),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-event-ai-feature-proxy-labels.py",
            "--observations",
            str(observations_path),
            "--output",
            str(output_path),
            "--event-ids-from-labels",
            str(allowlist_labels_path),
        ],
    )

    assert build_feature_proxy_labels.main() == 0

    records = [
        EventAiLabeledRecord.model_validate(json.loads(line))
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record.event_id for record in records] == ["event-0", "event-1"]
    assert records[0].model_provider == "deterministic_feature_proxy"
    assert records[0].model_id == "feature_bundle_proxy_v0"
    assert records[0].dataset_hash == "dataset-hash"
    assert records[0].split_manifest_hash == "split-hash"
    assert records[0].label.fundamental_direction == "positive"
    assert records[0].label.fundamental_strength == 2
    assert records[1].label.fundamental_direction == "negative"
    assert records[1].label.expected_horizon == "avoid"

    observations = [
        _observation(0, profit_revision_pct="0.15"),
        _observation(1, profit_revision_pct="-0.10"),
    ]
    assert ai_arm_allows(observations[0], records[0].label, EntryArm.EVENT_PLUS_AI) is True
    assert ai_arm_allows(observations[1], records[1].label, EntryArm.EVENT_PLUS_AI) is False
