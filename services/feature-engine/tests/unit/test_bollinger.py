from __future__ import annotations

import math

import polars as pl
import pytest
from feature_engine.indicators import bollinger, sma


def test_bollinger_middle_matches_sma() -> None:
    df = pl.DataFrame({"close": [float(i) for i in range(1, 25)]})
    bb = bollinger(df, window=5)
    expected = sma(df, window=5, output_col="expected").get_column("expected").to_list()
    actual = bb.get_column("bollinger_middle").to_list()
    for a, e in zip(actual, expected, strict=True):
        if e is None:
            assert a is None
        else:
            assert a is not None
            assert math.isclose(a, e)


def test_bollinger_warmup_is_null() -> None:
    df = pl.DataFrame({"close": [10.0, 11.0, 12.0]})
    bb = bollinger(df, window=5)
    for col in ("bollinger_upper", "bollinger_middle", "bollinger_lower"):
        assert bb.get_column(col).to_list() == [None, None, None]


def test_bollinger_symmetric_around_middle() -> None:
    df = pl.DataFrame({"close": [10.0, 12.0, 14.0, 13.0, 15.0, 14.0, 16.0]})
    bb = bollinger(df, window=3, num_std=2.0)
    mid = bb.get_column("bollinger_middle").to_list()
    upper = bb.get_column("bollinger_upper").to_list()
    lower = bb.get_column("bollinger_lower").to_list()
    for m, u, lo in zip(mid, upper, lower, strict=True):
        if m is None:
            assert u is None and lo is None
        else:
            assert u is not None and lo is not None
            assert math.isclose(u - m, m - lo, rel_tol=1e-9)


def test_bollinger_flat_series_has_zero_width() -> None:
    df = pl.DataFrame({"close": [100.0] * 10})
    bb = bollinger(df, window=5)
    mid = bb.get_column("bollinger_middle").drop_nulls().to_list()
    upper = bb.get_column("bollinger_upper").drop_nulls().to_list()
    lower = bb.get_column("bollinger_lower").drop_nulls().to_list()
    assert all(math.isclose(m, 100.0) for m in mid)
    assert all(math.isclose(u, 100.0) for u in upper)
    assert all(math.isclose(lo, 100.0) for lo in lower)


def test_bollinger_groups_per_symbol() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["A"] * 5 + ["B"] * 5,
            "close": [10.0, 11.0, 12.0, 13.0, 14.0, 100.0, 110.0, 120.0, 130.0, 140.0],
        }
    )
    bb = bollinger(df, window=3)
    a_mid = bb.filter(pl.col("symbol") == "A").get_column("bollinger_middle").to_list()
    b_mid = bb.filter(pl.col("symbol") == "B").get_column("bollinger_middle").to_list()
    assert a_mid[0] is None and a_mid[1] is None
    assert math.isclose(a_mid[2], 11.0)
    assert b_mid[0] is None and b_mid[1] is None
    assert math.isclose(b_mid[2], 110.0)


def test_bollinger_rejects_negative_std() -> None:
    df = pl.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError):
        bollinger(df, window=2, num_std=-1.0)
