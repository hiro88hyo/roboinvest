from __future__ import annotations

from decimal import Decimal

from gateway import kill_switch
from trade_contracts.enums import TradeMode


def test_allowed_with_positive_pnl(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        daily_pnl=Decimal("500"),
        weekly_pnl=Decimal("1000"),
        monthly_pnl=Decimal("2000"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is True
    assert result.reason is None


def test_trading_not_allowed_rejects(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(is_trading_allowed=False)
    result = kill_switch.evaluate(state)
    assert result.passed is False
    assert result.reason == "kill_switch_off"


def test_daily_loss_at_limit_rejects(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    # 境界: daily_pnl == -daily_loss_limit は reject 側(fail-closed)
    state = kill_switch_state_factory(
        daily_pnl=Decimal("-10000"),
        daily_loss_limit=Decimal("10000"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is False
    assert result.reason == "daily_loss_limit"


def test_daily_loss_just_above_limit_passes(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        daily_pnl=Decimal("-9999.99"),
        daily_loss_limit=Decimal("10000"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is True


def test_weekly_loss_rejects(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        weekly_pnl=Decimal("-30000"),
        weekly_loss_limit=Decimal("30000"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is False
    assert result.reason == "weekly_loss_limit"


def test_monthly_loss_rejects(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        monthly_pnl=Decimal("-100001"),
        monthly_loss_limit=Decimal("100000"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is False
    assert result.reason == "monthly_loss_limit"


def test_paper_mode_skips_pnl_checks(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        trade_mode=TradeMode.PAPER,
        daily_pnl=Decimal("-999999"),
        weekly_pnl=Decimal("-999999"),
        monthly_pnl=Decimal("-999999"),
    )
    result = kill_switch.evaluate(state)
    assert result.passed is True


def test_paper_mode_still_honors_kill_switch(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    state = kill_switch_state_factory(
        trade_mode=TradeMode.PAPER,
        is_trading_allowed=False,
    )
    result = kill_switch.evaluate(state)
    assert result.passed is False
    assert result.reason == "kill_switch_off"


def test_daily_checked_before_weekly_monthly(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    # すべて breach 時は daily が最初に拾われる(reason の安定性保証)
    state = kill_switch_state_factory(
        daily_pnl=Decimal("-99999"),
        weekly_pnl=Decimal("-99999"),
        monthly_pnl=Decimal("-99999"),
    )
    result = kill_switch.evaluate(state)
    assert result.reason == "daily_loss_limit"
