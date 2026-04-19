from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from feature_engine.streaming.session import TickDecision, TickSession
from trade_contracts.market import TickData


def _tick(symbol: str, ts: datetime, *, price: str = "2500") -> TickData:
    return TickData(symbol=symbol, timestamp=ts, price=Decimal(price), volume=100)


def test_first_tick_is_accepted() -> None:
    session = TickSession()
    t = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    assert session.observe(_tick("7203", t)) == TickDecision.ACCEPT
    assert session.last_timestamp("7203") == t


def test_advancing_tick_is_accepted() -> None:
    session = TickSession()
    t0 = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    session.observe(_tick("7203", t0))
    t1 = t0 + timedelta(seconds=1)
    assert session.observe(_tick("7203", t1)) == TickDecision.ACCEPT
    assert session.last_timestamp("7203") == t1


def test_duplicate_timestamp_is_dropped() -> None:
    session = TickSession()
    t = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    session.observe(_tick("7203", t))
    assert session.observe(_tick("7203", t, price="2501")) == TickDecision.DUPLICATE
    assert session.last_timestamp("7203") == t


def test_stale_tick_is_dropped_without_advancing_state() -> None:
    session = TickSession()
    t1 = datetime(2026, 4, 20, 9, 0, 1, tzinfo=UTC)
    t0 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    session.observe(_tick("7203", t1))
    assert session.observe(_tick("7203", t0)) == TickDecision.STALE
    assert session.last_timestamp("7203") == t1


def test_state_is_tracked_per_symbol() -> None:
    session = TickSession()
    t = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    assert session.observe(_tick("7203", t)) == TickDecision.ACCEPT
    assert session.observe(_tick("9984", t)) == TickDecision.ACCEPT
    assert session.last_timestamp("7203") == t
    assert session.last_timestamp("9984") == t


def test_reset_single_symbol_clears_only_that_symbol() -> None:
    session = TickSession()
    t = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    session.observe(_tick("7203", t))
    session.observe(_tick("9984", t))
    session.reset("7203")
    assert session.last_timestamp("7203") is None
    assert session.last_timestamp("9984") == t


def test_reset_all_clears_everything() -> None:
    session = TickSession()
    t = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    session.observe(_tick("7203", t))
    session.observe(_tick("9984", t))
    session.reset()
    assert session.last_timestamp("7203") is None
    assert session.last_timestamp("9984") is None


def test_after_reset_next_tick_is_accepted_even_if_older() -> None:
    session = TickSession()
    t1 = datetime(2026, 4, 20, 9, 0, 1, tzinfo=UTC)
    t0 = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)
    session.observe(_tick("7203", t1))
    session.reset("7203")
    assert session.observe(_tick("7203", t0)) == TickDecision.ACCEPT
