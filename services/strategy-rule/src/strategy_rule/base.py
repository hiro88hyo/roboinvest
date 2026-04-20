from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Protocol, runtime_checkable

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal


@runtime_checkable
class Strategy(Protocol):
    """Per-feature rule evaluator.

    Implementations must be side-effect free aside from mutating the
    `state` mapping handed in by the engine. State is scoped per
    (strategy name, symbol) so strategies do not need to track symbols
    themselves.
    """

    name: str

    def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None: ...
