from __future__ import annotations

import polars as pl

from ._common import maybe_group, require_columns, require_positive_int


def rsi(
    df: pl.DataFrame,
    period: int = 14,
    *,
    price_col: str = "close",
    output_col: str | None = None,
) -> pl.DataFrame:
    """Wilder 型 RSI (EWM 形式) を追加した新しい DataFrame を返す。

    - 平滑化は `ewm_mean(alpha=1/period, adjust=False)` に従う
    - ウォームアップ (先頭 `period` 行) は null
    - 値動きがなくなる (avg_gain == avg_loss == 0) 場合は null を返す
    - 下げのみ (avg_loss == 0 and avg_gain > 0) の場合は 100
    """
    require_positive_int("period", period)
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")
    require_columns(df, price_col)

    out = output_col or f"rsi_{period}"
    alpha = 1.0 / period

    diff_expr = pl.col(price_col).cast(pl.Float64).diff()
    df1 = df.with_columns(maybe_group(diff_expr, df).alias("__diff"))
    df1 = df1.with_columns(
        [
            pl.when(pl.col("__diff").is_null())
            .then(None)
            .when(pl.col("__diff") > 0)
            .then(pl.col("__diff"))
            .otherwise(0.0)
            .alias("__gain"),
            pl.when(pl.col("__diff").is_null())
            .then(None)
            .when(pl.col("__diff") < 0)
            .then(-pl.col("__diff"))
            .otherwise(0.0)
            .alias("__loss"),
        ]
    )

    avg_gain_expr = pl.col("__gain").ewm_mean(
        alpha=alpha, adjust=False, min_samples=period, ignore_nulls=True
    )
    avg_loss_expr = pl.col("__loss").ewm_mean(
        alpha=alpha, adjust=False, min_samples=period, ignore_nulls=True
    )
    df1 = df1.with_columns(
        [
            maybe_group(avg_gain_expr, df1).alias("__ag"),
            maybe_group(avg_loss_expr, df1).alias("__al"),
        ]
    )

    rsi_expr = (
        pl.when(pl.col("__ag").is_null() | pl.col("__al").is_null())
        .then(None)
        .when((pl.col("__ag") == 0) & (pl.col("__al") == 0))
        .then(None)
        .when(pl.col("__al") == 0)
        .then(pl.lit(100.0))
        .otherwise(100.0 - 100.0 / (1.0 + pl.col("__ag") / pl.col("__al")))
    )

    return df1.with_columns(rsi_expr.alias(out)).drop(
        ["__diff", "__gain", "__loss", "__ag", "__al"]
    )
