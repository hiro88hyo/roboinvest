from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from feature_engine.storage.warm import WarmWriter
from trade_contracts.market import TickData


def _tick(symbol: str, ts: datetime, price: str, volume: int = 100) -> TickData:
    return TickData(symbol=symbol, timestamp=ts, price=Decimal(price), volume=volume)


def test_flush_threshold_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WarmWriter(base_dir=tmp_path, resolution="raw", flush_threshold=0)


def test_record_tick_rejects_naive_timestamp(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    naive = datetime(2026, 4, 20, 9, 0)  # no tzinfo
    with pytest.raises(ValueError):
        writer.record_tick(_tick("7203", naive, "2500"))


def test_flush_empty_buffer_is_noop(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    assert writer.flush() == []
    assert writer.flush(symbol="7203") == []


def test_raw_flush_writes_each_tick(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)  # 9:00 JST
    for i, price in enumerate(["2500", "2501", "2502"]):
        writer.record_tick(_tick("7203", base + timedelta(seconds=i), price))
    paths = writer.flush()
    assert len(paths) == 1
    df = pl.read_parquet(paths[0])
    assert df.height == 3
    assert df.columns == ["symbol", "timestamp", "price", "volume"]
    assert df.get_column("price").to_list() == [2500.0, 2501.0, 2502.0]


def test_raw_partition_path_uses_jst_date(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    # 2026-04-20 00:00 UTC = 2026-04-20 09:00 JST
    ts_utc = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("7203", ts_utc, "2500"))
    paths = writer.flush()
    assert paths[0].parent == tmp_path / "symbol=7203" / "date=2026-04-20"


def test_raw_splits_across_jst_midnight(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    # 14:59 UTC = 23:59 JST on 4/19. 15:00 UTC = 00:00 JST on 4/20.
    writer.record_tick(_tick("7203", datetime(2026, 4, 19, 14, 59, tzinfo=UTC), "2500"))
    writer.record_tick(_tick("7203", datetime(2026, 4, 19, 15, 0, tzinfo=UTC), "2501"))
    paths = writer.flush()
    partitions = {p.parent.name for p in paths}
    assert partitions == {"date=2026-04-19", "date=2026-04-20"}


def test_auto_flush_on_threshold(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw", flush_threshold=2)
    base = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    assert writer.record_tick(_tick("7203", base, "100")) == []
    paths = writer.record_tick(_tick("7203", base + timedelta(seconds=1), "101"))
    assert len(paths) == 1
    # buffer should now be empty; next tick does not trigger flush
    assert writer.record_tick(_tick("7203", base + timedelta(seconds=2), "102")) == []


def test_flush_symbol_only_isolates_others(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    ts = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("7203", ts, "2500"))
    writer.record_tick(_tick("9984", ts, "8000"))
    paths = writer.flush(symbol="7203")
    assert len(paths) == 1
    assert paths[0].parent.parent.name == "symbol=7203"
    # 9984 is still buffered; flushing it yields its own file
    remaining = writer.flush(symbol="9984")
    assert len(remaining) == 1
    assert remaining[0].parent.parent.name == "symbol=9984"


def test_1s_aggregates_ticks_into_ohlcv_bars(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="1s")
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    # 3 ticks within the same second
    writer.record_tick(_tick("7203", base, "100", volume=10))
    writer.record_tick(_tick("7203", base + timedelta(milliseconds=200), "120", volume=5))
    writer.record_tick(_tick("7203", base + timedelta(milliseconds=800), "110", volume=3))
    # 1 tick in the next second
    writer.record_tick(_tick("7203", base + timedelta(seconds=1), "115", volume=7))
    paths = writer.flush()
    df = pl.read_parquet(paths[0]).sort("timestamp")
    assert df.height == 2
    assert df.get_column("open").to_list() == [100.0, 115.0]
    assert df.get_column("high").to_list() == [120.0, 115.0]
    assert df.get_column("low").to_list() == [100.0, 115.0]
    assert df.get_column("close").to_list() == [110.0, 115.0]
    assert df.get_column("volume").to_list() == [18, 7]


def test_1m_bar_covers_full_minute(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="1m")
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    for i, price in enumerate(["100", "105", "103", "107"]):
        writer.record_tick(_tick("7203", base + timedelta(seconds=i * 10), price))
    paths = writer.flush()
    df = pl.read_parquet(paths[0])
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["open"] == 100.0
    assert row["high"] == 107.0
    assert row["low"] == 100.0
    assert row["close"] == 107.0


def test_buffer_is_cleared_after_flush(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    ts = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("7203", ts, "2500"))
    writer.flush()
    # second flush on cleared buffer → no writes
    assert writer.flush() == []


def test_filename_encodes_resolution_and_ms_range(tmp_path: Path) -> None:
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    first = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    last = datetime(2026, 4, 20, 0, 0, 2, tzinfo=UTC)
    writer.record_tick(_tick("7203", first, "100"))
    writer.record_tick(_tick("7203", last, "101"))
    paths = writer.flush()
    first_ms = int(first.timestamp() * 1000)
    last_ms = int(last.timestamp() * 1000)
    assert paths[0].name == f"raw_{first_ms}_{last_ms}.parquet"
