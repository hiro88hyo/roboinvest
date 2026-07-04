from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import OrderStatus, OrderType, Side, SignalSource, TradeMode


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
    stop_loss_price: Decimal | None = None
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    created_at: datetime


class OrderResult(BaseModel):
    """OMS が発注処理の結果として返す約定情報。"""

    order_id: UUID
    status: OrderStatus
    filled_quantity: int = Field(ge=0)
    fill_price: Decimal | None = None
    executed_at: datetime | None = None
    error_message: str | None = None
