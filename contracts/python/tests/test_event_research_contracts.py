from __future__ import annotations

import pytest
from pydantic import ValidationError
from trade_contracts.event_research import EventAiLabel, EventType, ExecutionMode


def test_event_ai_label_accepts_strict_schema() -> None:
    label = EventAiLabel(
        event_type=EventType.FORECAST_REVISION,
        fundamental_direction="positive",
        fundamental_strength=3,
        revision_quality="high",
        valuation_context="cheap",
        technical_context="favorable",
        expected_horizon="10d",
        risk_flags=[],
        confidence=0.8,
        rationale="revision is material",
    )

    assert label.fundamental_strength == 3


def test_event_ai_label_rejects_unknown_enum_and_nan() -> None:
    with pytest.raises(ValidationError):
        EventAiLabel(
            event_type="forecast_revision",
            fundamental_direction="bullish",
            fundamental_strength=2,
            revision_quality="high",
            valuation_context="cheap",
            technical_context="favorable",
            expected_horizon="10d",
            risk_flags=[],
            confidence=0.8,
            rationale="invalid enum",
        )

    with pytest.raises(ValidationError):
        EventAiLabel(
            event_type="forecast_revision",
            fundamental_direction="positive",
            fundamental_strength=2,
            revision_quality="high",
            valuation_context="cheap",
            technical_context="favorable",
            expected_horizon="10d",
            risk_flags=[],
            confidence=float("nan"),
            rationale="invalid confidence",
        )


def test_execution_modes_are_separate_contract_values() -> None:
    assert ExecutionMode.NEXT_OPEN_UNCONDITIONAL.value == "next_open_unconditional"
    assert ExecutionMode.NEXT_0915_CONDITIONAL.value == "next_0915_conditional"
