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
    assert s.bollinger_breakout_tolerance == Decimal("0.15")


def test_strategies_enabled_accepts_csv_string() -> None:
    s = StrategyRuleSettings(strategies_enabled="sma_crossover, rsi_threshold")
    assert s.strategies_enabled == ["sma_crossover", "rsi_threshold"]


def test_strategies_enabled_accepts_list() -> None:
    s = StrategyRuleSettings(strategies_enabled=["a", "b"])
    assert s.strategies_enabled == ["a", "b"]


def test_strategies_enabled_drops_blank_entries() -> None:
    s = StrategyRuleSettings(strategies_enabled="a, , b,")
    assert s.strategies_enabled == ["a", "b"]
