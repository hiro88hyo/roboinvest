"""Phase 2 backtest pipeline.

OrderRequest JSONL + OrderBookSnapshot JSONL を入力として、
擬似約定の結果と最終 positions を JSONL / JSON で書き出す。
"""

from __future__ import annotations

from .gate import BacktestGateConfig, backtest_gate_failures, check_backtest_report
from .reader import iter_order_books, iter_order_requests, read_positions_json
from .report import BacktestReport, ClosedTrade, ExecutionQualityRecord, build_backtest_report
from .runner import BacktestSummary, NoFillRecord, run_backtest
from .writer import write_backtest_report, write_jsonl, write_positions_json

__all__ = [
    "BacktestGateConfig",
    "BacktestReport",
    "BacktestSummary",
    "ClosedTrade",
    "ExecutionQualityRecord",
    "NoFillRecord",
    "backtest_gate_failures",
    "build_backtest_report",
    "check_backtest_report",
    "iter_order_books",
    "iter_order_requests",
    "read_positions_json",
    "run_backtest",
    "write_backtest_report",
    "write_jsonl",
    "write_positions_json",
]
