from __future__ import annotations

import math

import polars as pl
import pytest
from feature_engine.indicators import sma


def test_sma_basic_window_3() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out = sma(df, window=3).get_column("sma_3").to_list()
    assert out[0] is None
    assert out[1] is None
    assert math.isclose(out[2], 2.0)
    assert math.isclose(out[3], 3.0)
    assert math.isclose(out[4], 4.0)


def test_sma_does_not_mutate_input() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0, 3.0]})
    sma(df, window=2)
    assert df.columns == ["close"]


def test_sma_groups_per_symbol() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["A", "A", "A", "B", "B", "B"],
            "close": [10.0, 20.0, 30.0, 100.0, 200.0, 300.0],
        }
    )
    out = sma(df, window=2).get_column("sma_2").to_list()
    # 各銘柄の先頭は window 不足で null、2 本目以降は銘柄内で計算される
    assert out[0] is None
    assert math.isclose(out[1], 15.0)
    assert math.isclose(out[2], 25.0)
    assert out[3] is None  # B 銘柄の先頭にリークしないこと
    assert math.isclose(out[4], 150.0)
    assert math.isclose(out[5], 250.0)


def test_sma_custom_output_col() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0, 3.0]})
    out = sma(df, window=2, output_col="ma_short")
    assert "ma_short" in out.columns
    assert "sma_2" not in out.columns


def test_sma_rejects_invalid_window() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError):
        sma(df, window=0)


def test_sma_rejects_missing_column() -> None:
    df = pl.DataFrame({"price": [1.0, 2.0]})
    with pytest.raises(ValueError):
        sma(df, window=2)
