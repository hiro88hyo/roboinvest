from __future__ import annotations

import logging
from typing import Any

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from .base import Strategy

logger = logging.getLogger(__name__)


class StrategyEngine:
    """Run a fixed list of strategies over a stream of `ProcessedFeatures`.

    State is held in-process and scoped per (strategy name, symbol).
    Exceptions raised by a single strategy are logged and isolated so one
    bad rule cannot mask the rest.
    """

    def __init__(self, strategies: list[Strategy]) -> None:
        self._strategies: list[Strategy] = list(strategies)
        self._state: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def strategies(self) -> list[Strategy]:
        return list(self._strategies)

    def evaluate(self, features: ProcessedFeatures) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        for strategy in self._strategies:
            key = (strategy.name, features.symbol)
            state = self._state.setdefault(key, {})
            try:
                signal = strategy.evaluate(features, state)
            except Exception:
                logger.exception(
                    "strategy %s failed for symbol %s",
                    strategy.name,
                    features.symbol,
                )
                continue
            if signal is not None:
                signals.append(signal)
        return signals
