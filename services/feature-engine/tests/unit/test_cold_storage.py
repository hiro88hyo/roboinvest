from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from feature_engine.storage.cold import (
    aggregate_to_ohlcv,
    delete_warm_partition,
    enumerate_warm_symbols,
    load_warm_partition,
    migrate_warm_to_cold,
    write_cold_partition,
)
from feature_engine.storage.warm import WarmWriter
from trade_contracts.market import TickData


def _tick(symbol: str, ts: datetime, price: str, volume: int = 100) -> TickData:
    return TickData(symbol=symbol, timestamp=ts, price=Decimal(price), volume=volume)


def _tick_df(rows: list[tuple[str, datetime, float, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "timestamp": [r[1] for r in rows],
            "price": [r[2] for r in rows],
            "volume": [r[3] for r in rows],
        }
    )


def test_aggregate_to_ohlcv_from_raw_ticks() -> None:
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    df = _tick_df(
        [
            ("7203", base, 100.0, 10),
            ("7203", base + timedelta(seconds=30), 120.0, 5),
            ("7203", base + timedelta(seconds=59), 110.0, 3),
            ("7203", base + timedelta(minutes=1), 115.0, 7),
        ]
    )
    out = aggregate_to_ohlcv(df, "1m").sort("timestamp")
    assert out.height == 2
    assert out.get_column("open").to_list() == [100.0, 115.0]
    assert out.get_column("high").to_list() == [120.0, 115.0]
    assert out.get_column("low").to_list() == [100.0, 115.0]
    assert out.get_column("close").to_list() == [110.0, 115.0]
    assert out.get_column("volume").to_list() == [18, 7]


def test_aggregate_to_ohlcv_from_ohlcv_bars() -> None:
    # 1s bars → re-aggregate to 1m
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "symbol": ["7203"] * 3,
            "timestamp": [base, base + timedelta(seconds=10), base + timedelta(seconds=20)],
            "open": [100.0, 105.0, 110.0],
            "high": [101.0, 108.0, 112.0],
            "low": [99.0, 104.0, 109.0],
            "close": [100.5, 106.0, 111.0],
            "volume": [10, 15, 20],
        }
    )
    out = aggregate_to_ohlcv(df, "1m")
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["open"] == 100.0  # first bar's open
    assert row["high"] == 112.0  # max high
    assert row["low"] == 99.0  # min low
    assert row["close"] == 111.0  # last bar's close
    assert row["volume"] == 45


def test_aggregate_empty_returns_empty() -> None:
    df = pl.DataFrame()
    out = aggregate_to_ohlcv(df, "1m")
    assert out.is_empty()


def test_aggregate_5m_groups_5_minute_windows() -> None:
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    df = _tick_df([("7203", base + timedelta(minutes=i), 100.0 + i, 10) for i in range(6)])
    out = aggregate_to_ohlcv(df, "5m").sort("timestamp")
    # minutes 0..4 in bar 1, minute 5 in bar 2
    assert out.height == 2
    assert out.get_column("open").to_list() == [100.0, 105.0]
    assert out.get_column("high").to_list() == [104.0, 105.0]
    assert out.get_column("volume").to_list() == [50, 10]


def test_load_warm_partition_missing_returns_empty(tmp_path: Path) -> None:
    df = load_warm_partition(tmp_path, "7203", date(2026, 4, 20))
    assert df.is_empty()


def test_load_warm_partition_concats_multiple_files(tmp_path: Path) -> None:
    # write two warm flushes into the same partition
    writer = WarmWriter(base_dir=tmp_path, resolution="raw")
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("7203", base, "100"))
    writer.flush()
    writer.record_tick(_tick("7203", base + timedelta(seconds=1), "101"))
    writer.flush()
    df = load_warm_partition(tmp_path, "7203", date(2026, 4, 20))
    assert df.height == 2
    assert df.get_column("price").to_list() == [100.0, 101.0]


