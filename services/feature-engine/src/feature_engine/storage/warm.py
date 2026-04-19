from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
from trade_contracts.market import TickData

from feature_engine.config import TickResolution

logger = logging.getLogger(__name__)

JST_NAME = "Asia/Tokyo"

_RESOLUTION_INTERVAL: dict[str, str] = {
    "1s": "1s",
    "1m": "1m",
    "5m": "5m",
}


@dataclass(slots=True)
class WarmWriter:
    """tick を `STORAGE_TICK_RESOLUTION` の粒度で集約し、
    `base_dir/symbol=<S>/date=<YYYY-MM-DD>/<resolution>_<start>_<end>.parquet`
    に書き出す Warm レイヤのシンク。

    - `record_tick` で銘柄別バッファに追加し、`flush_threshold` 到達で自動 flush
    - `flush()` で手動 flush (シャットダウン・定期フラッシュ用)
    - 日付区切りは JST 基準。timestamp は UTC (tz-aware) で保存
    - 1 回の flush で (symbol, date) 組ごとに 1 ファイル書き出す
    """

    base_dir: Path
    resolution: TickResolution
    flush_threshold: int = 1000
    _buffers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.flush_threshold <= 0:
            raise ValueError(f"flush_threshold must be positive, got {self.flush_threshold}")

    def record_tick(self, tick: TickData) -> list[Path]:
        """tick をバッファに追加し、閾値超過なら自動 flush。戻り値は書き出したパス。"""
        if tick.timestamp.tzinfo is None:
            raise ValueError(f"tick.timestamp must be tz-aware: {tick.timestamp!r}")
        buf = self._buffers.setdefault(tick.symbol, [])
        buf.append(
            {
                "symbol": tick.symbol,
                "timestamp": tick.timestamp,
                "price": float(tick.price),
                "volume": int(tick.volume),
            }
        )
        if len(buf) >= self.flush_threshold:
            return self.flush(symbol=tick.symbol)
        return []

    def flush(self, *, symbol: str | None = None) -> list[Path]:
        """バッファを集約・日付分割して Parquet 書き出し。"""
        targets: Iterable[str] = [symbol] if symbol is not None else list(self._buffers.keys())
        written: list[Path] = []
        for sym in targets:
            buf = self._buffers.get(sym)
            if not buf:
                continue
            df = pl.DataFrame(buf).sort("timestamp")
            aggregated = self._aggregate(df)
            with_date = aggregated.with_columns(
                pl.col("timestamp").dt.convert_time_zone(JST_NAME).dt.date().alias("_date")
            )
            for chunk in with_date.partition_by("_date", maintain_order=True):
                d: date = chunk.item(0, "_date")
                out = chunk.drop("_date").sort("timestamp")
                path = self._write_partition(sym, d, out)
                written.append(path)
            self._buffers[sym] = []
        return written

    def _aggregate(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.resolution == "raw":
            return df
        interval = _RESOLUTION_INTERVAL[self.resolution]
        return df.group_by_dynamic("timestamp", every=interval, group_by="symbol").agg(
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )

    def _write_partition(self, symbol: str, d: date, df: pl.DataFrame) -> Path:
        part_dir = self.base_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
        part_dir.mkdir(parents=True, exist_ok=True)
        first_ms = int(df.item(0, "timestamp").timestamp() * 1000)
        last_ms = int(df.item(-1, "timestamp").timestamp() * 1000)
        fname = f"{self.resolution}_{first_ms}_{last_ms}.parquet"
        path = part_dir / fname
        df.write_parquet(path)
        logger.info("warm parquet written: path=%s rows=%d", path, df.height)
        return path
