"""Built-in rule-based strategy plugins."""

from __future__ import annotations

from ..config import StrategyRuleSettings
from ..registry import register
from .bollinger_breakout import BollingerBreakoutStrategy
from .rsi_threshold import RsiThresholdStrategy
from .sma_crossover import SmaCrossoverStrategy

__all__ = [
    "BollingerBreakoutStrategy",
    "RsiThresholdStrategy",
    "SmaCrossoverStrategy",
    "register_builtin",
]


def _make_sma(settings: StrategyRuleSettings) -> SmaCrossoverStrategy:
    return SmaCrossoverStrategy(
        min_gap_ratio=settings.sma_min_gap_ratio,
        full_confidence_gap_ratio=settings.sma_full_confidence_gap_ratio,
    )


def _make_rsi(settings: StrategyRuleSettings) -> RsiThresholdStrategy:
    return RsiThresholdStrategy(
        buy_threshold=settings.rsi_buy_threshold,
        sell_threshold=settings.rsi_sell_threshold,
        volume_ratio_min=settings.entry_volume_ratio_min,
        require_price_above_vwap=settings.rsi_buy_require_price_above_vwap,
        require_sma_uptrend=settings.rsi_buy_require_sma_uptrend,
    )


def _make_bollinger(settings: StrategyRuleSettings) -> BollingerBreakoutStrategy:
    return BollingerBreakoutStrategy(
        tolerance=settings.bollinger_breakout_tolerance,
        volume_ratio_min=settings.entry_volume_ratio_min,
        require_buy_lower_reclaim=settings.bollinger_buy_require_lower_reclaim,
    )


def register_builtin() -> None:
    """Register all built-in strategies. Idempotent."""
    register("sma_crossover", _make_sma)
    register("rsi_threshold", _make_rsi)
    register("bollinger_breakout", _make_bollinger)
