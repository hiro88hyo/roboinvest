from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import polars as pl
import pytest
from feature_engine.backtest.runner import (
    compute_features,
    run_backtest,
    to_processed_features,
)
from feature_engine.config import FeatureEngineSettings


def _settings() -> FeatureEngineSettings:
    return FeatureEngineSettings(
        indicator_sma_short_window=3,
        indicator_sma_long_window=5,
        indicator_rsi_period=3,
        indicator_vwap_window=3,
        indicator_bollinger_period=3,
        indicator_bollinger_stddev=2.0,
        backtest_lookback_days=10,
    )


def _make_ohlcv(symbol: str, start: date, closes: list[float]) -> pl.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        rows.append(
            {
                "symbol": symbol,
                "date": d,
                "open": c,
                "high": c + 1.0,
                "low": c - 1.0,
                "close": c,
                "volume": 1000,
                "turnover": c * 1000,
            }
        )
    return pl.DataFrame(rows)


@dataclass
class FakeSource:
    watchlist_by_date: dict[date, list[str]] = field(default_factory=dict)
    ohlcv: pl.DataFrame = field(default_factory=lambda: pl.DataFrame())

    async def fetch_watchlist(self, valid_date: date) -> list[str]:
        return self.watchlist_by_date.get(valid_date, [])

    async def fetch_daily_ohlcv(
        self, symbols: list[str], *, start: date, end: date
    ) -> pl.DataFrame:
        if self.ohlcv.is_empty():
            return self.ohlcv
        return self.ohlcv.filter(
            pl.col("symbol").is_in(symbols) & (pl.col("date") >= start) & (pl.col("date") <= end)
        )


def test_compute_features_adds_all_indicator_columns() -> None:
    df = _make_ohlcv("7203", date(2026, 1, 5), [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0])
    out = compute_features(df, _settings())
    for col in (
        "sma_short",
        "sma_long",
        "rsi",
        "vwap",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
    ):
        assert col in out.columns
    # warmup は null、データが十分揃った末尾は非 null
    assert out.get_column("sma_short").to_list()[-1] is not None


def test_compute_features_empty_input_returns_empty() -> None:
    df = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "turnover": pl.Float64,
        }
    )
    out = compute_features(df, _settings())
    assert out.is_empty()


def test_to_processed_features_emits_decimal_and_null() -> None:
    df = _make_ohlcv("7203", date(2026, 1, 5), [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0])
    df = compute_features(df, _settings())
    features = to_processed_features(df)
    assert len(features) == 7
    first = features[0]
    assert first.symbol == "7203"
    assert isinstance(first.price, Decimal)
    # 先頭は warmup なので sma_short が null
    assert first.sma_short is None
    # 末尾は非 null のはず
    last = features[-1]
    assert last.sma_short is not None
    assert isinstance(last.sma_short, Decimal)
    # 大引け (15:00 JST) の timestamp が入る
    assert last.timestamp.hour == 15
    assert last.timestamp.tzinfo is not None


def test_to_processed_features_skips_null_close() -> None:
    df = _make_ohlcv("7203", date(2026, 1, 5), [100.0, 101.0, 102.0, 103.0, 104.0])
    df = compute_features(df, _settings())
    # close を 1 行 null にする
    df = df.with_columns(
        pl.when(pl.col("date") == date(2026, 1, 7))
        .then(None)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    features = to_processed_features(df)
    assert len(features) == 4


def test_to_processed_features_empty_df_returns_empty_list() -> None:
    assert to_processed_features(pl.DataFrame()) == []


def test_to_processed_features_rejects_missing_columns() -> None:
    df = pl.DataFrame({"symbol": ["7203"], "date": [date(2026, 1, 5)], "close": [100.0]})
    with pytest.raises(ValueError):
        to_processed_features(df)


async def test_run_backtest_resolves_watchlist_when_symbols_missing() -> None:
    end = date(2026, 1, 20)
    src = FakeSource(
        watchlist_by_date={end: ["7203"]},
        ohlcv=_make_ohlcv("7203", date(2026, 1, 10), [100.0, 101.0, 102.0, 103.0, 104.0]),
    )
    result = await run_backtest(src, _settings(), end_date=end)
    assert result.symbols == ["7203"]
    assert len(result.features) == 5
    assert all(f.symbol == "7203" for f in result.features)


async def test_run_backtest_uses_explicit_symbols() -> None:
    end = date(2026, 1, 20)
    ohlcv = pl.concat(
        [
            _make_ohlcv("7203", date(2026, 1, 10), [100.0, 101.0, 102.0]),
            _make_ohlcv("9984", date(2026, 1, 10), [200.0, 201.0, 202.0]),
        ]
    )
    src = FakeSource(watchlist_by_date={end: ["UNUSED"]}, ohlcv=ohlcv)
    result = await run_backtest(src, _settings(), end_date=end, symbols=["9984"])
    assert result.symbols == ["9984"]
    assert {f.symbol for f in result.features} == {"9984"}


async def test_run_backtest_empty_watchlist_returns_empty_result() -> None:
    src = FakeSource()
    result = await run_backtest(src, _settings(), end_date=date(2026, 1, 20))
    assert result.symbols == []
    assert result.features == []
