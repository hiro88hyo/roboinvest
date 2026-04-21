from __future__ import annotations

from datetime import UTC, datetime

import pytest
from trade_contracts.enums import Action, SignalSource

from aggregator.pairing import bucket_key, group_by_bucket


def test_bucket_key_rounds_down(signal_factory) -> None:  # type: ignore[no-untyped-def]
    ts = datetime(2026, 4, 20, 9, 0, 0, 500_000, tzinfo=UTC)  # .5s
    s = signal_factory(created_at=ts)
    # bucket=1000ms → same bucket as the second start
    assert bucket_key(s, 1000) == (s.symbol, int(ts.timestamp() * 1000) // 1000)


def test_bucket_key_zero_raises(signal_factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        bucket_key(signal_factory(), 0)


def test_group_by_bucket_chronological_then_symbol(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t0 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 4, 20, 9, 0, 2, tzinfo=UTC)  # +2s
    inputs = [
        signal_factory(symbol="9984", created_at=t1, source=SignalSource.AI),
        signal_factory(symbol="7203", created_at=t0, source=SignalSource.RULE),
        signal_factory(symbol="7203", created_at=t1, source=SignalSource.AI),
        signal_factory(symbol="9984", created_at=t0, source=SignalSource.RULE),
    ]
    buckets = list(group_by_bucket(inputs, bucket_ms=1000))
    symbols = [(b[0].symbol, b[0].created_at) for b in buckets]
    # Expect sorted by (bucket_ms, symbol)
    assert symbols == [
        ("7203", t0),
        ("9984", t0),
        ("7203", t1),
        ("9984", t1),
    ]


def test_group_by_bucket_groups_within_window(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t_start = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    t_late = datetime(2026, 4, 20, 9, 0, 0, 800_000, tzinfo=UTC)  # +800ms, same bucket
    a = signal_factory(
        symbol="7203", created_at=t_start, source=SignalSource.RULE, action=Action.BUY
    )
    b = signal_factory(symbol="7203", created_at=t_late, source=SignalSource.AI, action=Action.BUY)

    buckets = list(group_by_bucket([a, b], bucket_ms=1000))
    assert len(buckets) == 1
    assert {s.source for s in buckets[0]} == {SignalSource.RULE, SignalSource.AI}


def test_group_by_bucket_splits_across_boundary(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t_a = datetime(2026, 4, 20, 9, 0, 0, 900_000, tzinfo=UTC)
    t_b = datetime(2026, 4, 20, 9, 0, 1, 100_000, tzinfo=UTC)  # +200ms but different 1s bucket
    a = signal_factory(symbol="7203", created_at=t_a, source=SignalSource.RULE)
    b = signal_factory(symbol="7203", created_at=t_b, source=SignalSource.AI)

    buckets = list(group_by_bucket([a, b], bucket_ms=1000))
    assert len(buckets) == 2


def test_group_by_bucket_symbol_isolation(signal_factory) -> None:  # type: ignore[no-untyped-def]
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    a = signal_factory(symbol="7203", created_at=ts, source=SignalSource.RULE)
    b = signal_factory(symbol="9984", created_at=ts, source=SignalSource.RULE)
    buckets = list(group_by_bucket([a, b], bucket_ms=1000))
    assert len(buckets) == 2
    assert {buckets[0][0].symbol, buckets[1][0].symbol} == {"7203", "9984"}


def test_group_by_bucket_empty() -> None:
    assert list(group_by_bucket([], bucket_ms=1000)) == []
