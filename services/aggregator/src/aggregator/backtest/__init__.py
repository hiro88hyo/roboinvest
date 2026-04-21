"""Backtest CLI helpers: read StrategySignal JSONL, pair + aggregate, write unified JSONL."""

from .reader import iter_signals
from .runner import BacktestSummary, run_backtest
from .writer import write_jsonl

__all__ = [
    "BacktestSummary",
    "iter_signals",
    "run_backtest",
    "write_jsonl",
]
