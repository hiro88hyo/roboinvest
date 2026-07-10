"""Time-window pairing buffer for streaming aggregation.

Holds `StrategySignal` from the RULE and AI subscriptions keyed by
`(symbol, bucket, strategy_key, candidate_id)` where bucket is derived from
`created_at`. The identity fields keep an event candidate from pairing with
an unrelated RULE/AI decision. A bucket is considered *ready* to emit when:

  - both RULE and AI signals are present (fast path), or
  - the wall-clock time since the first signal landed in the bucket has
    exceeded `window_ms` (timeout path).

Ack ids ride alongside the signal so the runner can acknowledge each
source subscription separately after a bucket is emitted.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from trade_contracts.enums import SignalSource
from trade_contracts.signal import StrategySignal

from ..pairing import BucketKey, bucket_key

Origin = Literal["a", "b"]


@dataclass(frozen=True, slots=True)
class BufferedSignal:
    signal: StrategySignal
    origin: Origin
    ack_id: str
    received_at: float  # monotonic seconds


@dataclass(frozen=True, slots=True)
class ReadyBucket:
    key: BucketKey
    signals: list[StrategySignal]
    ack_ids_a: list[str]
    ack_ids_b: list[str]


@dataclass(slots=True)
class PairingBuffer:
    """In-memory buffer that yields complete or expired buckets."""

    bucket_ms: int
    window_ms: int
    _entries: dict[BucketKey, list[BufferedSignal]] = field(default_factory=dict)
    _first_seen: dict[BucketKey, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.bucket_ms <= 0:
            raise ValueError(f"bucket_ms must be positive, got {self.bucket_ms}")
        if self.window_ms < 0:
            raise ValueError(f"window_ms must be non-negative, got {self.window_ms}")

    def add(
        self,
        *,
        signal: StrategySignal,
        origin: Origin,
        ack_id: str,
        now_monotonic: float,
    ) -> BucketKey:
        key = bucket_key(signal, self.bucket_ms)
        self._entries.setdefault(key, []).append(
            BufferedSignal(signal=signal, origin=origin, ack_id=ack_id, received_at=now_monotonic)
        )
        self._first_seen.setdefault(key, now_monotonic)
        return key

    def __len__(self) -> int:
        return len(self._entries)

    def drain_ready(self, now_monotonic: float) -> list[ReadyBucket]:
        """Remove and return all buckets that are complete or have expired.

        A bucket is complete when it holds at least one RULE signal *and* at
        least one AI signal. It has expired when `now_monotonic - first_seen`
        exceeds `window_ms / 1000`.
        """
        ready_keys: list[BucketKey] = []
        window_seconds = self.window_ms / 1000.0
        for key, items in self._entries.items():
            if _has_both_sources(s.signal for s in items):
                ready_keys.append(key)
                continue
            if now_monotonic - self._first_seen[key] >= window_seconds:
                ready_keys.append(key)
        return [self._pop(key) for key in ready_keys]

    def drain_all(self) -> list[ReadyBucket]:
        """Flush every bucket regardless of readiness — for graceful shutdown."""
        return [self._pop(key) for key in list(self._entries.keys())]

    def _pop(self, key: BucketKey) -> ReadyBucket:
        items = self._entries.pop(key)
        self._first_seen.pop(key, None)
        return ReadyBucket(
            key=key,
            signals=[bs.signal for bs in items],
            ack_ids_a=[bs.ack_id for bs in items if bs.origin == "a"],
            ack_ids_b=[bs.ack_id for bs in items if bs.origin == "b"],
        )


def _has_both_sources(signals: Iterable[StrategySignal]) -> bool:
    seen_rule = False
    seen_ai = False
    for s in signals:
        if s.source is SignalSource.RULE:
            seen_rule = True
        elif s.source is SignalSource.AI:
            seen_ai = True
        if seen_rule and seen_ai:
            return True
    return False
