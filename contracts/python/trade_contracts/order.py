import json
from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field, model_validator

from .enums import (
    OrderStatus,
    OrderType,
    RoutingIntent,
    Side,
    SignalSource,
    TradeMode,
    TradingStyle,
)

_ORDER_ID_NAMESPACE = uuid5(NAMESPACE_URL, "roboinvest/OrderRequest/v1")


def deterministic_order_id(
    *,
    unified_signal_id: UUID | str,
    trade_mode: TradeMode | str,
    side: Side | str,
) -> UUID:
    """Return the stable OMS idempotency key for one routed signal."""

    identity = json.dumps(
        [str(unified_signal_id), str(trade_mode), str(side)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return uuid5(_ORDER_ID_NAMESPACE, identity)


class OrderRequest(BaseModel):
    """Gateway が OMS に発行する注文リクエスト。"""

    order_id: UUID = Field(default_factory=uuid4)
    unified_signal_id: UUID | None = None
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    trade_mode: TradeMode
    signal_source: SignalSource
    routing_intent: RoutingIntent = RoutingIntent.SYSTEM
    strategy_key: str | None = Field(default=None, min_length=1)
    candidate_id: str | None = Field(default=None, min_length=1)
    holding_type: TradingStyle | None = None
    stop_loss_price: Decimal | None = None
    stop_loss_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_order_id(cls, payload: object) -> object:
        if not isinstance(payload, dict) or "order_id" in payload:
            return payload
        unified_signal_id = payload.get("unified_signal_id")
        trade_mode = payload.get("trade_mode")
        side = payload.get("side")
        if unified_signal_id is None or trade_mode is None or side is None:
            return payload
        enriched = dict(payload)
        enriched["order_id"] = deterministic_order_id(
            unified_signal_id=unified_signal_id,
            trade_mode=str(trade_mode),
            side=str(side),
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
        if (
            self.routing_intent is RoutingIntent.PAPER_ONLY
            and self.trade_mode is not TradeMode.PAPER
        ):
            raise ValueError("PAPER_ONLY orders require trade_mode=paper")
        if self.stop_loss_price is not None and self.stop_loss_pct is not None:
            raise ValueError("stop_loss_price and stop_loss_pct are mutually exclusive")
        if self.stop_loss_pct is not None and self.side is not Side.BUY:
            raise ValueError("stop_loss_pct is only valid for BUY orders")
        if self.stop_loss_pct is not None and self.trade_mode is not TradeMode.PAPER:
            raise ValueError("stop_loss_pct is only supported for paper orders")
        return self


class OrderResult(BaseModel):
    """OMS が発注処理の結果として返す約定情報。"""

    order_id: UUID
    status: OrderStatus
    filled_quantity: int = Field(ge=0)
    fill_price: Decimal | None = None
    executed_at: datetime | None = None
    error_message: str | None = None
