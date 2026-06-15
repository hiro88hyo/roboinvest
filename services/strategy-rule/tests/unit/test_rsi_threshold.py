from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from strategy_rule.strategies.rsi_threshold import RsiThresholdStrategy
from trade_contracts.enums import Action
from trade_contracts.features import ProcessedFeatures


def test_no_rsi_returns_none(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = RsiThresholdStrategy()
    assert strategy.evaluate(features_factory(rsi=None), {}) is None


def test_oversold_emits_buy(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = RsiThresholdStrategy(buy_threshold=Decimal("30"))
    signal = strategy.evaluate(features_factory(rsi=Decimal("20")), {})
    assert signal is not None
    assert signal.action is Action.BUY
    # confidence = 0.5 + 0.5 * (30 - 20) / 30 = 0.6666...
    assert abs(signal.confidence - (0.5 + 0.5 * (10 / 30))) < 1e-9


def test_overbought_emits_sell(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = RsiThresholdStrategy(sell_threshold=Decimal("70"))
    signal = strategy.evaluate(features_factory(rsi=Decimal("80")), {})
    assert signal is not None
    assert signal.action is Action.SELL
    # confidence = 0.5 + 0.5 * (80 - 70) / 30 = 0.6666...
    assert abs(signal.confidence - (0.5 + 0.5 * (10 / 30))) < 1e-9


def test_buy_volume_ratio_filter_blocks_low_volume(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(
        buy_threshold=Decimal("30"),
        volume_ratio_min=Decimal("2.0"),
    )
    signal = strategy.evaluate(
        features_factory(rsi=Decimal("20"), volume_ratio=Decimal("1.5")),
        {},
    )
    assert signal is None


def test_buy_price_vwap_filter_blocks_below_vwap(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(
        buy_threshold=Decimal("30"),
        require_price_above_vwap=True,
    )
    signal = strategy.evaluate(
        features_factory(rsi=Decimal("20"), price=Decimal("990"), vwap=Decimal("1000")),
        {},
    )
    assert signal is None


def test_buy_sma_uptrend_filter_blocks_downtrend(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(
        buy_threshold=Decimal("30"),
        require_sma_uptrend=True,
    )
    signal = strategy.evaluate(
        features_factory(
            rsi=Decimal("20"),
            sma_short=Decimal("990"),
            sma_long=Decimal("1000"),
        ),
        {},
    )
    assert signal is None


def test_buy_trend_filters_allow_rebound_above_vwap_in_uptrend(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(
        buy_threshold=Decimal("30"),
        require_price_above_vwap=True,
        require_sma_uptrend=True,
    )
    signal = strategy.evaluate(
        features_factory(
            rsi=Decimal("20"),
            price=Decimal("1005"),
            vwap=Decimal("1000"),
            sma_short=Decimal("1002"),
            sma_long=Decimal("1000"),
        ),
        {},
    )
    assert signal is not None
    assert signal.action is Action.BUY
    assert "price>=vwap" in signal.reasoning
    assert "sma_short>=sma_long" in signal.reasoning


def test_buy_volume_ratio_filter_does_not_block_sell(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(
        sell_threshold=Decimal("70"),
        volume_ratio_min=Decimal("2.0"),
    )
    signal = strategy.evaluate(
        features_factory(rsi=Decimal("80"), volume_ratio=Decimal("1.0")),
        {},
    )
    assert signal is not None
    assert signal.action is Action.SELL


def test_neutral_returns_none(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = RsiThresholdStrategy()
    assert strategy.evaluate(features_factory(rsi=Decimal("50")), {}) is None


def test_at_threshold_yields_minimum_confidence(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RsiThresholdStrategy(buy_threshold=Decimal("30"))
    signal = strategy.evaluate(features_factory(rsi=Decimal("30")), {})
    assert signal is not None
    assert signal.action is Action.BUY
    assert signal.confidence == 0.5


def test_extreme_yields_max_confidence(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = RsiThresholdStrategy(buy_threshold=Decimal("30"))
    signal = strategy.evaluate(features_factory(rsi=Decimal("0")), {})
    assert signal is not None
    assert signal.confidence == 1.0
