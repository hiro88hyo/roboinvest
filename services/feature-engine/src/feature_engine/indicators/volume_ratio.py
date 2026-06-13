from __future__ import annotations

import polars as pl

from ._common import maybe_group, require_columns, require_positive_int


def volume_ratio(
    df: pl.DataFrame,
    window: int,
    *,
    volume_col: str = "volume",
    output_col: str | None = None,
) -> pl.DataFrame:
    """Current volume divided by rolling average volume.

    The current row is included in the rolling average, matching the
    daily-OHLCV parameter sweep used for strategy validation.
    """
    require_positive_int("window", window)
    require_columns(df, volume_col)

    out = output_col or f"volume_ratio_{window}"
    volume = pl.col(volume_col).cast(pl.Float64)
    avg = maybe_group(volume.rolling_mean(window_size=window), df)
    ratio = pl.when(avg.is_null() | (avg <= 0)).then(None).otherwise(volume / avg)
    return df.with_columns(ratio.alias(out))
