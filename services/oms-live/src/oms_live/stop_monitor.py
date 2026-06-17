"""Live position stop/target/trailing monitor decisions.

This module is intentionally pure. The streaming runner supplies the latest
executable bid price from OrderBookSnapshot and performs broker/Supabase I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .models import LivePosition

_PRICE_QUANT = Decimal("1")


@dataclass(frozen=True, slots=True)
class StopDecision:
    action: Literal["exit", "trail", "hold"]
    reason: str | None = None
    new_stop_loss_price: Decimal | None = None


_HOLD = StopDecision(action="hold")


def evaluate_live_stop(
    *,
    position: LivePosition,
    latest_bid: Decimal,
    now: datetime,
) -> StopDecision:
    """Evaluate live exit/trailing rules from the latest executable bid.

    Stop-loss applies to both day and swing positions. This is the safety path
    that does not depend on strategy SELL signals.
    """

    if position.stop_loss_price is not None and latest_bid <= position.stop_loss_price:
        return StopDecision(action="exit", reason="stop_loss")

    if position.target_price is not None and latest_bid >= position.target_price:
        return StopDecision(action="exit", reason="target")

    if position.max_hold_days is not None:
        elapsed_days = (now.date() - position.opened_at.date()).days
        if elapsed_days >= position.max_hold_days:
            return StopDecision(action="exit", reason="max_hold_days")

    if position.trailing_stop_pct is not None:
        candidate = (latest_bid * (Decimal(1) - position.trailing_stop_pct)).quantize(
            _PRICE_QUANT, rounding=ROUND_HALF_UP
        )
        existing = position.stop_loss_price
        if existing is None or candidate > existing:
            return StopDecision(action="trail", new_stop_loss_price=candidate)

    return _HOLD
