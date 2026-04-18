from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class DynamicScoringConfig:
    top_n: int
    weight_volatility: float = 1.0
    weight_volume_surge: float = 1.0
    weight_momentum: float = 1.0
    volatility_window: int = 20
    momentum_window: int = 20
    volume_surge_short: int = 5
    volume_surge_long: int = 20


def _zscore(expr: pl.Expr) -> pl.Expr:
    """分母が 0 になるケースをガードした標準化。"""
    mean = expr.mean()
    std = expr.std()
    return pl.when(std == 0).then(0.0).otherwise((expr - mean) / std).fill_null(0.0)


def score_candidates(
    *,
    candidates: pl.DataFrame,
    ohlcv: pl.DataFrame,
    config: DynamicScoringConfig,
) -> pl.DataFrame:
    """第2段階の動的スコアリング。

    入力:
      - candidates: 静的フィルタ通過後の銘柄集合 (symbol, symbol_name, ...)
      - ohlcv: 直近期間の日次 OHLCV (symbol, date, close, volume, ...)
    出力カラム:
      - symbol, symbol_name, score, selected_reasons (struct),
        volatility, volume_surge, momentum
    """
    if candidates.is_empty():
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "symbol_name": pl.Utf8,
                "score": pl.Float64,
                "volatility": pl.Float64,
                "volume_surge": pl.Float64,
                "momentum": pl.Float64,
            }
        )

    target_symbols = candidates.get_column("symbol").to_list()
    data = ohlcv.filter(pl.col("symbol").is_in(target_symbols)).sort(["symbol", "date"])

    # 銘柄ごとにメトリクス算出。window は末尾からの固定本数で見る。
    with_returns = data.with_columns(
        [
            (pl.col("close").pct_change().over("symbol")).alias("ret"),
        ]
    )

    vol_window = config.volatility_window
    mom_window = config.momentum_window
    short = config.volume_surge_short
    long_ = config.volume_surge_long

    agg = (
        with_returns.group_by("symbol")
        .agg(
            [
                pl.col("ret").tail(vol_window).std().alias("volatility"),
                (
                    pl.col("close").tail(1).last() / pl.col("close").tail(mom_window).first() - 1
                ).alias("momentum"),
                (
                    pl.col("volume").tail(short).mean()
                    / pl.col("volume").tail(long_).mean().replace(0, None)
                ).alias("volume_surge"),
            ]
        )
        .with_columns(
            [
                pl.col("volatility").fill_null(0.0),
                pl.col("momentum").fill_null(0.0),
                pl.col("volume_surge").fill_null(1.0),
            ]
        )
    )

    scored = agg.with_columns(
        [
            _zscore(pl.col("volatility")).alias("z_vol"),
            _zscore(pl.col("volume_surge")).alias("z_surge"),
            _zscore(pl.col("momentum")).alias("z_mom"),
        ]
    ).with_columns(
        (
            pl.col("z_vol") * config.weight_volatility
            + pl.col("z_surge") * config.weight_volume_surge
            + pl.col("z_mom") * config.weight_momentum
        ).alias("score")
    )

    result = (
        candidates.select(["symbol", "symbol_name"])
        .join(scored, on="symbol", how="inner")
        .sort("score", descending=True)
        .head(config.top_n)
    )
    return result.select(
        [
            "symbol",
            "symbol_name",
            "score",
            "volatility",
            "volume_surge",
            "momentum",
        ]
    )


def to_watchlist_rows(scored: pl.DataFrame, *, valid_date_iso: str) -> list[dict[str, object]]:
    """スコア済み DataFrame を `watchlist` テーブルの行形式に変換する。"""
    rows: list[dict[str, object]] = []
    for row in scored.to_dicts():
        reasons = {
            "volatility": row["volatility"],
            "volume_surge": row["volume_surge"],
            "momentum": row["momentum"],
        }
        rows.append(
            {
                "symbol": row["symbol"],
                "valid_date": valid_date_iso,
                "symbol_name": row["symbol_name"],
                "score": row["score"],
                "selected_reasons": reasons,
            }
        )
    return rows
