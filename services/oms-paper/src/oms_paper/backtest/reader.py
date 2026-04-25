from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest

from ..models import PaperPosition

logger = logging.getLogger(__name__)


def iter_order_requests(path: Path) -> Iterator[OrderRequest]:
    """Stream ``OrderRequest`` from a JSONL file (gateway backtest output).

    Blank lines are skipped. Malformed rows raise ``pydantic.ValidationError`` —
    callers decide whether to abort or skip.
    """
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            yield OrderRequest.model_validate_json(line)


def iter_order_books(path: Path) -> Iterator[OrderBookSnapshot]:
    """Stream ``OrderBookSnapshot`` from a JSONL file (feature-engine fixtures)."""
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            yield OrderBookSnapshot.model_validate_json(line)


def read_positions_json(path: Path) -> dict[str, PaperPosition]:
    """``{symbol: PaperPosition}`` 形式の JSON を読み込む。

    値部分は ``PaperPosition`` の ``model_validate`` でバリデートする。
    `positions` ファイル全体が空 (``{}``) の場合は空 dict を返す。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"positions file must be a JSON object, got: {type(raw).__name__}")
    return {str(sym): PaperPosition.model_validate(v) for sym, v in raw.items()}
