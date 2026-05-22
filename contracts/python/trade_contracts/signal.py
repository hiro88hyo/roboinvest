from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import Action, SignalSource, TradingStyle


class StrategySignal(BaseModel):
    """Strategy A / B が個別に出力するシグナル。"""

    signal_id: UUID = Field(default_factory=uuid4)
    source: SignalSource
    symbol: str
    price: Decimal | None = None
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None
    created_at: datetime


class UnifiedTradeSignal(BaseModel):
    """Aggregator が合議した最終シグナル。Gateway の入力となる。"""

    signal_id: UUID = Field(default_factory=uuid4)
    symbol: str
    price: Decimal | None = None
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    signal_source: SignalSource

    strategy_signal_id_a: UUID | None = None
    strategy_signal_id_b: UUID | None = None

    holding_type: TradingStyle
    stop_loss_price: Decimal | None = None
    target_price: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)

    created_at: datetime
