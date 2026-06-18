from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import polars as pl


@dataclass(frozen=True, slots=True)
class StaticFilterConfig:
    min_turnover_jpy: Decimal
    price_min: Decimal
    price_max: Decimal
    allowed_segments: tuple[str, ...]
    min_lot_size: int = 100
    max_min_lot_notional_jpy: Decimal | None = None
    turnover_lookback_days: int = 20


def apply_static_filter(
    *,
    master: pl.DataFrame,
    ohlcv: pl.DataFrame,
    config: StaticFilterConfig,
) -> pl.DataFrame:
    """第1段階の静的フィルタ。

    入力:
      - master: columns [symbol, symbol_name, market_segment, sector, is_active]
      - ohlcv: columns [symbol, date, open, high, low, close, volume, turnover]
    出力: master と同スキーマ (条件を満たす銘柄のみ)
    """
    if master.is_empty() or ohlcv.is_empty():
        return master.clear()

    # 直近 N 営業日の平均売買代金と最新終値を銘柄ごとに集計。
    latest_date = ohlcv.select(pl.col("date").max()).item()
    window = ohlcv.sort("date").group_by("symbol").tail(config.turnover_lookback_days)

    agg = window.group_by("symbol").agg(
        [
            pl.col("turnover").mean().alias("avg_turnover"),
            pl.col("close").last().alias("latest_close"),
            pl.col("date").max().alias("latest_date"),
        ]
    )

    min_turnover = float(config.min_turnover_jpy)
    price_min = float(config.price_min)
    price_max = float(config.price_max)
    min_lot_size = float(config.min_lot_size)
    max_min_lot_notional = (
        None if config.max_min_lot_notional_jpy is None else float(config.max_min_lot_notional_jpy)
    )

    joined = master.join(agg, on="symbol", how="inner")
    filtered = joined.filter(
        pl.col("is_active")
        & pl.col("market_segment").is_in(list(config.allowed_segments))
        & (pl.col("avg_turnover") >= min_turnover)
        & (pl.col("latest_close") >= price_min)
        & (pl.col("latest_close") <= price_max)
        & (
            pl.lit(True)
            if max_min_lot_notional is None
            else (pl.col("latest_close") * min_lot_size <= max_min_lot_notional)
        )
        & (pl.col("latest_date") == latest_date)
    )
    return filtered.select(master.columns)
