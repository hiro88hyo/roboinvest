from __future__ import annotations

from decimal import Decimal

from strategy_rule.config import DEFAULT_STRATEGIES, StrategyRuleSettings


def test_defaults() -> None:
    s = StrategyRuleSettings()
    assert s.strategies_enabled == list(DEFAULT_STRATEGIES)
    assert s.rsi_buy_threshold == Decimal("25")
    assert s.rsi_sell_threshold == Decimal("75")
    assert s.sma_min_gap_ratio == Decimal("0.005")
    assert s.sma_full_confidence_gap_ratio == Decimal("0.02")
    assert s.sma_buy_require_price_above_vwap is False
    assert s.bollinger_breakout_tolerance == Decimal("0.15")
    assert s.bollinger_buy_require_lower_reclaim is False
    assert s.entry_volume_ratio_min is None
    assert s.rsi_buy_require_price_above_vwap is True
    assert s.rsi_buy_require_sma_uptrend is True
    assert s.bollinger_buy_require_price_above_vwap is True
    assert s.bollinger_buy_require_sma_uptrend is True
    assert s.entry_max_spread_bps is None
    assert s.entry_max_spread_ticks is None
    assert s.entry_min_ask_depth_5 is None
    assert s.entry_min_book_imbalance_5 is None
    assert s.entry_min_minutes_from_open is None
    assert s.entry_min_minutes_to_close is None


def test_strategies_enabled_accepts_csv_string() -> None:
    s = StrategyRuleSettings(strategies_enabled="sma_crossover, rsi_threshold")
    assert s.strategies_enabled == ["sma_crossover", "rsi_threshold"]


def test_strategies_enabled_accepts_list() -> None:
    s = StrategyRuleSettings(strategies_enabled=["a", "b"])
    assert s.strategies_enabled == ["a", "b"]


def test_strategies_enabled_drops_blank_entries() -> None:
    s = StrategyRuleSettings(strategies_enabled="a, , b,")
    assert s.strategies_enabled == ["a", "b"]


def test_entry_volume_ratio_min_accepts_decimal_string() -> None:
    s = StrategyRuleSettings(entry_volume_ratio_min="2.0")
    assert s.entry_volume_ratio_min == Decimal("2.0")


def test_entry_volume_ratio_min_treats_empty_string_as_none() -> None:
    s = StrategyRuleSettings(entry_volume_ratio_min="")
    assert s.entry_volume_ratio_min is None


def test_entry_risk_filters_accept_env_values() -> None:
    s = StrategyRuleSettings(
        entry_max_spread_bps="30",
        entry_max_spread_ticks="2",
        entry_min_ask_depth_5="300",
        entry_min_book_imbalance_5="-0.5",
        entry_min_minutes_from_open="15",
        entry_min_minutes_to_close="60",
    )
    assert s.entry_max_spread_bps == Decimal("30")
    assert s.entry_max_spread_ticks == Decimal("2")
    assert s.entry_min_ask_depth_5 == 300
    assert s.entry_min_book_imbalance_5 == Decimal("-0.5")
    assert s.entry_min_minutes_from_open == 15
    assert s.entry_min_minutes_to_close == 60
