from __future__ import annotations

import polars as pl

from ._common import maybe_group, require_columns, require_positive_int


def vwap(
    df: pl.DataFrame,
    window: int,
    *,
    price_col: str | None = None,
    volume_col: str = "volume",
    output_col: str | None = None,
) -> pl.DataFrame:
    """ローリング VWAP (出来高加重平均価格) を追加した新しい DataFrame を返す。

    - `price_col` 未指定時は `high / low / close` から typical price = (H+L+C)/3 を使う
    - `window` 本に満たない期間は null
    - 期間内の出来高合計が 0 の場合は null
    - `symbol` 列があれば銘柄ごとに独立して計算
    """
    require_positive_int("window", window)
    require_columns(df, volume_col)

    if price_col is None:
        require_columns(df, "high", "low", "close")
        price_expr = (
            pl.col("high").cast(pl.Float64)
            + pl.col("low").cast(pl.Float64)
            + pl.col("close").cast(pl.Float64)
        ) / 3.0
    else:
        require_columns(df, price_col)
        price_expr = pl.col(price_col).cast(pl.Float64)

    out = output_col or f"vwap_{window}"

    df1 = df.with_columns(
        [
            (price_expr * pl.col(volume_col).cast(pl.Float64)).alias("__pv"),
            pl.col(volume_col).cast(pl.Float64).alias("__v"),
        ]
    )

    pv_sum = pl.col("__pv").rolling_sum(window_size=window)
    v_sum = pl.col("__v").rolling_sum(window_size=window)
    df1 = df1.with_columns(
        [
            maybe_group(pv_sum, df1).alias("__pv_sum"),
            maybe_group(v_sum, df1).alias("__v_sum"),
        ]
    )

    vwap_expr = (
        pl.when(pl.col("__v_sum").is_null() | (pl.col("__v_sum") == 0))
        .then(None)
        .otherwise(pl.col("__pv_sum") / pl.col("__v_sum"))
    )

    return df1.with_columns(vwap_expr.alias(out)).drop(["__pv", "__v", "__pv_sum", "__v_sum"])
