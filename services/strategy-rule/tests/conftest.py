"""Shared pytest fixtures for strategy-rule tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from trade_contracts.features import ProcessedFeatures

FeaturesFactory = Callable[..., ProcessedFeatures]


def _make_features(
    *,
    symbol: str = "7203",
    timestamp: datetime | None = None,
    price: Decimal = Decimal("1000"),
    sma_short: Decimal | None = None,
    sma_long: Decimal | None = None,
    rsi: Decimal | None = None,
    vwap: Decimal | None = None,
    bollinger_upper: Decimal | None = None,
    bollinger_middle: Decimal | None = None,
    bollinger_lower: Decimal | None = None,
) -> ProcessedFeatures:
    return ProcessedFeatures(
        symbol=symbol,
        timestamp=timestamp or datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
        price=price,
        sma_short=sma_short,
        sma_long=sma_long,
        rsi=rsi,
        vwap=vwap,
        bollinger_upper=bollinger_upper,
        bollinger_middle=bollinger_middle,
        bollinger_lower=bollinger_lower,
    )


@pytest.fixture
def features_factory() -> FeaturesFactory:
    return _make_features
