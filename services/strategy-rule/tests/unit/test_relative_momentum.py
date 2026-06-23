from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from strategy_rule.strategies.relative_momentum import RelativeMomentumStrategy
from trade_contracts.enums import Action
from trade_contracts.features import ProcessedFeatures


def _features(
    features_factory: Callable[..., ProcessedFeatures],
    **overrides: object,
) -> ProcessedFeatures:
    base = {
        "timestamp": datetime(2026, 6, 22, 9, 30, tzinfo=UTC),
        "price": Decimal("105"),
        "vwap": Decimal("104"),
        "return_from_open_bps": Decimal("500"),
        "intraday_peer_percentile": Decimal("0.9"),
        "intraday_high_price": Decimal("105"),
        "minutes_from_open": 30,
        "minutes_to_close": 360,
        "spread_bps": Decimal("10"),
        "spread_ticks": Decimal("1"),
        "ask_depth_5": 2000,
    }
    base.update(overrides)
    return features_factory(**base)


def test_emits_buy_for_relative_momentum(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy()
    signal = strategy.evaluate(_features(features_factory), {})

    assert signal is not None
    assert signal.action == Action.BUY
    assert signal.stop_loss_price == Decimal("104")
    assert signal.target_price == Decimal("106.5")
    assert "relative_momentum" in (signal.reasoning or "")


def test_requires_peer_percentile(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy(min_peer_percentile=Decimal("0.8"))

    signal = strategy.evaluate(
        _features(features_factory, intraday_peer_percentile=Decimal("0.7")),
        {},
    )

    assert signal is None


def test_requires_intraday_high_update(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy()

    signal = strategy.evaluate(
        _features(features_factory, price=Decimal("104"), intraday_high_price=Decimal("105")),
        {},
    )

    assert signal is None


def test_rejects_wide_vwap_stop_risk(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy(max_stop_risk_bps=Decimal("50"))

    signal = strategy.evaluate(
        _features(features_factory, price=Decimal("105"), vwap=Decimal("104")),
        {},
    )

    assert signal is None


def test_emits_only_once_per_symbol_per_day(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy()
    state: dict[str, object] = {}

    first = strategy.evaluate(_features(features_factory), state)
    second = strategy.evaluate(
        _features(
            features_factory,
            timestamp=datetime(2026, 6, 22, 9, 45, tzinfo=UTC),
            price=Decimal("106"),
            vwap=Decimal("105"),
            intraday_high_price=Decimal("106"),
        ),
        state,
    )

    assert first is not None
    assert second is None


def test_resets_on_new_date(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = RelativeMomentumStrategy()
    state: dict[str, object] = {}
    first = strategy.evaluate(_features(features_factory), state)
    second = strategy.evaluate(
        _features(
            features_factory,
            timestamp=datetime(2026, 6, 23, 9, 30, tzinfo=UTC) + timedelta(seconds=1),
            price=Decimal("106"),
            vwap=Decimal("105"),
            intraday_high_price=Decimal("106"),
        ),
        state,
    )

    assert first is not None
    assert second is not None
