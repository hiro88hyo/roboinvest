from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from strategy_rule.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from trade_contracts.enums import Action
from trade_contracts.features import ProcessedFeatures


def test_emits_buy_on_vwap_aligned_breakout(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy(
        range_minutes=15,
        entry_minute=15,
        min_minutes_to_close=45,
        max_stop_risk_bps=Decimal("300"),
        require_vwap=True,
        target_r_multiple=Decimal("1.5"),
    )
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
            trade_volume_delta=None,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=10),
            price=Decimal("105"),
            vwap=Decimal("102"),
            minutes_from_open=10,
            minutes_to_close=380,
            trade_volume_delta=1000,
        ),
        state,
    )

    signal = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=15,
            minutes_to_close=375,
            spread_bps=Decimal("10"),
            spread_ticks=Decimal("1"),
            ask_depth_5=2000,
            trade_volume_delta=500,
        ),
        state,
    )

    assert signal is not None
    assert signal.action == Action.BUY
    assert signal.stop_loss_price == Decimal("104")
    assert signal.target_price == Decimal("109.0")
    assert "opening_range_breakout" in (signal.reasoning or "")


def test_requires_cross_from_below(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy()
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=1),
            price=Decimal("105"),
            vwap=Decimal("102"),
            minutes_from_open=1,
            minutes_to_close=389,
        ),
        state,
    )

    first = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=15,
            minutes_to_close=375,
        ),
        state,
    )
    second = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=16),
            price=Decimal("107"),
            vwap=Decimal("105"),
            minutes_from_open=16,
            minutes_to_close=374,
        ),
        state,
    )

    assert first is not None
    assert second is None


def test_emits_only_once_per_symbol_per_day(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy()
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=10),
            price=Decimal("105"),
            vwap=Decimal("102"),
            minutes_from_open=10,
            minutes_to_close=380,
        ),
        state,
    )

    first = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=15,
            minutes_to_close=375,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=20),
            price=Decimal("104"),
            vwap=Decimal("103"),
            minutes_from_open=20,
            minutes_to_close=370,
        ),
        state,
    )
    second = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=21),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=21,
            minutes_to_close=369,
        ),
        state,
    )

    assert first is not None
    assert second is None


def test_rejects_when_price_below_vwap(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy(require_vwap=True)
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=10),
            price=Decimal("105"),
            vwap=Decimal("102"),
            minutes_from_open=10,
            minutes_to_close=380,
        ),
        state,
    )

    signal = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("107"),
            minutes_from_open=15,
            minutes_to_close=375,
        ),
        state,
    )

    assert signal is None


def test_rejects_when_stop_risk_is_too_wide(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy(max_stop_risk_bps=Decimal("100"))
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=10),
            price=Decimal("105"),
            vwap=Decimal("100"),
            minutes_from_open=10,
            minutes_to_close=380,
        ),
        state,
    )

    signal = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("100"),
            minutes_from_open=15,
            minutes_to_close=375,
        ),
        state,
    )

    assert signal is None


def test_can_require_volume_delta_and_opening_volume(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy(
        min_breakout_volume_delta=500,
        min_opening_range_volume=1000,
    )
    state: dict[str, object] = {}
    base = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=base,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
            trade_volume_delta=None,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=10),
            price=Decimal("105"),
            vwap=Decimal("102"),
            minutes_from_open=10,
            minutes_to_close=380,
            trade_volume_delta=900,
        ),
        state,
    )

    rejected = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=15),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=15,
            minutes_to_close=375,
            trade_volume_delta=400,
        ),
        state,
    )
    state["previous_price"] = Decimal("105")
    accepted = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=16),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=16,
            minutes_to_close=374,
            trade_volume_delta=500,
        ),
        state,
    )

    assert rejected is None
    assert accepted is None

    state["previous_price"] = Decimal("105")
    state["opening_volume"] = 1000
    accepted = strategy.evaluate(
        features_factory(
            timestamp=base + timedelta(minutes=17),
            price=Decimal("106"),
            vwap=Decimal("104"),
            minutes_from_open=17,
            minutes_to_close=373,
            trade_volume_delta=500,
        ),
        state,
    )
    assert accepted is not None


def test_resets_opening_range_on_new_date(
    features_factory: Callable[..., ProcessedFeatures],
) -> None:
    strategy = OpeningRangeBreakoutStrategy()
    state: dict[str, object] = {}
    first_day = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    next_day = datetime(2026, 6, 23, 9, 0, tzinfo=UTC)

    strategy.evaluate(
        features_factory(
            timestamp=first_day,
            price=Decimal("100"),
            vwap=Decimal("100"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )
    strategy.evaluate(
        features_factory(
            timestamp=next_day,
            price=Decimal("200"),
            vwap=Decimal("200"),
            minutes_from_open=0,
            minutes_to_close=390,
        ),
        state,
    )

    assert state["opening_high"] == Decimal("200")
    assert state["opening_low"] == Decimal("200")
