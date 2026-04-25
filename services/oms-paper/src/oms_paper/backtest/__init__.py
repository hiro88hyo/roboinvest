"""Phase 2 backtest pipeline.

OrderRequest JSONL + OrderBookSnapshot JSONL を入力として、
擬似約定の結果と最終 positions を JSONL / JSON で書き出す。
"""

from __future__ import annotations

from .reader import iter_order_books, iter_order_requests, read_positions_json
from .runner import BacktestSummary, NoFillRecord, run_backtest
from .writer import write_jsonl, write_positions_json

__all__ = [
    "BacktestSummary",
    "NoFillRecord",
    "iter_order_books",
    "iter_order_requests",
    "read_positions_json",
    "run_backtest",
    "write_jsonl",
    "write_positions_json",
]
