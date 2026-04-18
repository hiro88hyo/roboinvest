from __future__ import annotations

import logging
from pathlib import Path

import polars as pl
from trade_contracts.features import ProcessedFeatures

logger = logging.getLogger(__name__)


def write_jsonl(features: list[ProcessedFeatures], path: Path) -> int:
    """`ProcessedFeatures` を 1 行 1 レコードの JSONL で書き出す。既存ファイルは上書き。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for feat in features:
            f.write(feat.model_dump_json())
            f.write("\n")
    logger.info("wrote jsonl: path=%s rows=%d", path, len(features))
    return len(features)


def write_parquet(df: pl.DataFrame, path: Path) -> int:
    """指標列を含む DataFrame を Parquet で書き出す。既存ファイルは上書き。

    JSONL と違い、Parquet は `ProcessedFeatures` ではなく指標付き `daily_ohlcv` の
    形をそのまま保存する (Polars で扱いやすい)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    logger.info("wrote parquet: path=%s rows=%d", path, df.height)
    return df.height
