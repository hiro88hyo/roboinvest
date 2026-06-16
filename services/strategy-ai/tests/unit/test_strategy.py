from __future__ import annotations

import logging
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
    assert strategy.stats.llm_calls == 1
    assert strategy.stats.llm_successes == 1
    assert strategy.stats.signals_emitted == 1


@pytest.mark.asyncio
async def test_strategy_signal_carries_execution_context() -> None:
    llm = FakeLLMClient(['{"action": "BUY", "confidence": 0.8, "reasoning": "ok"}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    features = make_features(
        best_bid=Decimal("999"),
        best_ask=Decimal("1001"),
        spread_bps=Decimal("20"),
        tick_size=Decimal("1"),
        spread_ticks=Decimal("2"),
        bid_depth_5=1200,
        ask_depth_5=900,
        book_imbalance_5=Decimal("0.142857"),
        minutes_from_open=20,
        minutes_to_close=370,
        session_phase="morning",
    )

    signal = await strategy.evaluate(features, {})

    assert signal is not None
    assert signal.best_bid == Decimal("999")
    assert signal.best_ask == Decimal("1001")
    assert signal.spread_bps == Decimal("20")
    assert signal.tick_size == Decimal("1")
    assert signal.spread_ticks == Decimal("2")
    assert signal.bid_depth_5 == 1200
    assert signal.ask_depth_5 == 900
    assert signal.book_imbalance_5 == Decimal("0.142857")
    assert signal.minutes_from_open == 20
    assert signal.minutes_to_close == 370
    assert signal.session_phase == "morning"


@pytest.mark.asyncio
async def test_strategy_skips_hold(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLMClient(['{"action": "HOLD", "confidence": 0.5, "reasoning": "flat"}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}

    caplog.set_level(logging.INFO, logger="strategy_ai.strategy")

    signal = await strategy.evaluate(make_features(), state)

    assert signal is None
    assert state["last_call_at"] is not None  # rate limit clock starts even on HOLD
    assert strategy.stats.llm_calls == 1
    assert strategy.stats.llm_successes == 1
    assert strategy.stats.hold_decisions == 1
    skipped = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_decision_skipped"
    ]
    assert len(skipped) == 1
    record: Any = skipped[0]
    assert record.reason == "hold"
    assert record.action == "HOLD"
    assert record.confidence == 0.5


@pytest.mark.asyncio
async def test_strategy_swallows_llm_error(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLMClient([LLMError("boom")])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    caplog.set_level(logging.ERROR, logger="strategy_ai.strategy")

    signal = await strategy.evaluate(make_features(), {})

    assert signal is None
    assert strategy.stats.llm_calls == 1
    assert strategy.stats.llm_errors == 1
    errors = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "external_api_error"
    ]
    assert len(errors) == 1
    record: Any = errors[0]
    assert record.api_name == "llm"
    assert record.endpoint == "complete"
    assert record.reason == "llm_error"


@pytest.mark.asyncio
async def test_strategy_returns_none_on_unparseable_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = FakeLLMClient(["not even json"])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    caplog.set_level(logging.WARNING, logger="strategy_ai.strategy")

    signal = await strategy.evaluate(make_features(), {})

    assert signal is None
    assert strategy.stats.llm_calls == 1
    assert strategy.stats.llm_successes == 1
    assert strategy.stats.parse_failures == 1
    skipped = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_decision_skipped"
    ]
    assert len(skipped) == 1
    record: Any = skipped[0]
    assert record.reason == "parse_failed"


@pytest.mark.asyncio
async def test_strategy_rate_limits_within_window(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLMClient(
        [
            '{"action": "BUY", "confidence": 0.9, "reasoning": "first"}',
            '{"action": "SELL", "confidence": 0.9, "reasoning": "second"}',
        ]
    )
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)
    state: dict[str, Any] = {}
    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)

    caplog.set_level(logging.DEBUG, logger="strategy_ai.strategy")

    first = await strategy.evaluate(make_features(timestamp=t0), state)
    second = await strategy.evaluate(
        make_features(timestamp=t0 + timedelta(seconds=30)),
        state,
    )

    assert first is not None
    assert second is None
    assert len(llm.calls) == 1
    assert strategy.stats.llm_calls == 1
    skipped = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_decision_skipped"
    ]
    assert len(skipped) == 1
    record: Any = skipped[0]
    assert record.reason == "rate_limited"
    assert record.min_interval_seconds == 60


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


@pytest.mark.asyncio
async def test_strategy_skips_non_positive_confidence_decision() -> None:
    llm = FakeLLMClient(['{"action": "BUY", "confidence": 0.0}'])
    strategy = AiStrategy(llm=llm, min_interval_seconds=60)

    signal = await strategy.evaluate(make_features(), {})

    assert signal is None
    assert strategy.stats.confidence_rejects == 1
