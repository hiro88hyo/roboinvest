from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from gateway.backtest import run_backtest
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


def test_empty_input(kill_switch_state_factory) -> None:  # type: ignore[no-untyped-def]
    summary = run_backtest(
        [],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert summary.input_count == 0
    assert summary.approved == []
    assert summary.rejected == []


def test_approves_valid_buy(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert summary.approved_count == 1
    assert summary.rejected_count == 0
    [order] = summary.approved
    assert order.unified_signal_id == signal.signal_id
    assert order.quantity == 400
    assert order.trade_mode is TradeMode.LIVE  # from state


def test_uses_signal_price_when_entry_price_not_supplied(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(
        action=Action.BUY,
        price=Decimal("2000"),
        stop_loss_price=Decimal("1900"),
    )
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=None,
    )

    [order] = summary.approved
    assert order.limit_price == Decimal("2000")
    assert order.quantity == 200


def test_buy_limit_offset_ticks_are_applied(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(
        action=Action.BUY,
        price=Decimal("2000"),
        stop_loss_price=Decimal("1900"),
    )
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=None,
        buy_limit_offset_ticks=3,
    )

    [order] = summary.approved
    assert order.limit_price == Decimal("2003")


def test_rejects_buy_without_any_entry_price(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(
        action=Action.BUY,
        price=None,
        stop_loss_price=Decimal("1900"),
    )
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=None,
    )

    assert summary.approved == []
    [rejected] = summary.rejected
    assert rejected.reason == "missing_entry_price"


def test_rejects_hold(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.HOLD)
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert summary.approved == []
    [rej] = summary.rejected
    assert rej.unified_signal_id == signal.signal_id
    assert rej.reason == "action_hold"
    assert rej.created_at == signal.created_at


def test_rejects_all_when_kill_switch_off(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    state = kill_switch_state_factory(is_trading_allowed=False)
    signals = [
        unified_signal_factory(symbol="7203", action=Action.BUY, stop_loss_price=Decimal("950")),
        unified_signal_factory(symbol="9984", action=Action.BUY, stop_loss_price=Decimal("950")),
    ]
    summary = run_backtest(
        signals,
        state=state,
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert summary.approved == []
    assert summary.rejected_count == 2
    assert all(r.reason == "kill_switch_off" for r in summary.rejected)


def test_positions_allow_sell(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(symbol="7203", action=Action.SELL)
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        positions={"7203": 300},
    )
    [order] = summary.approved
    assert order.quantity == 300
    assert order.side.value == "SELL"


def test_sell_without_positions_rejected(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.SELL)
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    [rej] = summary.rejected
    assert rej.reason == "no_position_for_sell"


def test_positions_block_duplicate_buy(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(
        symbol="7203", action=Action.BUY, stop_loss_price=Decimal("950")
    )
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        positions={"7203": 100},
    )
    [rej] = summary.rejected
    assert rej.reason == "already_long"


def test_mixed_batch_splits_correctly(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    good = unified_signal_factory(symbol="7203", action=Action.BUY, stop_loss_price=Decimal("950"))
    hold = unified_signal_factory(symbol="9984", action=Action.HOLD)
    sell_no_pos = unified_signal_factory(symbol="6758", action=Action.SELL)
    summary = run_backtest(
        [good, hold, sell_no_pos],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    assert summary.approved_count == 1
    assert summary.rejected_count == 2
    reasons = {r.symbol: r.reason for r in summary.rejected}
    assert reasons == {"9984": "action_hold", "6758": "no_position_for_sell"}


def test_signal_created_at_is_used_for_approved_by_default(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal_time = datetime(2026, 4, 23, 9, 15, tzinfo=UTC)
    signal = unified_signal_factory(
        action=Action.BUY,
        stop_loss_price=Decimal("950"),
        created_at=signal_time,
    )
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    [order] = summary.approved
    assert order.created_at == signal_time


def test_now_overrides_created_at_for_approved(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    fixed = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
        now=fixed,
    )
    [order] = summary.approved
    assert order.created_at == fixed


def test_trade_mode_follows_state(  # type: ignore[no-untyped-def]
    unified_signal_factory, kill_switch_state_factory
) -> None:
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    summary = run_backtest(
        [signal],
        state=kill_switch_state_factory(trade_mode=TradeMode.PAPER),
        risk_config=_risk_cfg(),
        entry_price=Decimal("1000"),
    )
    [order] = summary.approved
    assert order.trade_mode is TradeMode.PAPER
