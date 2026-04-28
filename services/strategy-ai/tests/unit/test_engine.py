from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import pytest
from strategy_ai._testing import FakeLLMClient, make_features
from strategy_ai.engine import StrategyAiEngine
from strategy_ai.strategy import AiStrategy
from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


class _AlwaysBuy:
    name = "always_buy"

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        state["seen"] = state.get("seen", 0) + 1
        return StrategySignal(
            source=SignalSource.AI,
            symbol=features.symbol,
            action=Action.BUY,
            confidence=0.5,
            reasoning="always",
            created_at=features.timestamp,
        )


class _Boom:
    name = "boom"

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        raise RuntimeError("kapow")


@pytest.mark.asyncio
async def test_engine_runs_each_strategy_in_order() -> None:
    llm = FakeLLMClient(['{"action": "SELL", "confidence": 0.9, "reasoning": "x"}'])
    engine = StrategyAiEngine(
        [
            _AlwaysBuy(),
            AiStrategy(llm=llm, name="ai", min_interval_seconds=0),
        ]
    )

    signals = await engine.evaluate(make_features(symbol="7203"))

    assert [s.action for s in signals] == [Action.BUY, Action.SELL]
    assert [s.source for s in signals] == [SignalSource.AI, SignalSource.AI]


@pytest.mark.asyncio
async def test_engine_isolates_state_per_symbol() -> None:
    counter = _AlwaysBuy()
    engine = StrategyAiEngine([counter])

    await engine.evaluate(make_features(symbol="7203"))
    await engine.evaluate(make_features(symbol="7203"))
    await engine.evaluate(make_features(symbol="9984"))

    state = engine._state
    assert state[("always_buy", "7203")]["seen"] == 2
    assert state[("always_buy", "9984")]["seen"] == 1


@pytest.mark.asyncio
async def test_engine_isolates_strategy_failures() -> None:
    engine = StrategyAiEngine([_Boom(), _AlwaysBuy()])

    signals = await engine.evaluate(make_features())

    assert len(signals) == 1
    assert signals[0].action is Action.BUY
