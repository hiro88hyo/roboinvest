from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from feature_engine.backtest.writer import write_jsonl, write_parquet
from trade_contracts.features import ProcessedFeatures


def _sample_feature() -> ProcessedFeatures:
    return ProcessedFeatures(
        symbol="7203",
        timestamp=datetime(2026, 1, 15, 15, 0, 0),
        price=Decimal("2500.0"),
        sma_short=Decimal("2480.0"),
        sma_long=None,
        rsi=Decimal("55.5"),
        vwap=Decimal("2490.0"),
        volume_ratio=Decimal("1.5"),
        bollinger_upper=Decimal("2600.0"),
        bollinger_middle=Decimal("2500.0"),
        bollinger_lower=Decimal("2400.0"),
        order_book=None,
    )


def test_write_jsonl_roundtrip(tmp_path: Path) -> None:
    features = [_sample_feature(), _sample_feature()]
    out = tmp_path / "sub" / "features.jsonl"
    n = write_jsonl(features, out)
    assert n == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # JSONL の 1 行目が ProcessedFeatures として再読込できること
    rec = json.loads(lines[0])
    assert rec["symbol"] == "7203"
    restored = ProcessedFeatures.model_validate(rec)
    assert restored.price == Decimal("2500.0")


def test_write_jsonl_empty(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    assert write_jsonl([], out) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_write_parquet_roundtrip(tmp_path: Path) -> None:
    df = pl.DataFrame({"symbol": ["7203", "9984"], "close": [2500.0, 8000.0]})
    out = tmp_path / "sub" / "features.parquet"
    n = write_parquet(df, out)
    assert n == 2
    restored = pl.read_parquet(out)
    assert restored.shape == df.shape
    assert restored.get_column("symbol").to_list() == ["7203", "9984"]


def test_write_parquet_creates_parent_dir(tmp_path: Path) -> None:
    df = pl.DataFrame({"x": [1, 2]})
    out = tmp_path / "a" / "b" / "c" / "features.parquet"
    write_parquet(df, out)
    assert out.exists()


def test_write_jsonl_rejects_non_processed_features(tmp_path: Path) -> None:
    out = tmp_path / "features.jsonl"
    with pytest.raises(AttributeError):
        write_jsonl([object()], out)  # type: ignore[list-item]
