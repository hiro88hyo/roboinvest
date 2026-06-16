from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from strategy_rule.strategies.bollinger_breakout import BollingerBreakoutStrategy
from trade_contracts.enums import Action
from trade_contracts.features import ProcessedFeatures


def test_missing_bands_returns_none(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = BollingerBreakoutStrategy()
    assert strategy.evaluate(features_factory(bollinger_upper=None), {}) is None


def test_zero_band_width_returns_none(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = BollingerBreakoutStrategy()
    f = features_factory(
        price=Decimal("100"),
        bollinger_upper=Decimal("100"),
        bollinger_lower=Decimal("100"),
        bollinger_middle=Decimal("100"),
    )
    assert strategy.evaluate(f, {}) is None


def test_below_lower_emits_buy(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = BollingerBreakoutStrategy()
    f = features_factory(
        price=Decimal("90"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("95"),
    )
    signal = strategy.evaluate(f, {})
    assert signal is not None
    assert signal.action is Action.BUY
    # distance = (95 - 90) / (110 - 95) = 5 / 15 = 0.333...
    assert abs(signal.confidence - (5 / 15)) < 1e-9


def test_buy_reclaim_confirmation_arms_then_buys_on_lower_band_reclaim(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = BollingerBreakoutStrategy(require_buy_lower_reclaim=True)
    state: dict[str, object] = {}
    below = features_factory(
        price=Decimal("90"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("95"),
    )
    assert strategy.evaluate(below, state) is None

    reclaim = features_factory(
        price=Decimal("96"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("95"),
    )
    signal = strategy.evaluate(reclaim, state)
    assert signal is not None
    assert signal.action is Action.BUY
    assert signal.reasoning is not None
    assert "reclaimed lower band" in signal.reasoning
    assert abs(signal.confidence - (5 / 15)) < 1e-9


def test_buy_reclaim_confirmation_does_not_buy_without_prior_break(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = BollingerBreakoutStrategy(require_buy_lower_reclaim=True)
    reclaim = features_factory(
        price=Decimal("96"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("95"),
    )
    assert strategy.evaluate(reclaim, {}) is None


def test_above_upper_emits_sell(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = BollingerBreakoutStrategy()
    f = features_factory(
        price=Decimal("130"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("90"),
    )
    signal = strategy.evaluate(f, {})
    assert signal is not None
    assert signal.action is Action.SELL
    # distance = (130 - 110) / (110 - 90) = 20 / 20 = 1.0
    assert signal.confidence == 1.0


def test_buy_volume_ratio_filter_blocks_low_volume(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = BollingerBreakoutStrategy(volume_ratio_min=Decimal("2.0"))
    f = features_factory(
        price=Decimal("90"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("95"),
        volume_ratio=Decimal("1.5"),
    )
    assert strategy.evaluate(f, {}) is None


def test_inside_band_returns_none(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = BollingerBreakoutStrategy()
    f = features_factory(
        price=Decimal("100"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("90"),
    )
    assert strategy.evaluate(f, {}) is None


def test_tolerance_requires_stronger_breakout(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = BollingerBreakoutStrategy(tolerance=Decimal("0.5"))
    # band_width = 20, tolerance margin = 10. Need price < 80 or > 120.
    inside = features_factory(
        price=Decimal("85"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("90"),
    )
    assert strategy.evaluate(inside, {}) is None
    outside = features_factory(
        price=Decimal("75"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("90"),
    )
    signal = strategy.evaluate(outside, {})
    assert signal is not None
    assert signal.action is Action.BUY
