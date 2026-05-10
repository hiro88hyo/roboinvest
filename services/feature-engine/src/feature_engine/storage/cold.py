from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

logger = logging.getLogger(__name__)

ColdResolution = Literal["1m", "5m"]

_RESOLUTION_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
}


def aggregate_to_ohlcv(df: pl.DataFrame, resolution: ColdResolution) -> pl.DataFrame:
    """Warm レイヤの DataFrame を Cold 用の OHLCV バー (1m / 5m) に集約する。

    入力は以下の 2 形態を許容する:
    - raw tick: `symbol, timestamp, price, volume`
    - OHLCV バー (warm の 1s / 1m / 5m 出力): `symbol, timestamp, open, high, low, close, volume`

    いずれも `timestamp` は tz-aware (UTC) を前提とする。
    """
    if df.is_empty():
        return df
    interval = _RESOLUTION_INTERVAL[resolution]
    if "open" in df.columns:
        return (
            df.sort("timestamp")
            .group_by_dynamic("timestamp", every=interval, group_by="symbol")
            .agg(
                pl.col("open").first().alias("open"),
                pl.col("high").max().alias("high"),
                pl.col("low").min().alias("low"),
                pl.col("close").last().alias("close"),
                pl.col("volume").sum().alias("volume"),
            )
        )
    return (
        df.sort("timestamp")
        .group_by_dynamic("timestamp", every=interval, group_by="symbol")
        .agg(
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )
    )


def load_warm_partition(warm_dir: Path, symbol: str, d: date) -> pl.DataFrame:
    """Warm パーティション配下の Parquet を全て読んで 1 枚にまとめる。"""
    part_dir = warm_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
    if not part_dir.exists():
        return pl.DataFrame()
    files = sorted(part_dir.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def write_cold_partition(
    df: pl.DataFrame,
    cold_dir: Path,
    symbol: str,
    d: date,
    resolution: ColdResolution,
) -> Path:
    """集約済み OHLCV を `cold_dir/symbol=<S>/date=<D>/<resolution>_<start>_<end>.parquet`
    に書き出す。"""
    if df.is_empty():
        raise ValueError("cannot write empty dataframe to cold partition")
    part_dir = cold_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    sorted_df = df.sort("timestamp")
    first_ms = int(sorted_df.item(0, "timestamp").timestamp() * 1000)
    last_ms = int(sorted_df.item(-1, "timestamp").timestamp() * 1000)
    path = part_dir / f"{resolution}_{first_ms}_{last_ms}.parquet"
    sorted_df.write_parquet(path)
    logger.info("cold parquet written: path=%s rows=%d", path, sorted_df.height)
    return path


def migrate_warm_to_cold(
    warm_dir: Path,
    cold_dir: Path,
    symbol: str,
    d: date,
    resolution: ColdResolution,
) -> Path | None:
    """Warm の (symbol, date) 区画を読み込み、Cold 1m / 5m OHLCV にして書き出す。

    Warm パーティションが空なら何もせず None を返す。
    `scripts/warm-to-cold-migration.py` から日次で呼ばれることを想定。
    """
    warm = load_warm_partition(warm_dir, symbol, d)
    if warm.is_empty():
        logger.info(
            "warm partition empty: symbol=%s date=%s",
            symbol,
            d.isoformat(),
        )
        return None
    aggregated = aggregate_to_ohlcv(warm, resolution)
    return write_cold_partition(aggregated, cold_dir, symbol, d, resolution)


def enumerate_warm_symbols(warm_dir: Path, d: date) -> list[str]:
    """``warm_dir/symbol=<S>/date=<D>/*.parquet`` を持つ symbol 一覧を返す (sorted)。"""
    if not warm_dir.exists():
        return []
    target_date = d.isoformat()
    symbols: list[str] = []
    for sym_dir in warm_dir.glob("symbol=*"):
        if not sym_dir.is_dir():
            continue
        date_dir = sym_dir / f"date={target_date}"
        if not date_dir.is_dir():
            continue
        if not any(date_dir.glob("*.parquet")):
            continue
        symbols.append(sym_dir.name.removeprefix("symbol="))
    return sorted(symbols)


def delete_warm_partition(warm_dir: Path, symbol: str, d: date) -> int:
    """``warm_dir/symbol=<S>/date=<D>/`` 配下の Parquet を削除し、削除件数を返す。

    区画自体が無ければ 0。Parquet 削除後に区画が空なら ``date=`` ディレクトリも
    rmdir する (``symbol=`` 直下は別 date が残り得るので触らない)。
    """
    part_dir = warm_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
    if not part_dir.is_dir():
        return 0
    files = list(part_dir.glob("*.parquet"))
    for f in files:
        f.unlink()
    if not any(part_dir.iterdir()):
        part_dir.rmdir()
    logger.info(
        "warm partition deleted: symbol=%s date=%s removed=%d",
        symbol,
        d.isoformat(),
        len(files),
    )
    return len(files)
