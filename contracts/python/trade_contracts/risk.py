from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from .enums import TradeMode, TradingStyle


class RiskCheck(BaseModel):
    """Gateway のリスク検証結果。"""

    passed: bool
    reason: str | None = None
    adjusted_quantity: int | None = Field(default=None, ge=0)


class KillSwitchState(BaseModel):
    """system_status テーブルのシングルトン状態のメモリ表現。"""

    is_trading_allowed: bool
    trade_mode: TradeMode
    trading_style: TradingStyle
    daily_pnl: Decimal
    weekly_pnl: Decimal
    monthly_pnl: Decimal
    daily_loss_limit: Decimal
    weekly_loss_limit: Decimal
    monthly_loss_limit: Decimal
    updated_at: datetime
