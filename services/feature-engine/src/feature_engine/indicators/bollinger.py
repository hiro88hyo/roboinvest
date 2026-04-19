from __future__ import annotations

import polars as pl

from ._common import maybe_group, require_columns, require_positive_int


def bollinger(
    df: pl.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
    *,
    price_col: str = "close",
    prefix: str = "bollinger",
) -> pl.DataFrame:
    """ボリンジャーバンド (middle / upper / lower) を追加した新しい DataFrame を返す。

    - middle = SMA(window), std は母集団標準偏差 (ddof=0)
    - upper = middle + num_std * std, lower = middle - num_std * std
    - `window` 本に満たない期間は全て null
    """
    require_positive_int("window", window)
    if num_std <= 0:
        raise ValueError(f"num_std must be positive, got {num_std}")
    require_columns(df, price_col)

    middle_col = f"{prefix}_middle"

    middle_expr = pl.col(price_col).cast(pl.Float64).rolling_mean(window_size=window)
    std_expr = pl.col(price_col).cast(pl.Float64).rolling_std(window_size=window, ddof=0)

    df1 = df.with_columns(
        [
            maybe_group(middle_expr, df).alias(middle_col),
            maybe_group(std_expr, df).alias("__bb_std"),
        ]
    )

    return df1.with_columns(
        [
            (pl.col(middle_col) + num_std * pl.col("__bb_std")).alias(f"{prefix}_upper"),
            (pl.col(middle_col) - num_std * pl.col("__bb_std")).alias(f"{prefix}_lower"),
        ]
    ).drop("__bb_std")
