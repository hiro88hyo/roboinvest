from __future__ import annotations

from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from strategy_rule.engine import StrategyEngine
from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


class _AlwaysBuyStrategy:
    name = "always_buy"

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        state["calls"] = state.get("calls", 0) + 1
        return StrategySignal(
            source=SignalSource.RULE,
            symbol=features.symbol,
            action=Action.BUY,
            confidence=0.8,
            reasoning="always",
            created_at=features.timestamp,
        )


class _BoomStrategy:
    name = "boom"

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        raise RuntimeError("boom")


class _NoneStrategy:
    name = "none"

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        return None


def test_aggregates_signals_from_all_strategies(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    engine = StrategyEngine([_AlwaysBuyStrategy(), _NoneStrategy(), _AlwaysBuyStrategy()])
    signals = engine.evaluate(features_factory())
    assert len(signals) == 2
    assert all(s.action is Action.BUY for s in signals)


def test_strategy_failure_is_isolated(features_factory: Callable[..., ProcessedFeatures]) -> None:
    engine = StrategyEngine([_BoomStrategy(), _AlwaysBuyStrategy()])
    signals = engine.evaluate(features_factory())
    assert len(signals) == 1
    assert signals[0].action is Action.BUY


def test_state_is_scoped_per_strategy_and_symbol(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    s = _AlwaysBuyStrategy()
    engine = StrategyEngine([s])
    engine.evaluate(features_factory(symbol="7203"))
    engine.evaluate(features_factory(symbol="7203"))
    engine.evaluate(features_factory(symbol="9984"))
    state_7203 = engine._state[(s.name, "7203")]
    state_9984 = engine._state[(s.name, "9984")]
    assert state_7203["calls"] == 2
    assert state_9984["calls"] == 1


def test_strategies_property_returns_copy() -> None:
    s = _AlwaysBuyStrategy()
    engine = StrategyEngine([s])
    out = engine.strategies
    out.append(_NoneStrategy())
    assert len(engine.strategies) == 1


def test_signal_carries_feature_timestamp() -> None:
    ts = datetime(2026, 4, 20, 12, 34, 56, tzinfo=UTC)
    engine = StrategyEngine([_AlwaysBuyStrategy()])
    f = ProcessedFeatures(symbol="7203", timestamp=ts, price=Decimal("1000"))
    signals = engine.evaluate(f)
    assert signals[0].created_at == ts
