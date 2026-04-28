from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Protocol, runtime_checkable

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


@runtime_checkable
class AsyncStrategy(Protocol):
    """Per-feature async strategy evaluator.

    Mirrors `strategy_rule.base.Strategy` but allows `await` inside
    `evaluate` so implementations can call out to LLMs / external APIs.
    State is scoped per (strategy name, symbol) by the engine.
    """

    name: str

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None: ...
