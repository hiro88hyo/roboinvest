"""Publisher configuration, durable claim, and receipt models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trade_contracts.enums import Action, RoutingIntent, SignalSource, TradingStyle

from .artifact import EVENT_MAX_HOLD_DAYS, EVENT_STOP_LOSS_PCT, EVENT_STRATEGY_KEY

EVENT_BOOK_SUBSCRIPTION = "event-paper-raw-books"
EVENT_SIGNAL_TOPIC = "strategy-signals-a"
EVENT_PUBLISH_ENABLED_ENV = "EVENT_CLUSTER_PAPER_PUBLISH_ENABLED"
EVENT_SIGNAL_CONFIDENCE = 0.5
EVENT_MAX_BOOK_AGE_SECONDS = 10.0
EVENT_MAX_FUTURE_SKEW_SECONDS = 5.0
EVENT_ENTRY_WINDOW_START = time(9, 0)
EVENT_ENTRY_WINDOW_END = time(9, 30)
EVENT_EXIT_TIME = time(15, 30)
EVENT_EXECUTION_PROFILE = "opening_transport_stress_v1"
EVENT_EXECUTION_STRATEGY_KEY = f"{EVENT_STRATEGY_KEY}__{EVENT_EXECUTION_PROFILE}"


@dataclass(frozen=True, slots=True)
class EventPaperPublishConfig:
    subscription: str = EVENT_BOOK_SUBSCRIPTION
    signal_topic: str = EVENT_SIGNAL_TOPIC
    confidence: float = EVENT_SIGNAL_CONFIDENCE
    max_book_age_seconds: float = EVENT_MAX_BOOK_AGE_SECONDS
    max_future_skew_seconds: float = EVENT_MAX_FUTURE_SKEW_SECONDS
    pull_max_messages: int = 100
    max_pull_batches: int = 300
    idle_backoff_seconds: float = 0.2
    entry_window_start: time = EVENT_ENTRY_WINDOW_START
    entry_window_end: time = EVENT_ENTRY_WINDOW_END
    seek_before_pull: bool = True
    allow_test_resource_overrides: bool = False

    def __post_init__(self) -> None:
        if not self.allow_test_resource_overrides and self.subscription != EVENT_BOOK_SUBSCRIPTION:
            raise ValueError("event publisher subscription is fixed")
        if not self.allow_test_resource_overrides and self.signal_topic != EVENT_SIGNAL_TOPIC:
            raise ValueError("event publisher signal topic is fixed")
        if self.confidence != EVENT_SIGNAL_CONFIDENCE:
            raise ValueError("event signal confidence is frozen at 0.5")
        if self.max_book_age_seconds != EVENT_MAX_BOOK_AGE_SECONDS:
            raise ValueError("event book freshness limit is frozen at 10 seconds")
        if self.max_future_skew_seconds != EVENT_MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("event book future-skew limit is frozen at 5 seconds")
        if self.pull_max_messages <= 0 or self.max_pull_batches <= 0:
            raise ValueError("pull limits must be positive")
        if self.idle_backoff_seconds < 0:
            raise ValueError("idle_backoff_seconds must be non-negative")
        if (
            self.entry_window_start != EVENT_ENTRY_WINDOW_START
            or self.entry_window_end != EVENT_ENTRY_WINDOW_END
        ):
            raise ValueError("event entry window is frozen at 09:00-09:30 JST")


class EventPaperSignalFields(BaseModel):
    """Exact StrategySignal fields frozen by the first selected book."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal[SignalSource.RULE] = SignalSource.RULE
    routing_intent: Literal[RoutingIntent.PAPER_ONLY] = RoutingIntent.PAPER_ONLY
    strategy_key: str = EVENT_EXECUTION_STRATEGY_KEY
    candidate_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    action: Literal[Action.BUY] = Action.BUY
    confidence: float = EVENT_SIGNAL_CONFIDENCE
    holding_type: Literal[TradingStyle.SWING] = TradingStyle.SWING
    stop_loss_pct: Decimal = EVENT_STOP_LOSS_PCT
    max_hold_days: int = EVENT_MAX_HOLD_DAYS
    scheduled_exit_date: date | None = None
    scheduled_exit_time: time = EVENT_EXIT_TIME
    best_bid: Decimal = Field(gt=0)
    best_ask: Decimal = Field(gt=0)
    spread_bps: Decimal = Field(ge=0)
    tick_size: Decimal = Field(gt=0)
    spread_ticks: Decimal = Field(ge=0)
    bid_depth_1: int = Field(gt=0)
    ask_depth_1: int = Field(gt=0)
    bid_depth_5: int = Field(gt=0)
    ask_depth_5: int = Field(gt=0)
    book_imbalance_5: Decimal = Field(ge=-1, le=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_frozen_fields(self) -> Self:
        if self.confidence != EVENT_SIGNAL_CONFIDENCE:
            raise ValueError("event confidence drifted from 0.5")
        if self.strategy_key != EVENT_EXECUTION_STRATEGY_KEY:
            raise ValueError("event execution strategy key drifted")
        if self.stop_loss_pct != EVENT_STOP_LOSS_PCT:
            raise ValueError("event stop loss drifted from 0.10")
        if self.max_hold_days != EVENT_MAX_HOLD_DAYS:
            raise ValueError("event max hold drifted from 20")
        if self.scheduled_exit_date is not None:
            raise ValueError("event scheduled exit must be derived by OMS Paper from the fill")
        if self.scheduled_exit_time != EVENT_EXIT_TIME:
            raise ValueError("event scheduled exit time is frozen at 15:30 JST")
        if self.price != self.best_ask:
            raise ValueError("event signal price must equal the selected best ask")
        if self.best_bid >= self.best_ask:
            raise ValueError("event book must have a positive spread")
        if self.created_at.tzinfo is None:
            raise ValueError("event signal created_at must be timezone-aware")
        return self


class EventPaperPublicationCheckpoint(BaseModel):
    """Durable Pub/Sub success metadata stored with the selected quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(min_length=1)
    topic: Literal["strategy-signals-a"] = "strategy-signals-a"
    strategy_message_id: str = Field(min_length=1)
    published_at: datetime

    @model_validator(mode="after")
    def validate_published_at(self) -> Self:
        if self.published_at.tzinfo is None:
            raise ValueError("event publication timestamp must be timezone-aware")
        return self


class EventPaperPublicationAttempt(BaseModel):
    """Durable marker written before raw ack and external publish."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(min_length=1)
    attempted_at: datetime

    @model_validator(mode="after")
    def validate_attempted_at(self) -> Self:
        if self.attempted_at.tzinfo is None:
            raise ValueError("event publication attempt timestamp must be timezone-aware")
        return self


class EventPaperSignalClaim(BaseModel):
    """Versioned content stored in strategy_logs.reasoning before publish."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["event_paper_signal_claim"] = "event_paper_signal_claim"
    selection_strategy_key: Literal[
        "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
    ] = "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
    execution_profile: Literal["opening_transport_stress_v1"] = "opening_transport_stress_v1"
    comparable_to_registered_backtest: Literal[False] = False
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_book_message_id: str = Field(min_length=1)
    raw_book_received_at: datetime
    cluster_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    signal_date: date
    entry_date: date
    signal_fields: EventPaperSignalFields
    publication_attempt: EventPaperPublicationAttempt | None = None
    publication: EventPaperPublicationCheckpoint | None = None

    @model_validator(mode="after")
    def validate_claim_identity(self) -> Self:
        if self.raw_book_received_at.tzinfo is None:
            raise ValueError("claim book timestamp must be timezone-aware")
        if self.signal_fields.created_at != self.raw_book_received_at:
            raise ValueError("signal timestamp must equal the selected book receipt")
        expected = f"{self.cluster_id}:{self.observation_id}"
        if self.signal_fields.candidate_id != expected:
            raise ValueError("claim occurrence identity mismatch")
        if self.publication_attempt is not None:
            attempted_local = self.publication_attempt.attempted_at.astimezone(
                ZoneInfo("Asia/Tokyo")
            )
            attempted_time = attempted_local.time().replace(tzinfo=None)
            if attempted_local.date() != self.entry_date or not (
                time(9, 0) <= attempted_time < time(9, 30)
            ):
                raise ValueError("event publication attempt is outside the entry window")
            attempt_age = (
                self.publication_attempt.attempted_at - self.raw_book_received_at
            ).total_seconds()
            if (
                attempt_age < -EVENT_MAX_FUTURE_SKEW_SECONDS
                or attempt_age > EVENT_MAX_BOOK_AGE_SECONDS
            ):
                raise ValueError("event publication attempt used an invalid selected book")
        if self.publication is not None:
            if self.publication_attempt is None:
                raise ValueError("event publication has no durable attempt")
            if self.publication.attempt_id != self.publication_attempt.attempt_id:
                raise ValueError("event publication attempt identity mismatch")
            if self.publication.published_at < self.publication_attempt.attempted_at:
                raise ValueError("event publication predates its durable attempt")
            published_local = self.publication.published_at.astimezone(ZoneInfo("Asia/Tokyo"))
            published_time = published_local.time().replace(tzinfo=None)
            if published_local.date() != self.entry_date or not (
                time(9, 0) <= published_time < time(9, 30)
            ):
                raise ValueError("event publication is outside the target entry window")
            age_seconds = (
                self.publication.published_at - self.raw_book_received_at
            ).total_seconds()
            if (
                age_seconds < -EVENT_MAX_FUTURE_SKEW_SECONDS
                or age_seconds > EVENT_MAX_BOOK_AGE_SECONDS
            ):
                raise ValueError("event publication used a stale selected book")
        return self


class EventPaperPublishedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_key: str
    execution_candidate_id: str
    symbol: str
    signal_id: str
    raw_book_message_id: str
    observed_ask: Decimal
    book_received_at: datetime
    publication_status: Literal["confirmed", "ambiguous"] = "confirmed"
    publication_attempt_id: str = Field(min_length=1)
    attempted_at: datetime
    strategy_message_id: str | None
    topic: str
    published_at: datetime | None
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_publication_state(self) -> Self:
        if self.strategy_key != EVENT_EXECUTION_STRATEGY_KEY:
            raise ValueError("publication record has the wrong execution strategy key")
        if self.attempted_at.tzinfo is None:
            raise ValueError("publication attempt timestamp must be timezone-aware")
        if self.publication_status == "confirmed":
            if not self.strategy_message_id or self.published_at is None:
                raise ValueError("confirmed publication is missing Pub/Sub metadata")
            if self.published_at.tzinfo is None:
                raise ValueError("publication timestamp must be timezone-aware")
        elif self.strategy_message_id is not None or self.published_at is not None:
            raise ValueError("ambiguous publication cannot claim Pub/Sub success metadata")
        return self


class EventPaperPublishReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["paper_publish"] = "paper_publish"
    execution_profile: Literal["opening_transport_stress_v1"] = "opening_transport_stress_v1"
    comparable_to_registered_backtest: Literal[False] = False
    target_date: date
    artifact_path: str
    artifact_sha256: str
    selected_execution_candidate_ids: list[str] = Field(min_length=1)
    published: list[EventPaperPublishedRecord]
    skipped_messages: dict[str, int]


@dataclass(frozen=True, slots=True)
class EventPaperPreflightState:
    trade_mode: str
    is_trading_allowed: bool
    due_symbols: tuple[str, ...] = ()


def claim_json(claim: EventPaperSignalClaim) -> str:
    return claim.model_dump_json()


def parse_claim_json(value: str | None) -> EventPaperSignalClaim:
    if not value:
        raise ValueError("strategy log does not contain an event-paper claim")
    return EventPaperSignalClaim.model_validate_json(value)


def receipt_dict(receipt: EventPaperPublishReceipt) -> dict[str, Any]:
    return receipt.model_dump(mode="json")
