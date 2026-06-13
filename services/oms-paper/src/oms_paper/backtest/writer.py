from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import BaseModel

from ..models import PaperPosition
from .report import BacktestReport

logger = logging.getLogger(__name__)


def write_jsonl(records: Iterable[BaseModel], path: Path) -> int:
    """Pydantic モデルを JSONL で書き出す。既存ファイルは上書き。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json())
            f.write("\n")
            count += 1
    logger.info("wrote jsonl: path=%s rows=%d", path, count)
    return count


def write_positions_json(positions: Mapping[str, PaperPosition], path: Path) -> int:
    """``{symbol: PaperPosition}`` を整形 JSON で書き出す。

    ``model_dump(mode="json")`` で Decimal / datetime を JSON 互換に変換し、
    安定した順序で出力するため symbol でソートする。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {sym: positions[sym].model_dump(mode="json") for sym in sorted(positions)}
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info("wrote positions json: path=%s rows=%d", path, len(positions))
    return len(positions)


def write_backtest_report(report: BacktestReport, path: Path) -> None:
    """BacktestReport を整形 JSON で書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info("wrote backtest report: path=%s", path)
