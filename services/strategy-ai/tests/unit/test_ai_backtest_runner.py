from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from strategy_ai._testing import FakeLLMClient, make_features
from strategy_ai.backtest.runner import run_backtest
from strategy_ai.engine import StrategyAiEngine
from strategy_ai.llm.fixture import FixtureLLMClient
from strategy_ai.strategy import AiStrategy
from trade_contracts.enums import Action, SignalSource


@pytest.mark.asyncio
async def test_run_backtest_collects_signals_in_order() -> None:
    llm = FakeLLMClient(
        [
            json.dumps({"action": "BUY", "confidence": 0.8, "reasoning": "1"}),
            json.dumps({"action": "SELL", "confidence": 0.9, "reasoning": "2"}),
        ]
    )
    engine = StrategyAiEngine([AiStrategy(llm=llm, min_interval_seconds=0)])
    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    features = [
        make_features(symbol="7203", timestamp=t0),
        make_features(symbol="7203", timestamp=t0 + timedelta(seconds=1)),
    ]

    summary = await run_backtest(engine, iter(features))

    assert summary.feature_count == 2
    assert summary.signal_count == 2
    assert [s.action for s in summary.signals] == [Action.BUY, Action.SELL]
    assert all(s.source is SignalSource.AI for s in summary.signals)


@pytest.mark.asyncio
async def test_run_backtest_with_fixture_is_deterministic() -> None:
    """同じ FixtureLLMClient は同じレスポンスを循環するので結果が一致する。"""

    def make_engine() -> StrategyAiEngine:
        return StrategyAiEngine([AiStrategy(llm=FixtureLLMClient(), min_interval_seconds=0)])

    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    features = [make_features(symbol="7203", timestamp=t0 + timedelta(seconds=i)) for i in range(6)]

    a = await run_backtest(make_engine(), iter(features))
    b = await run_backtest(make_engine(), iter(features))

    assert [s.action for s in a.signals] == [s.action for s in b.signals]
    # default fixture rotates BUY / HOLD / SELL — HOLD 抑止後に BUY,SELL が交互に出る
    assert [s.action for s in a.signals] == [Action.BUY, Action.SELL] * 2


@pytest.mark.asyncio
async def test_run_backtest_respects_rate_limit() -> None:
    llm = FixtureLLMClient(
        responses=(json.dumps({"action": "BUY", "confidence": 0.5, "reasoning": "x"}),)
    )
    engine = StrategyAiEngine([AiStrategy(llm=llm, min_interval_seconds=60)])
    t0 = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    features = [
        make_features(symbol="7203", timestamp=t0),
        make_features(symbol="7203", timestamp=t0 + timedelta(seconds=10)),
        make_features(symbol="7203", timestamp=t0 + timedelta(seconds=120)),
    ]

    summary = await run_backtest(engine, iter(features))

    assert summary.feature_count == 3
    assert summary.signal_count == 2
