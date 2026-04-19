from __future__ import annotations

import math

import polars as pl
import pytest
from feature_engine.indicators import vwap


def test_vwap_with_price_col() -> None:
    df = pl.DataFrame(
        {
            "price": [100.0, 110.0, 120.0],
            "volume": [10.0, 20.0, 30.0],
        }
    )
    out = vwap(df, window=2, price_col="price").get_column("vwap_2").to_list()
    assert out[0] is None
    # (100*10 + 110*20) / (10+20) = 3200/30
    assert math.isclose(out[1], 3200.0 / 30.0)
    # (110*20 + 120*30) / (20+30) = 5800/50 = 116.0
    assert math.isclose(out[2], 116.0)


def test_vwap_uses_typical_price_when_hlc_present() -> None:
    df = pl.DataFrame(
        {
            "high": [12.0, 14.0],
            "low": [10.0, 11.0],
            "close": [11.0, 13.0],
            "volume": [100.0, 200.0],
        }
    )
    out = vwap(df, window=2).get_column("vwap_2").to_list()
    typical0 = (12 + 10 + 11) / 3
    typical1 = (14 + 11 + 13) / 3
    expected = (typical0 * 100 + typical1 * 200) / 300
    assert out[0] is None
    assert math.isclose(out[1], expected)


def test_vwap_zero_volume_window_is_null() -> None:
    df = pl.DataFrame(
        {
            "price": [100.0, 101.0, 102.0],
            "volume": [0.0, 0.0, 0.0],
        }
    )
    out = vwap(df, window=2, price_col="price").get_column("vwap_2").to_list()
    assert out == [None, None, None]


def test_vwap_groups_per_symbol() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["A", "A", "B", "B"],
            "price": [100.0, 110.0, 200.0, 220.0],
            "volume": [10.0, 10.0, 5.0, 5.0],
        }
    )
    out = vwap(df, window=2, price_col="price").get_column("vwap_2").to_list()
    assert out[0] is None
    assert math.isclose(out[1], (100 * 10 + 110 * 10) / 20)
    assert out[2] is None  # B 銘柄の先頭にリークしない
    assert math.isclose(out[3], (200 * 5 + 220 * 5) / 10)


def test_vwap_requires_hlc_when_price_col_not_given() -> None:
    df = pl.DataFrame({"price": [1.0], "volume": [1.0]})
    with pytest.raises(ValueError):
        vwap(df, window=1)
