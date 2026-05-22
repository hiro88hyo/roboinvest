from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from .llm.base import LLMClient, LLMError
from .parser import parse_response
from .prompt import build_prompt

logger = logging.getLogger(__name__)

_LAST_CALL_KEY = "last_call_at"


@dataclass(slots=True)
class AiStrategy:
    """LLM 推論で BUY / SELL を判定する単一戦略。

    - レート制御: 銘柄ごとに `min_interval_seconds` 未満の間隔ではスキップ
    - LLM 失敗・パース失敗・HOLD は `None` を返す (Aggregator に HOLD は流さない)
    - 成功時は `StrategySignal(source=AI, reasoning=<LLMの説明>)` を返す
    """

    llm: LLMClient
    min_interval_seconds: float = 300.0
    name: str = "ai_consensus"

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        if not self._should_call(features.timestamp, state):
            return None
        state[_LAST_CALL_KEY] = features.timestamp

        prompt = build_prompt(features)
        try:
            response = await self.llm.complete(prompt)
        except LLMError:
            logger.exception("llm call failed: symbol=%s", features.symbol)
            return None

        decision = parse_response(response)
        if decision is None:
            return None
        if decision.action is Action.HOLD:
            logger.debug("ai HOLD skipped: symbol=%s", features.symbol)
            return None
        if decision.confidence <= 0.0:
            logger.debug(
                "ai non-positive confidence skipped: symbol=%s action=%s",
                features.symbol,
                decision.action.value,
            )
            return None

        return StrategySignal(
            source=SignalSource.AI,
            symbol=features.symbol,
            price=features.price,
            action=decision.action,
            confidence=decision.confidence,
            reasoning=decision.reasoning or None,
            created_at=features.timestamp,
        )

    def _should_call(
        self,
        now: datetime,
        state: MutableMapping[str, Any],
    ) -> bool:
        last = state.get(_LAST_CALL_KEY)
        if last is None:
            return True
        if not isinstance(last, datetime):
            return True
        elapsed = (now - last).total_seconds()
        if elapsed < 0:
            return True
        return elapsed >= self.min_interval_seconds
