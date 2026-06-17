"""Day position stop/target/trailing monitor decisions for OMS Paper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from trade_contracts.enums import TradingStyle

from .models import PaperPosition

_PRICE_QUANT = Decimal("1")


@dataclass(frozen=True, slots=True)
class DayDecision:
    action: Literal["exit", "trail", "hold"]
    reason: str | None = None
    new_stop_loss_price: Decimal | None = None


_HOLD = DayDecision(action="hold")


def evaluate_day_exit(
    *,
    position: PaperPosition,
    latest_price: Decimal,
    now: datetime,
) -> DayDecision:
    """Evaluate day position exit/trailing rules from the latest executable bid."""

    if position.holding_type is not TradingStyle.DAY:
        return _HOLD

    if position.stop_loss_price is not None and latest_price <= position.stop_loss_price:
        return DayDecision(action="exit", reason="stop_loss")

    if position.target_price is not None and latest_price >= position.target_price:
        return DayDecision(action="exit", reason="target")

    if position.max_hold_days is not None:
        elapsed_days = (now.date() - position.opened_at.date()).days
        if elapsed_days >= position.max_hold_days:
            return DayDecision(action="exit", reason="max_hold_days")

    if position.trailing_stop_pct is not None:
        candidate = (latest_price * (Decimal(1) - position.trailing_stop_pct)).quantize(
            _PRICE_QUANT, rounding=ROUND_HALF_UP
        )
        existing = position.stop_loss_price
        if existing is None or candidate > existing:
            return DayDecision(action="trail", new_stop_loss_price=candidate)

    return _HOLD
