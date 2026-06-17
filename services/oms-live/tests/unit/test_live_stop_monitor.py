from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from oms_live._testing import DEFAULT_TS, make_live_position
from oms_live.stop_monitor import evaluate_live_stop


def test_stop_loss_exits_day_position() -> None:
    pos = make_live_position(stop_loss_price=Decimal("950"))

    decision = evaluate_live_stop(position=pos, latest_bid=Decimal("950"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "stop_loss"


def test_target_exits_after_stop_check() -> None:
    pos = make_live_position(stop_loss_price=Decimal("950"), target_price=Decimal("1100"))

    decision = evaluate_live_stop(position=pos, latest_bid=Decimal("1100"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "target"


def test_trailing_stop_only_raises_stop() -> None:
    pos = make_live_position(stop_loss_price=Decimal("980"), trailing_stop_pct=Decimal("0.02"))

    decision = evaluate_live_stop(position=pos, latest_bid=Decimal("1100"), now=DEFAULT_TS)

    assert decision.action == "trail"
    assert decision.new_stop_loss_price == Decimal("1078")


def test_trailing_stop_does_not_lower_existing_stop() -> None:
    pos = make_live_position(stop_loss_price=Decimal("1078"), trailing_stop_pct=Decimal("0.02"))

    decision = evaluate_live_stop(position=pos, latest_bid=Decimal("1090"), now=DEFAULT_TS)

    assert decision.action == "hold"


def test_max_hold_days_exits() -> None:
    pos = make_live_position(max_hold_days=2, opened_at=DEFAULT_TS - timedelta(days=2))

    decision = evaluate_live_stop(position=pos, latest_bid=Decimal("1000"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "max_hold_days"
