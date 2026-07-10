from __future__ import annotations

from decimal import Decimal

from gateway import validator
from gateway.config import RiskConfig
from trade_contracts.enums import Action, TradeMode


def _risk_cfg() -> RiskConfig:
    return RiskConfig(
        capital=Decimal("1000000"),
        max_risk_per_trade_pct=Decimal("0.02"),
        swing_risk_scale=Decimal("0.5"),
        default_stop_loss_spread_pct=Decimal("0.02"),
        min_lot_size=100,
    )


def test_hold_is_rejected(unified_signal_factory, kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.HOLD)
    state = kill_switch_state_factory()
    result = validator.validate(
        signal=signal,
        state=state,
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert result.passed is False
    assert result.reason == "action_hold"


def test_kill_switch_off_short_circuits_before_lot_calc(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    state = kill_switch_state_factory(is_trading_allowed=False)
    result = validator.validate(
        signal=signal,
        state=state,
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert result.passed is False
    assert result.reason == "kill_switch_off"
    assert result.adjusted_quantity is None


def test_buy_happy_path(unified_signal_factory, kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 400


def test_relative_stop_buy_is_allowed_in_paper(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_pct=Decimal("0.10"))
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(trade_mode=TradeMode.PAPER),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 200


def test_relative_stop_buy_is_rejected_in_live(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_pct=Decimal("0.10"))
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(trade_mode=TradeMode.LIVE),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert result.passed is False
    assert result.reason == "relative_stop_live_unsupported"


def test_buy_with_existing_long_rejects(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=100,
    )
    assert result.passed is False
    assert result.reason == "already_long"


def test_buy_with_zero_long_does_not_reject(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=0,
    )
    assert result.passed is True
    assert result.adjusted_quantity == 400


def test_sell_without_position_rejects(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.SELL)
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=None,
    )
    assert result.passed is False
    assert result.reason == "no_position_for_sell"


def test_sell_with_zero_position_rejects(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.SELL)
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=0,
    )
    assert result.passed is False
    assert result.reason == "no_position_for_sell"


def test_sell_with_position_uses_full_quantity(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.SELL)
    result = validator.validate(
        signal=signal,
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=300,
    )
    assert result.passed is True
    assert result.adjusted_quantity == 300


def test_sell_rejected_by_kill_switch_before_position_check(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.SELL)
    state = kill_switch_state_factory(
        daily_pnl=Decimal("-10000"),
        daily_loss_limit=Decimal("10000"),
    )
    result = validator.validate(
        signal=signal,
        state=state,
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        existing_long_quantity=100,
    )
    assert result.passed is False
    assert result.reason == "daily_loss_limit"
