from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from strategy_ai.event.cache import event_ai_cache_key
from strategy_ai.event.jobs import build_event_ai_job
from strategy_ai.event.parser import EventAiParseError, parse_event_ai_label
from strategy_ai.event.prompt import build_event_prompt, prompt_hash
from trade_contracts.event_research import (
    EntryArm,
    EventAiLabel,
    EventRecord,
    EventSource,
    EventType,
    ObservationRecord,
)


def _event() -> EventRecord:
    at = datetime(2026, 1, 20, 15, 30, tzinfo=UTC)
    return EventRecord(
        event_id="event-1",
        event_cluster_id="cluster-1",
        symbol="7203",
        source=EventSource.FIXTURE,
        raw_document_type="ForecastRevision",
        event_type=EventType.FORECAST_REVISION,
        disclosed_date="2026-01-20",
        disclosed_time="15:30:00",
        disclosed_at=at,
        data_available_at=at,
        signal_date="2026-01-20",
        entry_date="2026-01-21",
        feature_cutoff_at=at,
        raw_source_identifier="fixture-1",
        fetched_at=at,
        raw={"ForecastEarningsPerShare": "125"},
    )


def _observation() -> ObservationRecord:
    at = datetime(2026, 1, 20, 15, 30, tzinfo=UTC)
    return ObservationRecord(
        observation_id="obs-1",
        event_id="event-1",
        event_cluster_id="cluster-1",
        symbol="7203",
        event_type=EventType.FORECAST_REVISION,
        signal_date="2026-01-20",
        entry_date="2026-01-21",
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1100"),
        valuation_price=Decimal("1060"),
        source_record_id="fixture-1",
        labels={
            "forward_return_10d": 0.1,
            "exit_price_10d": "1210",
            "trade_result": "win",
            "pnl": "10000",
        },
    )


def _label(confidence: float = 0.7) -> EventAiLabel:
    return EventAiLabel(
        event_type=EventType.FORECAST_REVISION,
        fundamental_direction="positive",
        fundamental_strength=2,
        revision_quality="medium",
        valuation_context="fair",
        technical_context="neutral",
        expected_horizon="10d",
        risk_flags=[],
        confidence=confidence,
        rationale="fixture",
    )


def test_prompt_is_deterministic_and_excludes_forward_results() -> None:
    first = build_event_prompt(_event(), _observation())
    second = build_event_prompt(_event(), _observation())

    assert first == second
    assert prompt_hash(first) == prompt_hash(second)
    for forbidden in ("forward_return", "exit_price", "trade_result", "pnl"):
        assert forbidden not in first.lower()


def test_event_ai_job_records_prompt_metadata() -> None:
    job = build_event_ai_job(
        event=_event(),
        observation=_observation(),
        model_provider="fixture",
        model_id="fixture-model",
    )

    assert job.prompt_version == "event_ai_label_v0"
    assert job.feature_schema_version == "event_research_v0"
    assert job.prompt_hash == prompt_hash(job.prompt)


def test_parser_accepts_valid_json_and_rejects_bad_values() -> None:
    raw = _label().model_dump_json()

    assert parse_event_ai_label(raw).confidence == 0.7
    with pytest.raises(EventAiParseError):
        parse_event_ai_label("{bad json")
    with pytest.raises(EventAiParseError):
        parse_event_ai_label(
            json.dumps({**_label().model_dump(mode="json"), "revision_quality": "great"})
        )
    with pytest.raises(EventAiParseError):
        parse_event_ai_label(
            json.dumps({**_label().model_dump(mode="json"), "confidence": float("nan")})
        )


def test_cache_key_includes_model_and_prompt_settings() -> None:
    base = event_ai_cache_key(
        prompt_hash="abc",
        model_provider="fixture",
        model_id="m1",
        temperature=Decimal("0"),
        seed=1,
    )
    changed_model = event_ai_cache_key(
        prompt_hash="abc",
        model_provider="fixture",
        model_id="m2",
        temperature=Decimal("0"),
        seed=1,
    )
    changed_temp = event_ai_cache_key(
        prompt_hash="abc",
        model_provider="fixture",
        model_id="m1",
        temperature=Decimal("0.1"),
        seed=1,
    )

    assert base != changed_model
    assert base != changed_temp


def test_ai_arm_does_not_emit_direct_strategy_signal() -> None:
    from strategy_ai.event.evaluator import ai_arm_allows

    assert ai_arm_allows(_observation(), _label(), EntryArm.EVENT_PLUS_AI)
    assert not ai_arm_allows(_observation(), _label(confidence=0.4), EntryArm.EVENT_PLUS_AI)
