from __future__ import annotations

from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from strategy_rule.backtest.runner import run_backtest
from strategy_rule.engine import StrategyEngine
from strategy_rule.strategies.sma_crossover import SmaCrossoverStrategy
from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


class _CountingStrategy:
    name = "counting"

    def __init__(self) -> None:
        self.seen = 0

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        self.seen += 1
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            action=Action.BUY,
            confidence=0.5,
            reasoning="counted",
            created_at=features.timestamp,
        )


def test_counts_features_and_signals(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    engine = StrategyEngine([_CountingStrategy()])
    feats = [features_factory(symbol="7203"), features_factory(symbol="9984")]
    summary = run_backtest(engine, feats)
    assert summary.feature_count == 2
    assert summary.signal_count == 2
    assert len(summary.signals) == 2


def test_handles_empty_input() -> None:
    engine = StrategyEngine([_CountingStrategy()])
    summary = run_backtest(engine, [])
    assert summary.feature_count == 0
    assert summary.signal_count == 0
    assert summary.signals == []


def test_state_carries_across_features(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    """SMA crossover needs prior diff to detect a cross. The runner must
    feed features in order so the strategy can build per-symbol state."""
    engine = StrategyEngine([SmaCrossoverStrategy(full_confidence_gap_ratio=Decimal("0.02"))])
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    feats = [
        features_factory(
            timestamp=base,
            price=Decimal("1000"),
            sma_short=Decimal("99"),
            sma_long=Decimal("100"),
        ),
        features_factory(
            timestamp=base + timedelta(minutes=1),
            price=Decimal("1000"),
            sma_short=Decimal("120"),
            sma_long=Decimal("100"),
        ),
    ]
    summary = run_backtest(engine, feats)
    assert summary.signal_count == 1
    assert summary.signals[0].action is Action.BUY
