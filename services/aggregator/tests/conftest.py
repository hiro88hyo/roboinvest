"""Shared pytest fixtures for aggregator tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal

SignalFactory = Callable[..., StrategySignal]


def _make_signal(
    *,
    source: SignalSource = SignalSource.RULE,
    symbol: str = "7203",
    action: Action = Action.BUY,
    confidence: float = 0.7,
    reasoning: str | None = None,
    created_at: datetime | None = None,
) -> StrategySignal:
    return StrategySignal(
        signal_id=uuid4(),
        source=source,
        symbol=symbol,
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        created_at=created_at or datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def signal_factory() -> SignalFactory:
    return _make_signal
