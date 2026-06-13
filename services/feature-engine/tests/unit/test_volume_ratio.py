from __future__ import annotations

import polars as pl
from feature_engine.indicators import volume_ratio


def test_volume_ratio_current_volume_over_rolling_average() -> None:
    df = pl.DataFrame({"symbol": ["7203"] * 3, "volume": [100, 100, 400]})
    out = volume_ratio(df, window=3, output_col="volume_ratio")
    assert out.get_column("volume_ratio").to_list() == [None, None, 2.0]


def test_volume_ratio_groups_per_symbol() -> None:
    df = pl.DataFrame(
        {
            "symbol": ["A", "A", "B", "B"],
            "volume": [100, 300, 10, 10],
        }
    )
    out = volume_ratio(df, window=2, output_col="volume_ratio")
    assert out.filter(pl.col("symbol") == "A").get_column("volume_ratio").to_list() == [
        None,
        1.5,
    ]
    assert out.filter(pl.col("symbol") == "B").get_column("volume_ratio").to_list() == [
        None,
        1.0,
    ]
