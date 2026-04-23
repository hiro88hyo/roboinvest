from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aggregator.streaming.buffer import PairingBuffer
from trade_contracts.enums import Action, SignalSource


def _buf(*, bucket_ms: int = 1000, window_ms: int = 500) -> PairingBuffer:
    return PairingBuffer(bucket_ms=bucket_ms, window_ms=window_ms)


def test_bucket_fast_path_fires_when_both_sources_present(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf()
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    rule = signal_factory(source=SignalSource.RULE, action=Action.BUY, created_at=ts)
    ai = signal_factory(source=SignalSource.AI, action=Action.BUY, created_at=ts)

    buf.add(signal=rule, origin="a", ack_id="a1", now_monotonic=100.0)
    assert buf.drain_ready(100.0) == []

    buf.add(signal=ai, origin="b", ack_id="b1", now_monotonic=100.1)
    ready = buf.drain_ready(100.2)
    assert len(ready) == 1
    bucket = ready[0]
    assert bucket.key == ("7203", int(ts.timestamp() * 1000) // 1000)
    assert bucket.ack_ids_a == ["a1"]
    assert bucket.ack_ids_b == ["b1"]
    assert {s.source for s in bucket.signals} == {SignalSource.RULE, SignalSource.AI}
    assert len(buf) == 0


def test_bucket_expires_after_window(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf(window_ms=500)
    rule = signal_factory(source=SignalSource.RULE)
    buf.add(signal=rule, origin="a", ack_id="a1", now_monotonic=0.0)
    assert buf.drain_ready(0.4) == []
    ready = buf.drain_ready(0.5)
    assert len(ready) == 1
    assert ready[0].ack_ids_a == ["a1"]
    assert ready[0].ack_ids_b == []


def test_different_buckets_do_not_pair(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf(bucket_ms=1000, window_ms=5000)
    t1 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 4, 20, 9, 0, 2, tzinfo=UTC)  # different bucket (2s gap @ 1000ms bucket)
    buf.add(
        signal=signal_factory(source=SignalSource.RULE, created_at=t1),
        origin="a",
        ack_id="a1",
        now_monotonic=0.0,
    )
    buf.add(
        signal=signal_factory(source=SignalSource.AI, created_at=t2),
        origin="b",
        ack_id="b1",
        now_monotonic=0.1,
    )
    assert buf.drain_ready(0.2) == []
    assert len(buf) == 2


def test_different_symbols_do_not_pair(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf(window_ms=5000)
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    buf.add(
        signal=signal_factory(source=SignalSource.RULE, symbol="7203", created_at=ts),
        origin="a",
        ack_id="a1",
        now_monotonic=0.0,
    )
    buf.add(
        signal=signal_factory(source=SignalSource.AI, symbol="9984", created_at=ts),
        origin="b",
        ack_id="b1",
        now_monotonic=0.0,
    )
    assert buf.drain_ready(0.1) == []


def test_consensus_source_ignored_for_fast_path(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf(window_ms=5000)
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    rule = signal_factory(source=SignalSource.RULE, created_at=ts)
    consensus = signal_factory(source=SignalSource.CONSENSUS, created_at=ts)
    buf.add(signal=rule, origin="a", ack_id="a1", now_monotonic=0.0)
    buf.add(signal=consensus, origin="b", ack_id="b1", now_monotonic=0.0)
    assert buf.drain_ready(0.1) == []


def test_drain_all_flushes_regardless_of_window(signal_factory) -> None:  # type: ignore[no-untyped-def]
    buf = _buf(window_ms=10_000)
    buf.add(
        signal=signal_factory(source=SignalSource.RULE), origin="a", ack_id="a1", now_monotonic=0.0
    )
    ready = buf.drain_all()
    assert len(ready) == 1
    assert len(buf) == 0


def test_constructor_validates_positive_bucket() -> None:
    with pytest.raises(ValueError):
        PairingBuffer(bucket_ms=0, window_ms=500)


def test_constructor_validates_non_negative_window() -> None:
    with pytest.raises(ValueError):
        PairingBuffer(bucket_ms=1000, window_ms=-1)
