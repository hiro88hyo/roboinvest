from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
from trade_contracts.market import OrderBookSnapshot

from .warm import JST_NAME

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BookWarmWriter:
    """OrderBookSnapshot を `base_dir/symbol=<S>/date=<YYYY-MM-DD>/*.parquet` に保存する。"""

    base_dir: Path
    flush_threshold: int = 1000
    _buffers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.flush_threshold <= 0:
            raise ValueError(f"flush_threshold must be positive, got {self.flush_threshold}")

    def record_book(self, book: OrderBookSnapshot) -> list[Path]:
        if book.timestamp.tzinfo is None:
            raise ValueError(f"book.timestamp must be tz-aware: {book.timestamp!r}")
        buf = self._buffers.setdefault(book.symbol, [])
        buf.append(
            {
                "symbol": book.symbol,
                "timestamp": book.timestamp,
                "bids_json": json.dumps(
                    [level.model_dump(mode="json") for level in book.bids],
                    separators=(",", ":"),
                ),
                "asks_json": json.dumps(
                    [level.model_dump(mode="json") for level in book.asks],
                    separators=(",", ":"),
                ),
            }
        )
        if len(buf) >= self.flush_threshold:
            return self.flush(symbol=book.symbol)
        return []

    def flush(self, *, symbol: str | None = None) -> list[Path]:
        targets: Iterable[str] = [symbol] if symbol is not None else list(self._buffers.keys())
        written: list[Path] = []
        for sym in targets:
            buf = self._buffers.get(sym)
            if not buf:
                continue
            df = pl.DataFrame(buf).sort("timestamp")
            with_date = df.with_columns(
                pl.col("timestamp").dt.convert_time_zone(JST_NAME).dt.date().alias("_date")
            )
            for chunk in with_date.partition_by("_date", maintain_order=True):
                d: date = chunk.item(0, "_date")
                out = chunk.drop("_date").sort("timestamp")
                path = self._write_partition(sym, d, out)
                written.append(path)
            self._buffers[sym] = []
        return written

    def _write_partition(self, symbol: str, d: date, df: pl.DataFrame) -> Path:
        part_dir = self.base_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
        part_dir.mkdir(parents=True, exist_ok=True)
        first_ms = int(df.item(0, "timestamp").timestamp() * 1000)
        last_ms = int(df.item(-1, "timestamp").timestamp() * 1000)
        path = part_dir / f"book_{first_ms}_{last_ms}.parquet"
        df.write_parquet(path)
        logger.info("book warm parquet written: path=%s rows=%d", path, df.height)
        return path


def load_book_partition(book_dir: Path, symbol: str, d: date) -> pl.DataFrame:
    part_dir = book_dir / f"symbol={symbol}" / f"date={d.isoformat()}"
    if not part_dir.exists():
        return pl.DataFrame()
    files = sorted(part_dir.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def enumerate_book_symbols(book_dir: Path, d: date) -> list[str]:
    if not book_dir.exists():
        return []
    target_date = d.isoformat()
    symbols: list[str] = []
    for sym_dir in book_dir.glob("symbol=*"):
        if not sym_dir.is_dir():
            continue
        date_dir = sym_dir / f"date={target_date}"
        if not date_dir.is_dir() or not any(date_dir.glob("*.parquet")):
            continue
        symbols.append(sym_dir.name.removeprefix("symbol="))
    return sorted(symbols)
