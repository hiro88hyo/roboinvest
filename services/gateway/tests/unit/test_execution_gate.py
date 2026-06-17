from __future__ import annotations

from decimal import Decimal

from gateway.execution_gate import ExecutionGateConfig, reject_reason
from trade_contracts.enums import Action


def test_sell_is_not_blocked(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(
        action=Action.SELL,
        spread_bps=Decimal("999"),
        ask_depth_5=0,
    )
    assert reject_reason(signal=signal, quantity=100, config=ExecutionGateConfig()) is None


def test_wide_spread_rejects_buy(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, spread_bps=Decimal("31"))
    assert (
        reject_reason(signal=signal, quantity=100, config=ExecutionGateConfig())
        == "execution_spread_too_wide"
    )


def test_wide_spread_ticks_rejects_buy(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, spread_ticks=Decimal("3"))
    assert (
        reject_reason(signal=signal, quantity=100, config=ExecutionGateConfig())
        == "execution_spread_ticks_too_wide"
    )


def test_insufficient_ask_depth_rejects_buy(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, ask_depth_5=250)
    assert (
        reject_reason(
            signal=signal,
            quantity=100,
            config=ExecutionGateConfig(min_ask_depth_multiplier=Decimal("3")),
        )
        == "execution_insufficient_ask_depth"
    )


def test_missing_execution_context_does_not_reject(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY)
    assert reject_reason(signal=signal, quantity=100, config=ExecutionGateConfig()) is None
