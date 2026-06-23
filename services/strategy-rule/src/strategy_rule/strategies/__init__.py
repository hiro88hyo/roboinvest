"""Built-in rule-based strategy plugins."""

from __future__ import annotations

from ..config import StrategyRuleSettings
from ..registry import register
from .bollinger_breakout import BollingerBreakoutStrategy
from .opening_range_breakout import OpeningRangeBreakoutStrategy
from .relative_momentum import RelativeMomentumStrategy
from .rsi_threshold import RsiThresholdStrategy
from .sma_crossover import SmaCrossoverStrategy

__all__ = [
    "BollingerBreakoutStrategy",
    "OpeningRangeBreakoutStrategy",
    "RelativeMomentumStrategy",
    "RsiThresholdStrategy",
    "SmaCrossoverStrategy",
    "register_builtin",
]


def _make_sma(settings: StrategyRuleSettings) -> SmaCrossoverStrategy:
    return SmaCrossoverStrategy(
        min_gap_ratio=settings.sma_min_gap_ratio,
        full_confidence_gap_ratio=settings.sma_full_confidence_gap_ratio,
        volume_ratio_min=settings.entry_volume_ratio_min,
        require_price_above_vwap=settings.sma_buy_require_price_above_vwap,
        max_price=settings.entry_max_price,
        max_spread_bps=settings.entry_max_spread_bps,
        max_spread_ticks=settings.entry_max_spread_ticks,
        min_ask_depth_5=settings.entry_min_ask_depth_5,
        min_book_imbalance_5=settings.entry_min_book_imbalance_5,
        min_minutes_from_open=settings.entry_min_minutes_from_open,
        min_minutes_to_close=settings.entry_min_minutes_to_close,
        max_book_age_seconds=settings.entry_max_book_age_seconds,
        buy_target_pct=settings.buy_target_pct,
        buy_trailing_stop_pct=settings.buy_trailing_stop_pct,
    )


def _make_rsi(settings: StrategyRuleSettings) -> RsiThresholdStrategy:
    return RsiThresholdStrategy(
        buy_threshold=settings.rsi_buy_threshold,
        sell_threshold=settings.rsi_sell_threshold,
        volume_ratio_min=settings.entry_volume_ratio_min,
        require_price_above_vwap=settings.rsi_buy_require_price_above_vwap,
        require_sma_uptrend=settings.rsi_buy_require_sma_uptrend,
        max_price=settings.entry_max_price,
        max_spread_bps=settings.entry_max_spread_bps,
        max_spread_ticks=settings.entry_max_spread_ticks,
        min_ask_depth_5=settings.entry_min_ask_depth_5,
        min_book_imbalance_5=settings.entry_min_book_imbalance_5,
        min_minutes_from_open=settings.entry_min_minutes_from_open,
        min_minutes_to_close=settings.entry_min_minutes_to_close,
        max_book_age_seconds=settings.entry_max_book_age_seconds,
        buy_target_pct=settings.buy_target_pct,
        buy_trailing_stop_pct=settings.buy_trailing_stop_pct,
    )


def _make_bollinger(settings: StrategyRuleSettings) -> BollingerBreakoutStrategy:
    return BollingerBreakoutStrategy(
        tolerance=settings.bollinger_breakout_tolerance,
        volume_ratio_min=settings.entry_volume_ratio_min,
        require_buy_lower_reclaim=settings.bollinger_buy_require_lower_reclaim,
        require_price_above_vwap=settings.bollinger_buy_require_price_above_vwap,
        require_sma_uptrend=settings.bollinger_buy_require_sma_uptrend,
        max_price=settings.entry_max_price,
        max_spread_bps=settings.entry_max_spread_bps,
        max_spread_ticks=settings.entry_max_spread_ticks,
        min_ask_depth_5=settings.entry_min_ask_depth_5,
        min_book_imbalance_5=settings.entry_min_book_imbalance_5,
        min_minutes_from_open=settings.entry_min_minutes_from_open,
        min_minutes_to_close=settings.entry_min_minutes_to_close,
        max_book_age_seconds=settings.entry_max_book_age_seconds,
        buy_target_pct=settings.buy_target_pct,
        buy_trailing_stop_pct=settings.buy_trailing_stop_pct,
    )


def _make_opening_range_breakout(settings: StrategyRuleSettings) -> OpeningRangeBreakoutStrategy:
    return OpeningRangeBreakoutStrategy(
        range_minutes=settings.orb_range_minutes,
        entry_minute=settings.orb_entry_minute,
        min_minutes_to_close=settings.orb_min_minutes_to_close,
        max_stop_risk_bps=settings.orb_max_stop_risk_bps,
        cooldown_seconds=settings.orb_cooldown_seconds,
        require_vwap=settings.orb_require_vwap,
        target_r_multiple=settings.orb_target_r_multiple,
        min_breakout_volume_delta=settings.orb_min_breakout_volume_delta,
        min_opening_range_volume=settings.orb_min_opening_range_volume,
        max_price=settings.entry_max_price,
        max_spread_bps=settings.entry_max_spread_bps,
        max_spread_ticks=settings.entry_max_spread_ticks,
        min_ask_depth_5=settings.entry_min_ask_depth_5,
        min_book_imbalance_5=settings.entry_min_book_imbalance_5,
        max_book_age_seconds=settings.entry_max_book_age_seconds,
    )


def _make_relative_momentum(settings: StrategyRuleSettings) -> RelativeMomentumStrategy:
    return RelativeMomentumStrategy(
        min_return_from_open_bps=settings.relative_momentum_min_return_from_open_bps,
        min_peer_percentile=settings.relative_momentum_min_peer_percentile,
        min_vwap_distance_bps=settings.relative_momentum_min_vwap_distance_bps,
        min_minutes_from_open=settings.relative_momentum_min_minutes_from_open,
        min_minutes_to_close=settings.relative_momentum_min_minutes_to_close,
        max_stop_risk_bps=settings.relative_momentum_max_stop_risk_bps,
        target_r_multiple=settings.relative_momentum_target_r_multiple,
        max_price=settings.entry_max_price,
        max_spread_bps=settings.entry_max_spread_bps,
        max_spread_ticks=settings.entry_max_spread_ticks,
        min_ask_depth_5=settings.entry_min_ask_depth_5,
        min_book_imbalance_5=settings.entry_min_book_imbalance_5,
        max_book_age_seconds=settings.entry_max_book_age_seconds,
    )


def register_builtin() -> None:
    """Register all built-in strategies. Idempotent."""
    register("sma_crossover", _make_sma)
    register("rsi_threshold", _make_rsi)
    register("bollinger_breakout", _make_bollinger)
    register("opening_range_breakout", _make_opening_range_breakout)
    register("relative_momentum", _make_relative_momentum)
