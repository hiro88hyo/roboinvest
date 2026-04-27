from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from strategy_ai._testing import FakeLLMClient, make_features
from strategy_ai.llm.base import LLMError
from strategy_ai.strategy import AiStrategy
from trade_contracts.enums import Action, SignalSource


@pytest.mark.asyncio
async def test_strategy_returns_signal_on_buy() -> None:
    llm = FakeLLMClient(['{"action": "BUY", "confidence": 0.8, "reasoning": "RSI low"}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}
    features = make_features(symbol="7203", price=Decimal("1000"))

    signal = await strategy.evaluate(features, state)

    assert signal is not None
    assert signal.action is Action.BUY
    assert signal.source is SignalSource.AI
    assert signal.symbol == "7203"
    assert signal.confidence == 0.8
    assert signal.reasoning == "RSI low"
    assert signal.created_at == features.timestamp
    assert state["last_call_at"] == features.timestamp
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_strategy_skips_hold() -> None:
    llm = FakeLLMClient(['{"action": "HOLD", "confidence": 0.5, "reasoning": "flat"}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}

    signal = await strategy.evaluate(make_features(), state)

    assert signal is None
    assert state["last_call_at"] is not None  # rate limit clock starts even on HOLD


@pytest.mark.asyncio
async def test_strategy_swallows_llm_error() -> None:
    llm = FakeLLMClient([LLMError("boom")])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    signal = await strategy.evaluate(make_features(), {})

    assert signal is None


@pytest.mark.asyncio
async def test_strategy_returns_none_on_unparseable_response() -> None:
    llm = FakeLLMClient(["not even json"])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    signal = await strategy.evaluate(make_features(), {})

    assert signal is None


@pytest.mark.asyncio
async def test_strategy_rate_limits_within_window() -> None:
    llm = FakeLLMClient(
        [
            '{"action": "BUY", "confidence": 0.9, "reasoning": "first"}',
            '{"action": "SELL", "confidence": 0.9, "reasoning": "second"}',
        ]
    )
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}
    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)

    first = await strategy.evaluate(make_features(timestamp=t0), state)
    second = await strategy.evaluate(
        make_features(timestamp=t0 + timedelta(seconds=30)),
        state,
    )

    assert first is not None
    assert second is None
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_strategy_allows_after_window_elapsed() -> None:
    llm = FakeLLMClient(
        [
            '{"action": "BUY", "confidence": 0.9, "reasoning": "first"}',
            '{"action": "SELL", "confidence": 0.7, "reasoning": "second"}',
        ]
    )
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}
    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)

    first = await strategy.evaluate(make_features(timestamp=t0), state)
    second = await strategy.evaluate(
        make_features(timestamp=t0 + timedelta(seconds=120)),
        state,
    )

    assert first is not None
    assert second is not None
    assert second.action is Action.SELL
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_strategy_returns_none_when_reasoning_missing() -> None:
    llm = FakeLLMClient(['{"action": "BUY", "confidence": 0.6}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    signal = await strategy.evaluate(make_features(), {})

    assert signal is not None
    assert signal.reasoning is None
