"""Phase 2 backtest pipeline: UnifiedTradeSignal JSONL -> OrderRequest / Rejection JSONL."""

from __future__ import annotations

from .reader import iter_unified_signals
from .runner import BacktestSummary, RejectionRecord, run_backtest
from .writer import write_jsonl

__all__ = [
    "BacktestSummary",
    "RejectionRecord",
    "iter_unified_signals",
    "run_backtest",
    "write_jsonl",
]
