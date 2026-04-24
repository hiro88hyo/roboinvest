from __future__ import annotations

from decimal import Decimal

import pytest
from gateway import lot_calculator
from gateway.config import RiskConfig
from trade_contracts.enums import Action, TradingStyle


def _cfg(**overrides: object) -> RiskConfig:
    defaults: dict[str, object] = dict(
        capital=Decimal("1000000"),
        max_risk_per_trade_pct=Decimal("0.02"),
        swing_risk_scale=Decimal("0.5"),
        default_stop_loss_spread_pct=Decimal("0.02"),
        min_lot_size=100,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)  # type: ignore[arg-type]


def test_buy_with_explicit_stop_loss(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(
        action=Action.BUY,
        stop_loss_price=Decimal("950"),
    )
    # risk = 1,000,000 * 0.02 = 20,000
    # risk/share = 1000 - 950 = 50
    # raw = 20,000 / 50 = 400 shares
    # adjusted = floor(400/100)*100 = 400
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 400


def test_buy_floors_to_lot_size(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("970"))
    # risk = 20,000; risk/share = 30; raw = 666.66...
    # adjusted = floor(666/100)*100 = 600
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 600


def test_buy_without_explicit_stop_uses_default_spread(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=None)
    # default_spread_pct = 0.02 → stop = 1000*(1-0.02) = 980
    # risk/share = 20; risk = 20,000
    # raw = 1,000; adjusted = 1,000
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 1000


def test_buy_swing_applies_risk_scale(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(
        action=Action.BUY,
        holding_type=TradingStyle.SWING,
        stop_loss_price=Decimal("950"),
    )
    # risk = 20,000 * 0.5 = 10,000; risk/share = 50; raw = 200
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 200


def test_buy_stop_loss_at_entry_rejects(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("1000"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is False
    assert result.reason == "invalid_stop_loss"


def test_buy_stop_loss_above_entry_rejects(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("1050"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is False
    assert result.reason == "invalid_stop_loss"


def test_buy_below_min_lot_rejects(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    # risk = 20,000; risk/share = 300; raw = 66.67 → floor to 0 lots
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("700"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is False
    assert result.reason == "below_min_lot"


def test_buy_exactly_one_lot_passes(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    # risk = 20,000; risk/share = 200; raw = 100 → 100 shares = 1 lot
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("800"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(),
    )
    assert result.passed is True
    assert result.adjusted_quantity == 100


def test_zero_entry_price_rejects(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("0"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("0"),
        config=_cfg(),
    )
    assert result.passed is False
    assert result.reason == "invalid_entry_price"


def test_non_buy_signal_raises(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.SELL)
    with pytest.raises(ValueError):
        lot_calculator.calculate(
            signal=signal,
            entry_price=Decimal("1000"),
            config=_cfg(),
        )


def test_zero_risk_config_rejects(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(max_risk_per_trade_pct=Decimal("0")),
    )
    assert result.passed is False
    assert result.reason == "invalid_risk_config"


def test_custom_min_lot_size(unified_signal_factory) -> None:  # type: ignore[no-untyped-def]
    signal = unified_signal_factory(action=Action.BUY, stop_loss_price=Decimal("950"))
    # raw = 400; min_lot=500 → floor(400/500)*500 = 0 → below
    result = lot_calculator.calculate(
        signal=signal,
        entry_price=Decimal("1000"),
        config=_cfg(min_lot_size=500),
    )
    assert result.passed is False
    assert result.reason == "below_min_lot"
