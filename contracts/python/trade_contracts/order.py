from datetime import date, datetime
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from .enums import OrderStatus, OrderType, Side, SignalSource, TradeMode, TradingStyle


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
    holding_type: TradingStyle | None = None
    stop_loss_price: Decimal | None = None
    stop_loss_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_stop_loss_intent(self) -> Self:
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
