from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aggregator.backtest.runner import run_backtest
from aggregator.consensus import ConsensusConfig
from trade_contracts.enums import Action, SignalSource, TradingStyle


def _cfg(**overrides: object) -> ConsensusConfig:
    base: dict[str, object] = dict(
        weight_rule=Decimal("1.0"),
        weight_ai=Decimal("1.0"),
        min_confidence=Decimal("0.3"),
        conflict_policy="skip",
        default_holding_type=TradingStyle.DAY,
    )
    base.update(overrides)
    return ConsensusConfig(**base)  # type: ignore[arg-type]


def test_run_backtest_rule_only(signal_factory) -> None:  # type: ignore[no-untyped-def]
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    a = [signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8, created_at=ts)]
    summary = run_backtest(a, None, config=_cfg(), bucket_ms=1000)
    assert summary.input_count == 1
    assert summary.bucket_count == 1
    assert summary.unified_count == 1
    [u] = summary.unified
    assert u.signal_source is SignalSource.RULE
    assert u.created_at == ts  # set from latest input, not wall clock


def test_run_backtest_pairs_ab_within_bucket(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t0 = datetime(2026, 4, 20, 9, 0, 0, 0, tzinfo=UTC)
    t_late = datetime(2026, 4, 20, 9, 0, 0, 400_000, tzinfo=UTC)
    a = [signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.6, created_at=t0)]
    b = [
        signal_factory(source=SignalSource.AI, action=Action.BUY, confidence=0.8, created_at=t_late)
    ]
    summary = run_backtest(a, b, config=_cfg(), bucket_ms=1000)
    assert summary.bucket_count == 1
    [u] = summary.unified
    assert u.signal_source is SignalSource.CONSENSUS
    assert u.confidence == pytest.approx(0.7)
    assert u.created_at == t_late  # latest input wins


def test_run_backtest_splits_across_bucket_boundary(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t_a = datetime(2026, 4, 20, 9, 0, 0, 900_000, tzinfo=UTC)
    t_b = datetime(2026, 4, 20, 9, 0, 1, 100_000, tzinfo=UTC)
    a = [
        signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8, created_at=t_a)
    ]
    b = [signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.8, created_at=t_b)]
    summary = run_backtest(a, b, config=_cfg(), bucket_ms=1000)
    # 2 buckets, each single-source — both pass through with their own source
    assert summary.bucket_count == 2
    assert summary.unified_count == 2
    sources = {u.signal_source for u in summary.unified}
    assert sources == {SignalSource.RULE, SignalSource.AI}


def test_run_backtest_conflict_skip_drops_output(signal_factory) -> None:  # type: ignore[no-untyped-def]
    ts = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    a = [signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.9, created_at=ts)]
    b = [signal_factory(source=SignalSource.AI, action=Action.SELL, confidence=0.9, created_at=ts)]
    summary = run_backtest(a, b, config=_cfg(conflict_policy="skip"), bucket_ms=1000)
    assert summary.bucket_count == 1
    assert summary.unified_count == 0


def test_run_backtest_preserves_chronological_order(signal_factory) -> None:  # type: ignore[no-untyped-def]
    t0 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 4, 20, 9, 0, 5, tzinfo=UTC)
    a = [
        signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8, created_at=t1),
        signal_factory(source=SignalSource.RULE, action=Action.BUY, confidence=0.8, created_at=t0),
    ]
    summary = run_backtest(a, None, config=_cfg(), bucket_ms=1000)
    assert [u.created_at for u in summary.unified] == [t0, t1]


def test_run_backtest_empty_inputs() -> None:
    summary = run_backtest([], None, config=_cfg(), bucket_ms=1000)
    assert summary.input_count == 0
    assert summary.bucket_count == 0
    assert summary.unified_count == 0
    assert summary.unified == []
