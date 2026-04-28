from __future__ import annotations

import logging
from typing import Any

from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from .base import AsyncStrategy

logger = logging.getLogger(__name__)


class StrategyAiEngine:
    """Run a fixed list of async strategies over a stream of `ProcessedFeatures`.

    state は (strategy.name, symbol) で隔離。1 戦略の例外は他戦略を巻き込まない。
    `strategy_rule.engine.StrategyEngine` の async 版で、当面は AiStrategy 1 本だが
    将来複数 AI ペルソナを足すための器として用意しておく。
    """

    def __init__(self, strategies: list[AsyncStrategy]) -> None:
        self._strategies: list[AsyncStrategy] = list(strategies)
        self._state: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def strategies(self) -> list[AsyncStrategy]:
        return list(self._strategies)

    async def evaluate(self, features: ProcessedFeatures) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        for strategy in self._strategies:
            key = (strategy.name, features.symbol)
            state = self._state.setdefault(key, {})
            try:
                signal = await strategy.evaluate(features, state)
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
