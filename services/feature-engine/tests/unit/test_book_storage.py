from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from feature_engine.storage.book import BookWarmWriter, enumerate_book_symbols, load_book_partition
from trade_contracts.market import OrderBookSnapshot, PriceLevel


def _book(symbol: str, ts: datetime) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=ts,
        bids=[PriceLevel(price=Decimal("999"), quantity=100)],
        asks=[PriceLevel(price=Decimal("1000"), quantity=200)],
    )


def test_record_book_rejects_naive_timestamp(tmp_path: Path) -> None:
    writer = BookWarmWriter(base_dir=tmp_path)
    naive = datetime(2026, 4, 20, 9, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        writer.record_book(_book("7203", naive))


def test_flush_writes_book_partition_and_loads_it(tmp_path: Path) -> None:
    writer = BookWarmWriter(base_dir=tmp_path)
    ts = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    writer.record_book(_book("7203", ts))
    paths = writer.flush()

    assert len(paths) == 1
    assert paths[0].name.startswith("book_")
    df = load_book_partition(tmp_path, "7203", date(2026, 4, 20))
    assert df.select(pl.len()).item() == 1
    row = df.row(0, named=True)
    assert row["symbol"] == "7203"
    assert json.loads(row["bids_json"]) == [{"price": "999", "quantity": 100}]
    assert json.loads(row["asks_json"]) == [{"price": "1000", "quantity": 200}]


def test_enumerate_book_symbols_lists_only_target_date(tmp_path: Path) -> None:
    writer = BookWarmWriter(base_dir=tmp_path)
    target = datetime(2026, 4, 20, 0, 0, tzinfo=UTC)
    other = datetime(2026, 4, 21, 0, 0, tzinfo=UTC)
    writer.record_book(_book("9432", target))
    writer.record_book(_book("7203", target))
    writer.record_book(_book("8001", other))
    writer.flush()

    assert enumerate_book_symbols(tmp_path, date(2026, 4, 20)) == ["7203", "9432"]


def test_load_book_partition_missing_returns_empty(tmp_path: Path) -> None:
    assert load_book_partition(tmp_path, "7203", date(2026, 4, 20)).is_empty()
