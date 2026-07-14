"""Shared identity and journal contracts for the event-paper transport path.

The opening transport stress workflow is deliberately isolated from normal
strategy traffic.  These helpers identify that one immutable route and define
the durable-dispatch states used by Aggregator and Gateway.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import RoutingIntent

EVENT_PAPER_EXECUTION_PROFILE = "opening_transport_stress_v1"
EVENT_PAPER_FROZEN_EXECUTION_PROFILE = "frozen_opening_close_v1"
EVENT_PAPER_RESEARCH_STRATEGY_KEY = (
    "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
)
EVENT_PAPER_EXECUTION_STRATEGY_KEY = (
    f"{EVENT_PAPER_RESEARCH_STRATEGY_KEY}__{EVENT_PAPER_EXECUTION_PROFILE}"
)
EVENT_PAPER_FROZEN_EXECUTION_STRATEGY_KEY = (
    f"{EVENT_PAPER_RESEARCH_STRATEGY_KEY}__{EVENT_PAPER_FROZEN_EXECUTION_PROFILE}"
)
EVENT_PAPER_EXECUTION_STRATEGY_KEYS = frozenset(
    {
        EVENT_PAPER_EXECUTION_STRATEGY_KEY,
        EVENT_PAPER_FROZEN_EXECUTION_STRATEGY_KEY,
    }
)


class EventPaperDispatchStage(StrEnum):
    """One of the two durable downstream delivery boundaries."""

    AGGREGATOR = "aggregator"
    GATEWAY = "gateway"


class EventPaperDispatchStatus(StrEnum):
    """Persisted stage state.

    ``ATTEMPTING`` is intentionally treated as ambiguous after a process or
    network failure: Pub/Sub acknowledgement and a database checkpoint cannot
    be committed atomically.
    """

    PREPARED = "prepared"
    ATTEMPTING = "attempting"
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"


class EventPaperDispatchOutcome(StrEnum):
    """Outcome returned by the stage-journal RPC."""

    PREPARED = "prepared"
    ATTEMPT_STARTED = "attempt_started"
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    PAYLOAD_MISMATCH = "payload_mismatch"
    ATTEMPT_MISMATCH = "attempt_mismatch"


class EventPaperDispatchResult(BaseModel):
    """Strictly parsed durable stage-journal RPC response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: EventPaperDispatchOutcome
    stage: EventPaperDispatchStage
    input_signal_id: UUID
    input_payload: dict[str, Any]
    input_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_payload: dict[str, Any]
    output_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_topic: str = Field(min_length=1)
    status: EventPaperDispatchStatus
    attempt_id: str | None = None
    attempted_at: datetime | None = None
    pubsub_message_id: str | None = None
    confirmed_at: datetime | None = None
    last_error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> EventPaperDispatchResult:
        if self.attempted_at is not None and self.attempted_at.tzinfo is None:
            raise ValueError("attempted_at must be timezone-aware")
        if self.confirmed_at is not None and self.confirmed_at.tzinfo is None:
            raise ValueError("confirmed_at must be timezone-aware")
        if self.status is EventPaperDispatchStatus.PREPARED:
            if self.attempt_id is not None or self.attempted_at is not None:
                raise ValueError("prepared dispatch must not have an attempt")
            if self.pubsub_message_id is not None or self.confirmed_at is not None:
                raise ValueError("prepared dispatch must not have a publication checkpoint")
        else:
            if self.attempt_id is None or self.attempted_at is None:
                raise ValueError("attempted dispatch must have an attempt id and timestamp")
        if self.status is EventPaperDispatchStatus.CONFIRMED:
            if self.pubsub_message_id is None or self.confirmed_at is None:
                raise ValueError("confirmed dispatch must have a publication checkpoint")
        elif self.pubsub_message_id is not None or self.confirmed_at is not None:
            raise ValueError("unconfirmed dispatch must not have a publication checkpoint")

        expected_statuses = {
            EventPaperDispatchOutcome.PREPARED: {EventPaperDispatchStatus.PREPARED},
            EventPaperDispatchOutcome.ATTEMPT_STARTED: {EventPaperDispatchStatus.ATTEMPTING},
            EventPaperDispatchOutcome.CONFIRMED: {EventPaperDispatchStatus.CONFIRMED},
            EventPaperDispatchOutcome.AMBIGUOUS: {
                EventPaperDispatchStatus.ATTEMPTING,
                EventPaperDispatchStatus.AMBIGUOUS,
            },
        }
        allowed = expected_statuses.get(self.outcome)
        if allowed is not None and self.status not in allowed:
            raise ValueError(
                f"outcome={self.outcome.value} is incompatible with status={self.status.value}"
            )
        return self


def is_event_paper_execution_signal(
    *, routing_intent: RoutingIntent, strategy_key: str | None
) -> bool:
    """Return whether a message belongs to the isolated event-paper workflow."""

    return (
        routing_intent is RoutingIntent.PAPER_ONLY
        and strategy_key in EVENT_PAPER_EXECUTION_STRATEGY_KEYS
    )


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash a JSON-native business payload with a stable representation."""

    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
