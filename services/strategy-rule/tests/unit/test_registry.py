from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
from strategy_rule import registry
from strategy_rule.config import StrategyRuleSettings
from strategy_rule.strategies import (
    BollingerBreakoutStrategy,
    OpeningRangeBreakoutStrategy,
    RelativeMomentumStrategy,
    RsiThresholdStrategy,
    SmaCrossoverStrategy,
    register_builtin,
)
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


class _DummyStrategy:
    name = "dummy"

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        return None


def _make_dummy(_settings: StrategyRuleSettings) -> _DummyStrategy:
    return _DummyStrategy()


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    snapshot = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def test_register_and_build() -> None:
    registry.register("dummy", _make_dummy)
    settings = StrategyRuleSettings(strategies_enabled=["dummy"])
    out = registry.build_strategies(settings)
    assert len(out) == 1
    assert out[0].name == "dummy"


def test_build_skips_unknown_names() -> None:
    registry.register("dummy", _make_dummy)
    settings = StrategyRuleSettings(strategies_enabled=["dummy", "nope"])
    out = registry.build_strategies(settings)
    assert [s.name for s in out] == ["dummy"]


def test_build_preserves_declaration_order() -> None:
    registry.register("a", _make_dummy)
    registry.register("b", _make_dummy)
    settings = StrategyRuleSettings(strategies_enabled=["b", "a"])
    out = registry.build_strategies(settings)
    assert len(out) == 2


def test_register_builtin_registers_current_builtin_set() -> None:
    register_builtin()
    names = registry.registered_names()
    assert "sma_crossover" in names
    assert "rsi_threshold" in names
    assert "bollinger_breakout" in names
    assert "opening_range_breakout" in names
    assert "relative_momentum" in names


def test_builtin_factories_produce_expected_types() -> None:
    register_builtin()
    settings = StrategyRuleSettings(
        strategies_enabled=[
            "sma_crossover",
            "rsi_threshold",
            "bollinger_breakout",
            "opening_range_breakout",
            "relative_momentum",
        ],
    )
    out = registry.build_strategies(settings)
    types = {type(s) for s in out}
    assert types == {
        SmaCrossoverStrategy,
        RsiThresholdStrategy,
        BollingerBreakoutStrategy,
        OpeningRangeBreakoutStrategy,
        RelativeMomentumStrategy,
    }


def test_unregister_removes() -> None:
    registry.register("dummy", _make_dummy)
    registry.unregister("dummy")
    assert "dummy" not in registry.registered_names()
    registry.unregister("dummy")  # idempotent
