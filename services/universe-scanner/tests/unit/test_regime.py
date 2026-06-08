from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest
from universe_scanner.regime import MarketRegime, RegimeScoringConfig, score_market_regime


def _series(
    symbol: str,
    *,
    start_close: float = 100.0,
    daily_step: float = 0.0,
    latest_multiplier: float = 1.0,
    volume_multiplier: float = 1.0,
    days: int = 30,
) -> list[dict[str, Any]]:
    start = date(2026, 4, 20) - timedelta(days=days - 1)
    rows = []
    for i in range(days):
        close = start_close + daily_step * i
        volume = 1000.0
        if i == days - 1:
            close *= latest_multiplier
            volume *= volume_multiplier
        rows.append(
            {
                "symbol": symbol,
                "date": start + timedelta(days=i),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": volume,
                "turnover": close * volume,
            }
        )
    return rows


def test_score_market_regime_normal_when_breadth_is_healthy() -> None:
    rows = []
    for i in range(30):
        rows.extend(_series(f"S{i:02d}", start_close=100 + i, latest_multiplier=1.01))

    decision = score_market_regime(ohlcv=pl.DataFrame(rows))

    assert decision.market_regime is MarketRegime.NORMAL
    assert decision.buy_enabled is True
    assert decision.position_size_multiplier == 1.0
    assert decision.metrics.usable_symbol_count == 30
    assert decision.metrics.down_ratio == 0.0


def test_score_market_regime_risk_off_for_broad_weakness() -> None:
    rows = []
    for i in range(24):
        rows.extend(_series(f"D{i:02d}", latest_multiplier=0.96))
    for i in range(6):
        rows.extend(_series(f"U{i:02d}", latest_multiplier=1.01))

    decision = score_market_regime(ohlcv=pl.DataFrame(rows))

    assert decision.market_regime is MarketRegime.RISK_OFF
    assert decision.buy_enabled is False
    assert decision.position_size_multiplier == 0.0
    assert decision.metrics.down_ratio == 0.8
    assert decision.metrics.big_down_ratio == 0.8
    assert "down_ratio=0.800" in decision.rationale


def test_score_market_regime_crash_for_extreme_average_drop() -> None:
    rows = []
    for i in range(30):
        rows.extend(_series(f"C{i:02d}", latest_multiplier=0.94))

    decision = score_market_regime(ohlcv=pl.DataFrame(rows))

    assert decision.market_regime is MarketRegime.CRASH
    assert decision.buy_enabled is False
    assert decision.confidence >= 0.5


def test_score_market_regime_caution_when_data_is_missing() -> None:
    rows = []
    for i in range(10):
        rows.extend(_series(f"S{i:02d}"))
    symbols = [f"S{i:02d}" for i in range(30)]

    decision = score_market_regime(ohlcv=pl.DataFrame(rows), symbols=symbols)

    assert decision.market_regime is MarketRegime.CAUTION
    assert decision.buy_enabled is True
    assert decision.position_size_multiplier == 0.5
    assert decision.metrics.symbol_count == 30
    assert decision.metrics.usable_symbol_count == 10
    assert decision.metrics.missing_ratio == pytest.approx(2 / 3)


def test_score_market_regime_uses_supplied_symbol_subset() -> None:
    rows = []
    for i in range(10):
        rows.extend(_series(f"UP{i:02d}", latest_multiplier=1.02))
    for i in range(10):
        rows.extend(_series(f"DN{i:02d}", latest_multiplier=0.95))

    decision = score_market_regime(
        ohlcv=pl.DataFrame(rows),
        symbols=[f"UP{i:02d}" for i in range(10)],
        config=RegimeScoringConfig(min_usable_symbols=5),
    )

    assert decision.market_regime is MarketRegime.NORMAL
    assert decision.metrics.symbol_count == 10
    assert decision.metrics.down_ratio == 0.0
