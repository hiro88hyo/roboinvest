from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from feature_engine.config import FeatureEngineSettings
from feature_engine.streaming.feature_state import (
    StreamingFeatureState,
    _default_buffer_size,
)
from trade_contracts.market import OrderBookSnapshot, PriceLevel, TickData


def _settings() -> FeatureEngineSettings:
    return FeatureEngineSettings(
        indicator_sma_short_window=3,
        indicator_sma_long_window=5,
        indicator_rsi_period=3,
        indicator_vwap_window=3,
        indicator_volume_ratio_window=3,
        indicator_bollinger_period=3,
        indicator_bollinger_stddev=2.0,
    )


def _tick(symbol: str, ts: datetime, price: str, volume: int = 100) -> TickData:
    return TickData(symbol=symbol, timestamp=ts, price=Decimal(price), volume=volume)


def test_default_buffer_size_uses_max_window_times_two() -> None:
    s = FeatureEngineSettings(
        indicator_sma_long_window=25,
        indicator_rsi_period=14,
        indicator_vwap_window=20,
        indicator_bollinger_period=20,
    )
    assert _default_buffer_size(s) == 50


def test_from_settings_rejects_zero_buffer() -> None:
    with pytest.raises(ValueError):
        StreamingFeatureState.from_settings(_settings(), buffer_size=0)


def test_first_tick_returns_features_with_null_indicators() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    t0 = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    features = state.record_tick(_tick("7203", t0, "2500"))

    assert features.symbol == "7203"
    assert features.timestamp == t0
    assert features.price == Decimal("2500")
    # バッファ 1 本ではどの指標もウォームアップ未達
    assert features.sma_short is None
    assert features.sma_long is None
    assert features.rsi is None
    assert features.bollinger_middle is None
    assert features.volume_ratio is None
    assert features.order_book is None


def test_sma_short_fills_after_window_reached() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    state.record_tick(_tick("7203", base, "100"))
    state.record_tick(_tick("7203", base + timedelta(seconds=1), "200"))
    features = state.record_tick(_tick("7203", base + timedelta(seconds=2), "300"))

    # sma_short(3) = (100 + 200 + 300) / 3 = 200
    assert features.sma_short == Decimal("200.0")
    # sma_long(5) はまだ 3 本しかないので null
    assert features.sma_long is None


def test_volume_ratio_fills_after_window_reached() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    state.record_tick(_tick("7203", base, "100", volume=100))
    state.record_tick(_tick("7203", base + timedelta(seconds=1), "100", volume=100))
    features = state.record_tick(_tick("7203", base + timedelta(seconds=2), "100", volume=400))

    assert features.volume_ratio == Decimal("2.0")


def test_buffer_rolls_after_exceeding_size() -> None:
    state = StreamingFeatureState.from_settings(_settings(), buffer_size=3)
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    for i, price in enumerate(["100", "200", "300", "400"]):
        state.record_tick(_tick("7203", base + timedelta(seconds=i), price))
    # buffer_size=3 なので最新 3 本 (200, 300, 400) のみ保持
    assert state.buffer_length("7203") == 3


def test_per_symbol_state_is_isolated() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    # 7203 を 3 本、9984 を 1 本
    for i, price in enumerate(["100", "200", "300"]):
        state.record_tick(_tick("7203", base + timedelta(seconds=i), price))
    state.record_tick(_tick("9984", base, "8000"))

    assert state.buffer_length("7203") == 3
    assert state.buffer_length("9984") == 1


def test_order_book_snapshot_is_attached_to_subsequent_tick() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    ts = datetime(2026, 4, 20, 0, 15, tzinfo=UTC)
    book = OrderBookSnapshot(
        symbol="7203",
        timestamp=ts,
        bids=[
            PriceLevel(price=Decimal("2499"), quantity=100),
            PriceLevel(price=Decimal("2498"), quantity=300),
        ],
        asks=[
            PriceLevel(price=Decimal("2501"), quantity=200),
            PriceLevel(price=Decimal("2502"), quantity=400),
        ],
    )
    state.record_order_book(book)
    features = state.record_tick(_tick("7203", ts, "2500"))
    assert features.order_book is not None
    assert features.order_book.symbol == "7203"
    assert features.order_book.bids[0].price == Decimal("2499")
    assert features.best_bid == Decimal("2499")
    assert features.best_ask == Decimal("2501")
    assert features.bid_depth_1 == 100
    assert features.ask_depth_1 == 200
    assert features.bid_depth_5 == 400
    assert features.ask_depth_5 == 600
    assert features.book_imbalance_5 == Decimal("-0.2")
    assert features.spread_bps == Decimal("8.00")
    assert features.tick_size == Decimal("1")
    assert features.spread_ticks == Decimal("2")
    assert features.minutes_from_open == 15
    assert features.minutes_to_close == 375
    assert features.session_phase == "morning"


def test_order_book_is_not_shared_across_symbols() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    ts = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    book = OrderBookSnapshot(
        symbol="7203",
        timestamp=ts,
        bids=[PriceLevel(price=Decimal("2499"), quantity=100)],
        asks=[PriceLevel(price=Decimal("2501"), quantity=200)],
    )
    state.record_order_book(book)
    features = state.record_tick(_tick("9984", ts, "8000"))
    assert features.order_book is None
    assert features.best_bid is None
    assert features.spread_bps is None


def test_reset_all_clears_ticks_and_books() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    ts = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    state.record_tick(_tick("7203", ts, "100"))
    state.record_order_book(
        OrderBookSnapshot(
            symbol="7203",
            timestamp=ts,
            bids=[PriceLevel(price=Decimal("99"), quantity=10)],
            asks=[PriceLevel(price=Decimal("101"), quantity=10)],
        )
    )
    state.reset()
    assert state.buffer_length("7203") == 0
    features = state.record_tick(_tick("7203", ts, "100"))
    assert features.order_book is None


def test_reset_single_symbol_preserves_others() -> None:
    state = StreamingFeatureState.from_settings(_settings())
    ts = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    state.record_tick(_tick("7203", ts, "100"))
    state.record_tick(_tick("9984", ts, "8000"))
    state.reset("7203")
    assert state.buffer_length("7203") == 0
    assert state.buffer_length("9984") == 1


def test_bollinger_middle_matches_sma_of_same_window() -> None:
    # bollinger_period = 3, so middle = SMA(3). With monotonic prices it converges cleanly.
    state = StreamingFeatureState.from_settings(_settings())
    base = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    state.record_tick(_tick("7203", base, "100"))
    state.record_tick(_tick("7203", base + timedelta(seconds=1), "200"))
    features = state.record_tick(_tick("7203", base + timedelta(seconds=2), "300"))
    # middle = 200, and upper > middle > lower by num_std * std
    assert features.bollinger_middle == Decimal("200.0")
    assert features.bollinger_upper is not None
    assert features.bollinger_lower is not None
    assert features.bollinger_upper > features.bollinger_middle > features.bollinger_lower
