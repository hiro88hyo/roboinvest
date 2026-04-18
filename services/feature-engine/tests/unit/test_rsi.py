from __future__ import annotations

import polars as pl
import pytest
from feature_engine.indicators import rsi


def test_rsi_warmup_is_null() -> None:
    df = pl.DataFrame({"close": [float(i) for i in range(1, 10)]})
    out = rsi(df, period=5).get_column("rsi_5").to_list()
    # 先頭 period 行は null (diff の null + ewm_mean の min_periods)
    assert out[:5] == [None, None, None, None, None]
    assert out[5] is not None


def test_rsi_monotonic_up_is_100() -> None:
    df = pl.DataFrame({"close": [float(i) for i in range(1, 30)]})
    out = rsi(df, period=14).get_column("rsi_14").drop_nulls().to_list()
    assert all(abs(v - 100.0) < 1e-9 for v in out)


def test_rsi_monotonic_down_is_0() -> None:
    df = pl.DataFrame({"close": [float(30 - i) for i in range(0, 29)]})
    out = rsi(df, period=14).get_column("rsi_14").drop_nulls().to_list()
    assert all(abs(v - 0.0) < 1e-9 for v in out)


def test_rsi_flat_series_is_null() -> None:
    df = pl.DataFrame({"close": [100.0] * 20})
    # 値動きなし (avg_gain == avg_loss == 0) は未定義として null
    out = rsi(df, period=14).get_column("rsi_14").drop_nulls().to_list()
    assert out == []


def test_rsi_groups_per_symbol() -> None:
    # A は上昇のみ、B は下降のみ
    df = pl.DataFrame(
        {
            "symbol": ["A"] * 20 + ["B"] * 20,
            "close": [float(i) for i in range(1, 21)] + [float(20 - i) for i in range(0, 20)],
        }
    )
    out = rsi(df, period=5)
    a_vals = out.filter(pl.col("symbol") == "A").get_column("rsi_5").drop_nulls().to_list()
    b_vals = out.filter(pl.col("symbol") == "B").get_column("rsi_5").drop_nulls().to_list()
    assert all(abs(v - 100.0) < 1e-9 for v in a_vals)
    assert all(abs(v - 0.0) < 1e-9 for v in b_vals)


def test_rsi_rejects_period_lt_2() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError):
        rsi(df, period=1)
