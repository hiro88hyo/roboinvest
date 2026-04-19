from __future__ import annotations

import polars as pl

from ._common import maybe_group, require_columns, require_positive_int


def sma(
    df: pl.DataFrame,
    window: int,
    *,
    price_col: str = "close",
    output_col: str | None = None,
) -> pl.DataFrame:
    """単純移動平均 (SMA) を追加した新しい DataFrame を返す。

    - `window` 本に満たない期間は null
    - `symbol` 列があれば銘柄ごとに独立して計算
    - 入力は時系列昇順にソート済みであること
    """
    require_positive_int("window", window)
    require_columns(df, price_col)

    out = output_col or f"sma_{window}"
    expr = pl.col(price_col).cast(pl.Float64).rolling_mean(window_size=window)
    return df.with_columns(maybe_group(expr, df).alias(out))
