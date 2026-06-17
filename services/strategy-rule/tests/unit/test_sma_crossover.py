from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from strategy_rule.strategies.sma_crossover import SmaCrossoverStrategy
from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures


def test_returns_none_when_features_missing(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = SmaCrossoverStrategy()
    state: dict[str, object] = {}
    assert strategy.evaluate(features_factory(sma_short=None, sma_long=None), state) is None
    assert (
        strategy.evaluate(features_factory(sma_short=Decimal("100"), sma_long=None), state) is None
    )


def test_first_observation_only_records_state(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = SmaCrossoverStrategy()
    state: dict[str, object] = {}
    f = features_factory(sma_short=Decimal("100"), sma_long=Decimal("90"))
    assert strategy.evaluate(f, state) is None
    assert state["prev_diff"] == Decimal("10")


def test_upward_cross_emits_buy(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = SmaCrossoverStrategy(full_confidence_gap_ratio=Decimal("0.02"))
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("99"), sma_long=Decimal("100")),
        state,
    )
    # diff = 20, gap_ratio = 0.02 -> confidence 1.0
    signal = strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("120"), sma_long=Decimal("100")),
        state,
    )
    assert signal is not None
    assert signal.action is Action.BUY
    assert signal.source is SignalSource.RULE
    assert signal.confidence == 1.0


def test_downward_cross_emits_sell(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = SmaCrossoverStrategy(full_confidence_gap_ratio=Decimal("0.02"))
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("101"), sma_long=Decimal("100")),
        state,
    )
    signal = strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("90"), sma_long=Decimal("100")),
        state,
    )
    assert signal is not None
    assert signal.action is Action.SELL
    assert 0.0 < signal.confidence <= 1.0


def test_no_cross_no_signal(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = SmaCrossoverStrategy()
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(sma_short=Decimal("110"), sma_long=Decimal("100")),
        state,
    )
    signal = strategy.evaluate(
        features_factory(sma_short=Decimal("115"), sma_long=Decimal("100")),
        state,
    )
    assert signal is None


def test_min_gap_ratio_filters_micro_cross(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = SmaCrossoverStrategy(min_gap_ratio=Decimal("0.01"))
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("99"), sma_long=Decimal("100")),
        state,
    )
    # cross with tiny gap (1 / 1000 = 0.001) below min_gap_ratio
    signal = strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("101"), sma_long=Decimal("100")),
        state,
    )
    assert signal is None


def test_confidence_scales_with_gap(features_factory: Callable[..., ProcessedFeatures]) -> None:
    strategy = SmaCrossoverStrategy(full_confidence_gap_ratio=Decimal("0.02"))
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("99"), sma_long=Decimal("100")),
        state,
    )
    # gap = 10 / 1000 = 0.01 -> confidence = 0.01 / 0.02 = 0.5
    signal = strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("110"), sma_long=Decimal("100")),
        state,
    )
    assert signal is not None
    assert signal.confidence == 0.5


def test_buy_entry_filters_block_unsafe_upward_cross(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = SmaCrossoverStrategy(
        full_confidence_gap_ratio=Decimal("0.02"),
        volume_ratio_min=Decimal("2.0"),
        require_price_above_vwap=True,
        max_spread_ticks=Decimal("2"),
        min_ask_depth_5=300,
        min_minutes_from_open=15,
    )
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("99"), sma_long=Decimal("100")),
        state,
    )
    assert strategy.evaluate(
        features_factory(
            price=Decimal("1000"),
            vwap=Decimal("1001"),
            volume_ratio=Decimal("3.0"),
            spread_ticks=Decimal("1"),
            ask_depth_5=500,
            minutes_from_open=20,
            sma_short=Decimal("120"),
            sma_long=Decimal("100"),
        ),
        state,
    ) is None


def test_buy_entry_filters_allow_safe_upward_cross(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = SmaCrossoverStrategy(
        full_confidence_gap_ratio=Decimal("0.02"),
        volume_ratio_min=Decimal("2.0"),
        require_price_above_vwap=True,
        max_spread_ticks=Decimal("2"),
        min_ask_depth_5=300,
        min_minutes_from_open=15,
    )
    state: dict[str, object] = {}
    strategy.evaluate(
        features_factory(price=Decimal("1000"), sma_short=Decimal("99"), sma_long=Decimal("100")),
        state,
    )
    signal = strategy.evaluate(
        features_factory(
            price=Decimal("1002"),
            vwap=Decimal("1001"),
            volume_ratio=Decimal("3.0"),
            spread_ticks=Decimal("1"),
            ask_depth_5=500,
            minutes_from_open=20,
            sma_short=Decimal("120"),
            sma_long=Decimal("100"),
        ),
        state,
    )
    assert signal is not None
    assert signal.action is Action.BUY
    assert signal.reasoning is not None
    assert "volume_ratio_min=2.0" in signal.reasoning
    assert "spread_ticks<=2" in signal.reasoning
