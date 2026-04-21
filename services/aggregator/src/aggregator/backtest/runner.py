from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import chain

from trade_contracts.signal import StrategySignal, UnifiedTradeSignal

from ..consensus import ConsensusConfig, aggregate
from ..pairing import group_by_bucket

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class BacktestSummary:
    input_count: int
    bucket_count: int
    unified_count: int
    unified: list[UnifiedTradeSignal]


def run_backtest(
    signals_a: Iterable[StrategySignal],
    signals_b: Iterable[StrategySignal] | None,
    *,
    config: ConsensusConfig,
    bucket_ms: int,
) -> BacktestSummary:
    """Pair A/B StrategySignals by `(symbol, time-bucket)` and run consensus.

    Passing ``None`` for ``signals_b`` is a supported pass-through mode for
    pre-strategy-ai backtests: every bucket contains RULE-only signals and the
    consensus function returns a single-source UnifiedTradeSignal.

    Chronological output order is produced by `group_by_bucket`. Within each
    bucket, the pairing's ``created_at`` for the UnifiedTradeSignal is set to
    the latest input signal's ``created_at`` so that timestamps remain
    consistent with the underlying data rather than wall-clock ``now``.
    """
    all_signals = list(chain(signals_a, signals_b or []))
    unified: list[UnifiedTradeSignal] = []
    bucket_count = 0
    for bucket in group_by_bucket(all_signals, bucket_ms=bucket_ms):
        bucket_count += 1
        latest = max(s.created_at for s in bucket)
        merged = aggregate(bucket, config=config, now=latest)
        if merged is not None:
            unified.append(merged)
    logger.info(
        "backtest done: inputs=%d buckets=%d unified=%d",
        len(all_signals),
        bucket_count,
        len(unified),
    )
    return BacktestSummary(
        input_count=len(all_signals),
        bucket_count=bucket_count,
        unified_count=len(unified),
        unified=unified,
    )
