"""Time-bucket pairing of StrategySignals for consensus.

Both Phase 2 (backtest) and Phase 3 (streaming) need to gather StrategySignals
that were emitted around the same moment for the same symbol so the consensus
function can see A and B together. This module provides the pure function used
by the backtest driver; the streaming buffer (Phase 3) will wrap it with a
time-window policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from trade_contracts.signal import StrategySignal

BucketKey = tuple[str, int, str | None, str | None]


def bucket_key(signal: StrategySignal, bucket_ms: int) -> BucketKey:
    """Return a time bucket isolated by symbol and strategy candidate identity."""
    if bucket_ms <= 0:
        raise ValueError(f"bucket_ms must be positive, got {bucket_ms}")
    ts_ms = int(signal.created_at.timestamp() * 1000)
    return (
        signal.symbol,
        ts_ms // bucket_ms,
        signal.strategy_key,
        signal.candidate_id,
    )


def group_by_bucket(
    signals: Iterable[StrategySignal],
    *,
    bucket_ms: int,
) -> Iterator[list[StrategySignal]]:
    """Group signals by symbol, time bucket, and candidate identity.

    Output order is chronological by bucket, then alphabetical by symbol and
    strategy/candidate identity. Within a bucket, signals retain input order.
    """
    groups: dict[BucketKey, list[StrategySignal]] = {}
    for s in signals:
        groups.setdefault(bucket_key(s, bucket_ms), []).append(s)
    for key in sorted(
        groups.keys(),
        key=lambda kk: (kk[1], kk[0], kk[2] or "", kk[3] or ""),
    ):
        yield groups[key]
