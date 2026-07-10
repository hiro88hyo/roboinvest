import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field, model_validator

from .enums import Action, RoutingIntent, SignalSource, TradingStyle

_STRATEGY_SIGNAL_ID_NAMESPACE = uuid5(NAMESPACE_URL, "roboinvest/StrategySignal/v1")
_UNIFIED_SIGNAL_ID_NAMESPACE = uuid5(NAMESPACE_URL, "roboinvest/UnifiedTradeSignal/v1")


def deterministic_strategy_signal_id(
    *,
    strategy_key: str,
    candidate_id: str,
    source: SignalSource | str,
    symbol: str,
    action: Action | str,
) -> UUID:
    """Return the stable id for one strategy/candidate/source decision."""

    identity = json.dumps(
        [strategy_key, candidate_id, str(source), symbol, str(action)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return uuid5(_STRATEGY_SIGNAL_ID_NAMESPACE, identity)


def deterministic_unified_signal_id(
    *,
    symbol: str,
    action: Action | str,
    strategy_signal_id_a: UUID | str | None,
    strategy_signal_id_b: UUID | str | None,
) -> UUID:
    """Return a stable aggregate id for the contributing strategy signals."""

    identity = json.dumps(
        [
            symbol,
            str(action),
            None if strategy_signal_id_a is None else str(strategy_signal_id_a),
            None if strategy_signal_id_b is None else str(strategy_signal_id_b),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return uuid5(_UNIFIED_SIGNAL_ID_NAMESPACE, identity)


EXECUTION_FIELD_NAMES = (
    "best_bid",
    "best_ask",
    "spread_bps",
    "tick_size",
    "spread_ticks",
    "bid_depth_1",
    "ask_depth_1",
    "bid_depth_5",
    "ask_depth_5",
    "book_imbalance_5",
    "minutes_from_open",
    "minutes_to_close",
    "session_phase",
)

ORDER_FIELD_NAMES = (
    "stop_loss_price",
    "stop_loss_pct",
    "target_price",
    "trailing_stop_pct",
    "max_hold_days",
    "scheduled_exit_date",
)


def execution_fields_from(payload: Any) -> dict[str, Any]:
    """Copy optional execution context fields between feature/signal models."""
    return {name: getattr(payload, name, None) for name in EXECUTION_FIELD_NAMES}


def order_fields_from(payload: Any) -> dict[str, Any]:
    """Copy optional order management fields between signal models."""
    return {name: getattr(payload, name, None) for name in ORDER_FIELD_NAMES}


class StrategySignal(BaseModel):
    """Strategy A / B が個別に出力するシグナル。"""

    signal_id: UUID = Field(default_factory=uuid4)
    source: SignalSource
    routing_intent: RoutingIntent = RoutingIntent.SYSTEM
    strategy_key: str | None = Field(default=None, min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    symbol: str
    price: Decimal | None = None
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None
    holding_type: TradingStyle | None = None
    stop_loss_price: Decimal | None = None
    stop_loss_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread_bps: Decimal | None = None
    tick_size: Decimal | None = None
    spread_ticks: Decimal | None = None
    bid_depth_1: int | None = None
    ask_depth_1: int | None = None
    bid_depth_5: int | None = None
    ask_depth_5: int | None = None
    book_imbalance_5: Decimal | None = None
    minutes_from_open: int | None = None
    minutes_to_close: int | None = None
    session_phase: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_identity_signal_id(cls, payload: Any) -> Any:
        if not isinstance(payload, dict) or "signal_id" in payload:
            return payload
        strategy_key = payload.get("strategy_key")
        candidate_id = payload.get("candidate_id")
        source = payload.get("source")
        symbol = payload.get("symbol")
        action = payload.get("action")
        if not all((strategy_key, candidate_id, source, symbol, action)):
            return payload
        enriched = dict(payload)
        enriched["signal_id"] = deterministic_strategy_signal_id(
            strategy_key=str(strategy_key),
            candidate_id=str(candidate_id),
            source=str(source),
            symbol=str(symbol),
            action=str(action),
        )
        return enriched

    @model_validator(mode="after")
    def validate_stop_loss_intent(self) -> Self:
        if (self.strategy_key is None) != (self.candidate_id is None):
            raise ValueError("strategy_key and candidate_id must be provided together")
        if self.strategy_key is not None and (
            not self.strategy_key.strip() or not self.candidate_id or not self.candidate_id.strip()
        ):
            raise ValueError("strategy_key and candidate_id must not be blank")
        if self.stop_loss_price is not None and self.stop_loss_pct is not None:
            raise ValueError("stop_loss_price and stop_loss_pct are mutually exclusive")
        if self.stop_loss_pct is not None and self.action is not Action.BUY:
            raise ValueError("stop_loss_pct is only valid for BUY signals")
        return self


class UnifiedTradeSignal(BaseModel):
    """Aggregator が合議した最終シグナル。Gateway の入力となる。"""

    signal_id: UUID = Field(default_factory=uuid4)
    routing_intent: RoutingIntent = RoutingIntent.SYSTEM
    strategy_key: str | None = Field(default=None, min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    symbol: str
    price: Decimal | None = None
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    signal_source: SignalSource

    strategy_signal_id_a: UUID | None = None
    strategy_signal_id_b: UUID | None = None

    holding_type: TradingStyle
    stop_loss_price: Decimal | None = None
    stop_loss_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread_bps: Decimal | None = None
    tick_size: Decimal | None = None
    spread_ticks: Decimal | None = None
    bid_depth_1: int | None = None
    ask_depth_1: int | None = None
    bid_depth_5: int | None = None
    ask_depth_5: int | None = None
    book_imbalance_5: Decimal | None = None
    minutes_from_open: int | None = None
    minutes_to_close: int | None = None
    session_phase: str | None = None

    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_aggregate_signal_id(cls, payload: Any) -> Any:
        if not isinstance(payload, dict) or "signal_id" in payload:
            return payload
        strategy_signal_id_a = payload.get("strategy_signal_id_a")
        strategy_signal_id_b = payload.get("strategy_signal_id_b")
        symbol = payload.get("symbol")
        action = payload.get("action")
        if (
            symbol is None
            or action is None
            or (strategy_signal_id_a is None and strategy_signal_id_b is None)
        ):
            return payload
        enriched = dict(payload)
        enriched["signal_id"] = deterministic_unified_signal_id(
            symbol=str(symbol),
            action=str(action),
            strategy_signal_id_a=strategy_signal_id_a,
            strategy_signal_id_b=strategy_signal_id_b,
        )
        return enriched

    @model_validator(mode="after")
    def validate_stop_loss_intent(self) -> Self:
        if (self.strategy_key is None) != (self.candidate_id is None):
            raise ValueError("strategy_key and candidate_id must be provided together")
        if self.strategy_key is not None and (
            not self.strategy_key.strip() or not self.candidate_id or not self.candidate_id.strip()
        ):
            raise ValueError("strategy_key and candidate_id must not be blank")
        if self.stop_loss_price is not None and self.stop_loss_pct is not None:
            raise ValueError("stop_loss_price and stop_loss_pct are mutually exclusive")
        if self.stop_loss_pct is not None and self.action is not Action.BUY:
            raise ValueError("stop_loss_pct is only valid for BUY signals")
        return self
