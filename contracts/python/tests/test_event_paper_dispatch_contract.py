from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from trade_contracts.event_paper_dispatch import EventPaperDispatchResult


def _payload() -> dict[str, str]:
    return {
        "routing_intent": "PAPER_ONLY",
        "strategy_key": (
            "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
            "__opening_transport_stress_v1"
        ),
    }


def _result(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "outcome": "prepared",
        "stage": "aggregator",
        "input_signal_id": str(uuid4()),
        "input_payload": _payload(),
        "input_payload_sha256": "a" * 64,
        "output_payload": _payload(),
        "output_payload_sha256": "b" * 64,
        "destination_topic": "trade-signals",
        "status": "prepared",
        "attempt_id": None,
        "attempted_at": None,
        "pubsub_message_id": None,
        "confirmed_at": None,
        "last_error": None,
    }
    row.update(overrides)
    return row


def test_confirmed_dispatch_requires_checkpoint_and_timezone_aware_timestamps() -> None:
    now = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    result = EventPaperDispatchResult.model_validate(
        _result(
            outcome="confirmed",
            status="confirmed",
            attempt_id="attempt-1",
            attempted_at=now,
            pubsub_message_id="message-1",
            confirmed_at=now,
        )
    )
    assert result.confirmed_at == now


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        (
            {
                "outcome": "confirmed",
                "status": "confirmed",
                "attempt_id": "attempt-1",
                "attempted_at": datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
            },
            "publication checkpoint",
        ),
        (
            {
                "outcome": "attempt_started",
                "status": "prepared",
            },
            "incompatible",
        ),
        (
            {
                "outcome": "ambiguous",
                "status": "ambiguous",
                "attempt_id": "attempt-1",
                "attempted_at": datetime(2026, 4, 20, 9, 0),
            },
            "timezone-aware",
        ),
    ],
)
def test_dispatch_contract_rejects_invalid_outcome_or_attempt_state(
    overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        EventPaperDispatchResult.model_validate(_result(**overrides))
