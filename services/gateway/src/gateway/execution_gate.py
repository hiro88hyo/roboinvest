"""Execution-quality entry gate for BUY orders.

This module is intentionally pure: it only inspects the unified signal and the
final approved quantity. The streaming runner decides whether a failure is
log-only or an actual reject.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trade_contracts.enums import Action
from trade_contracts.signal import UnifiedTradeSignal


@dataclass(frozen=True, slots=True)
class ExecutionGateConfig:
    max_spread_bps: Decimal = Decimal("30")
    max_spread_ticks: Decimal = Decimal("2")
    min_ask_depth_multiplier: Decimal = Decimal("3")


def reject_reason(
    *,
    signal: UnifiedTradeSignal,
    quantity: int,
    config: ExecutionGateConfig,
) -> str | None:
    if signal.action is not Action.BUY:
        return None
    if quantity <= 0:
        return "execution_invalid_quantity"

    if (
        config.max_spread_bps > 0
        and signal.spread_bps is not None
        and signal.spread_bps > config.max_spread_bps
    ):
        return "execution_spread_too_wide"

    if (
        config.max_spread_ticks > 0
        and signal.spread_ticks is not None
        and signal.spread_ticks > config.max_spread_ticks
    ):
        return "execution_spread_ticks_too_wide"

    if (
        config.min_ask_depth_multiplier > 0
        and signal.ask_depth_5 is not None
        and Decimal(signal.ask_depth_5) < Decimal(quantity) * config.min_ask_depth_multiplier
    ):
        return "execution_insufficient_ask_depth"

    return None