def test_write_cold_partition_layout(tmp_path: Path) -> None:
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    df = aggregate_to_ohlcv(
        _tick_df([("7203", base, 100.0, 10), ("7203", base + timedelta(seconds=30), 105.0, 5)]),
        "1m",
    )
    path = write_cold_partition(df, tmp_path, "7203", date(2026, 4, 20), "1m")
    assert path.parent == tmp_path / "symbol=7203" / "date=2026-04-20"
    assert path.name.startswith("1m_")
    loaded = pl.read_parquet(path)
    assert loaded.height == 1


def test_write_cold_partition_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_cold_partition(pl.DataFrame(), tmp_path, "7203", date(2026, 4, 20), "1m")


def test_migrate_warm_to_cold_end_to_end(tmp_path: Path) -> None:
    warm_dir = tmp_path / "warm"
    cold_dir = tmp_path / "cold"
    writer = WarmWriter(base_dir=warm_dir, resolution="raw")
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)  # 9:00 JST on 2026-04-20
    for i, price in enumerate(["100", "110", "105", "115"]):
        writer.record_tick(_tick("7203", base + timedelta(seconds=i * 15), price))
    writer.flush()
    path = migrate_warm_to_cold(warm_dir, cold_dir, "7203", date(2026, 4, 20), "1m")
    assert path is not None
    df = pl.read_parquet(path)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["open"] == 100.0
    assert row["high"] == 115.0
    assert row["low"] == 100.0
    assert row["close"] == 115.0
    assert row["volume"] == 400


def test_migrate_returns_none_for_missing_partition(tmp_path: Path) -> None:
    out = migrate_warm_to_cold(
        tmp_path / "warm", tmp_path / "cold", "7203", date(2026, 4, 20), "1m"
    )
    assert out is None


def test_enumerate_warm_symbols_lists_only_target_date(tmp_path: Path) -> None:
    warm_dir = tmp_path / "warm"
    writer = WarmWriter(base_dir=warm_dir, resolution="raw")
    base_target = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    base_other = datetime(2026, 4, 21, 0, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("9432", base_target, "100"))
    writer.record_tick(_tick("7203", base_target, "100"))
    writer.record_tick(_tick("8001", base_other, "100"))  # 違う date
    writer.flush()

    found = enumerate_warm_symbols(warm_dir, date(2026, 4, 20))
    assert found == ["7203", "9432"]  # 8001 は別 date なので含まれない


def test_enumerate_warm_symbols_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert enumerate_warm_symbols(tmp_path / "no-such-warm", date(2026, 4, 20)) == []


def test_enumerate_warm_symbols_skips_empty_partition(tmp_path: Path) -> None:
    # symbol=*/date=* dir はあるが parquet は無い
    empty_dir = tmp_path / "warm" / "symbol=7203" / "date=2026-04-20"
    empty_dir.mkdir(parents=True)
    assert enumerate_warm_symbols(tmp_path / "warm", date(2026, 4, 20)) == []


def test_delete_warm_partition_removes_files_and_dir(tmp_path: Path) -> None:
    warm_dir = tmp_path / "warm"
    writer = WarmWriter(base_dir=warm_dir, resolution="raw")
    base = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
    writer.record_tick(_tick("7203", base, "100"))
    writer.flush()
    writer.record_tick(_tick("7203", base + timedelta(seconds=1), "101"))
    writer.flush()
    part_dir = warm_dir / "symbol=7203" / "date=2026-04-20"
    assert len(list(part_dir.glob("*.parquet"))) == 2

    removed = delete_warm_partition(warm_dir, "7203", date(2026, 4, 20))
    assert removed == 2
    assert not part_dir.exists()  # date= dir も消える
    # symbol= ディレクトリ自体は残す (別 date が将来生まれる可能性)
    assert (warm_dir / "symbol=7203").exists()


def test_delete_warm_partition_returns_zero_for_missing(tmp_path: Path) -> None:
    assert delete_warm_partition(tmp_path / "warm", "7203", date(2026, 4, 20)) == 0
