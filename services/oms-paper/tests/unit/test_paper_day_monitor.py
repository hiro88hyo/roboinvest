from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from oms_paper._testing import DEFAULT_TS, make_paper_position
from oms_paper.day_monitor import evaluate_day_exit
from trade_contracts.enums import TradingStyle


def test_swing_position_is_hold() -> None:
    pos = make_paper_position(
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("950"),
    )

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("950"), now=DEFAULT_TS)

    assert decision.action == "hold"


def test_stop_loss_exits_day_position() -> None:
    pos = make_paper_position(stop_loss_price=Decimal("950"))

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("950"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "stop_loss"


def test_target_exits_day_position() -> None:
    pos = make_paper_position(stop_loss_price=Decimal("950"), target_price=Decimal("1100"))

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("1100"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "target"


def test_trailing_stop_raises_stop() -> None:
    pos = make_paper_position(stop_loss_price=Decimal("980"), trailing_stop_pct=Decimal("0.02"))

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("1100"), now=DEFAULT_TS)

    assert decision.action == "trail"
    assert decision.new_stop_loss_price == Decimal("1078")


def test_trailing_stop_does_not_lower_existing_stop() -> None:
    pos = make_paper_position(stop_loss_price=Decimal("1078"), trailing_stop_pct=Decimal("0.02"))

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("1090"), now=DEFAULT_TS)

    assert decision.action == "hold"


def test_max_hold_days_exits_day_position() -> None:
    pos = make_paper_position(max_hold_days=2, opened_at=DEFAULT_TS - timedelta(days=2))

    decision = evaluate_day_exit(position=pos, latest_price=Decimal("1000"), now=DEFAULT_TS)

    assert decision.action == "exit"
    assert decision.reason == "max_hold_days"
