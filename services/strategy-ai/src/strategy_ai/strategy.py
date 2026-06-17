from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.logging import event_extra
from trade_contracts.signal import StrategySignal, execution_fields_from

from .llm.base import LLMClient, LLMError
from .parser import parse_response
from .prompt import build_prompt

logger = logging.getLogger(__name__)

_LAST_CALL_KEY = "last_call_at"


@dataclass(slots=True)
class AiStrategyStats:
    llm_calls: int = 0
    llm_successes: int = 0
    llm_errors: int = 0
    parse_failures: int = 0
    hold_decisions: int = 0
    confidence_rejects: int = 0
    signals_emitted: int = 0


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
    stats: AiStrategyStats = field(default_factory=AiStrategyStats)

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        if not self._should_call(features.timestamp, state):
            logger.debug(
                "ai skipped by rate limit: symbol=%s strategy=%s",
                features.symbol,
                self.name,
                extra=event_extra(
                    "ai_decision_skipped",
                    symbol=features.symbol,
                    strategy=self.name,
                    reason="rate_limited",
                    feature_timestamp=features.timestamp.isoformat(),
                    min_interval_seconds=self.min_interval_seconds,
                ),
            )
            return None
        state[_LAST_CALL_KEY] = features.timestamp

        prompt = build_prompt(features)
        self.stats.llm_calls += 1
        try:
            response = await self.llm.complete(prompt)
        except LLMError:
            self.stats.llm_errors += 1
            logger.exception(
                "llm call failed: symbol=%s",
                features.symbol,
                extra=event_extra(
                    "external_api_error",
                    api_name="llm",
                    endpoint="complete",
                    symbol=features.symbol,
                    strategy=self.name,
                    reason="llm_error",
                    feature_timestamp=features.timestamp.isoformat(),
                ),
            )
            return None

        self.stats.llm_successes += 1
        decision = parse_response(response)
        if decision is None:
            self.stats.parse_failures += 1
            logger.warning(
                "ai decision skipped: symbol=%s reason=parse_failed",
                features.symbol,
                extra=event_extra(
                    "ai_decision_skipped",
                    symbol=features.symbol,
                    strategy=self.name,
                    reason="parse_failed",
                    feature_timestamp=features.timestamp.isoformat(),
                ),
            )
            return None
        if decision.action is Action.HOLD:
            self.stats.hold_decisions += 1
            logger.info(
                "ai decision skipped: symbol=%s reason=hold confidence=%.3f",
                features.symbol,
                decision.confidence,
                extra=event_extra(
                    "ai_decision_skipped",
                    symbol=features.symbol,
                    strategy=self.name,
                    reason="hold",
                    action=decision.action.value,
                    confidence=decision.confidence,
                    feature_timestamp=features.timestamp.isoformat(),
                ),
            )
            return None
        if decision.confidence <= 0.0:
            self.stats.confidence_rejects += 1
            logger.info(
                "ai decision skipped: symbol=%s reason=non_positive_confidence action=%s",
                features.symbol,
                decision.action.value,
                extra=event_extra(
                    "ai_decision_skipped",
                    symbol=features.symbol,
                    strategy=self.name,
                    reason="non_positive_confidence",
                    action=decision.action.value,
                    confidence=decision.confidence,
                    feature_timestamp=features.timestamp.isoformat(),
                ),
            )
            return None

        self.stats.signals_emitted += 1
        return StrategySignal(
            source=SignalSource.AI,
            symbol=features.symbol,
            price=features.price,
            action=decision.action,
            confidence=decision.confidence,
            reasoning=decision.reasoning or None,
            **execution_fields_from(features),
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
