"""parse_push_message の単体テスト。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from feeder._testing import make_book_payload, make_tick_payload
from feeder.parser import parse_push_message
from trade_contracts.market import OrderBookSnapshot, TickData

JST = timezone(timedelta(hours=9), name="JST")


def test_parse_tick_only() -> None:
    payload = make_tick_payload(symbol="7203", current_price=2500.5, trading_volume=300)
    results = parse_push_message(payload)

    assert len(results) == 1
    tick = results[0]
    assert isinstance(tick, TickData)
    assert tick.symbol == "7203"
    assert tick.price == Decimal("2500.5")
    assert tick.volume == 300
    assert tick.timestamp == datetime(2026, 4, 25, 9, 0, tzinfo=JST)


def test_parse_book_only() -> None:
    payload = make_book_payload(symbol="9984")
    payload.pop("CurrentPrice", None)

    results = parse_push_message(payload)

    assert len(results) == 1
    book = results[0]
    assert isinstance(book, OrderBookSnapshot)
    assert book.symbol == "9984"
    assert [level.price for level in book.bids] == [Decimal("999.0"), Decimal("998.0")]
    assert [level.quantity for level in book.bids] == [200, 500]
    assert [level.price for level in book.asks] == [Decimal("1000.0"), Decimal("1001.0")]


def test_parse_combined_message_returns_both() -> None:
    payload = {
        **make_tick_payload(symbol="7203", current_price=1000.0, trading_volume=200),
        **{f"Buy{i}": {"Price": 1000 - i, "Qty": 100} for i in range(1, 4)},
        **{f"Sell{i}": {"Price": 1000 + i, "Qty": 150} for i in range(1, 4)},
    }

    results = parse_push_message(payload)

    assert len(results) == 2
    assert isinstance(results[0], TickData)
    assert isinstance(results[1], OrderBookSnapshot)
    assert len(results[1].bids) == 3
    assert len(results[1].asks) == 3


def test_parse_book_skips_zero_quantity_levels() -> None:
    payload = make_book_payload(
        bids=[(999.0, 200), (998.0, 0)],
        asks=[(1000.0, 200), (1001.0, 100)],
    )
    payload.pop("CurrentPrice", None)
    # 特別気配などで Price 欠落のレベルもスキップされること
    payload["Sell3"] = {"Price": None, "Qty": 100}

    results = parse_push_message(payload)
    book = next(r for r in results if isinstance(r, OrderBookSnapshot))
    assert len(book.bids) == 1
    assert len(book.asks) == 2


def test_parse_returns_empty_when_symbol_missing() -> None:
    assert parse_push_message({"CurrentPrice": 1000}) == []


def test_parse_returns_empty_for_unrelated_payload() -> None:
    assert parse_push_message({"Symbol": "7203"}) == []


def test_parse_handles_naive_timestamp_as_jst() -> None:
    payload = make_tick_payload(current_price_time="2026-04-25T09:00:00")

    results = parse_push_message(payload)
    tick = results[0]
    assert isinstance(tick, TickData)
    assert tick.timestamp == datetime(2026, 4, 25, 9, 0, tzinfo=JST)


def test_parse_uses_string_decimal_to_avoid_float_drift() -> None:
    # JSON では float で来るため、文字列経由で Decimal 化する経路を保証する
    payload = make_tick_payload(current_price=0.1 + 0.2, trading_volume=10)

    results = parse_push_message(payload)
    tick = results[0]
    assert isinstance(tick, TickData)
    # str(0.1 + 0.2) は "0.30000000000000004"。Decimal 化後もこの正確な値を保つ
    assert tick.price == Decimal("0.30000000000000004")


def test_parse_negative_volume_is_clamped_to_zero() -> None:
    payload = make_tick_payload(trading_volume=-5)

    results = parse_push_message(payload)
    tick = results[0]
    assert isinstance(tick, TickData)
    assert tick.volume == 0


def test_parse_integer_symbol_is_normalized_to_string() -> None:
    payload = make_tick_payload()
    payload["Symbol"] = 7203

    results = parse_push_message(payload)
    assert results[0].symbol == "7203"


def test_parse_book_from_top_level_bid_ask_fallback() -> None:
    """BidPrice/AskPrice/BidQty/AskQty → 1-level OrderBookSnapshot (PUSH differential)."""
    payload = {
        "Symbol": "7203",
        "CurrentPriceTime": "2026-04-25T09:00:00+09:00",
        "BidPrice": 999.0,
        "BidQty": 200,
        "AskPrice": 1000.0,
        "AskQty": 150,
    }
    results = parse_push_message(payload)

    assert len(results) == 1
    book = results[0]
    assert isinstance(book, OrderBookSnapshot)
    assert book.symbol == "7203"
    assert len(book.bids) == 1
    assert book.bids[0].price == Decimal("999.0")
    assert book.bids[0].quantity == 200
    assert len(book.asks) == 1
    assert book.asks[0].price == Decimal("1000.0")
    assert book.asks[0].quantity == 150


def test_parse_book_fallback_ignores_zero_bid_price() -> None:
    """BidPrice=0 は気配なし扱いで book を生成しない。"""
    payload = {
        "Symbol": "7203",
        "CurrentPriceTime": "2026-04-25T09:00:00+09:00",
        "BidPrice": 0,
        "BidQty": 0,
        "AskPrice": 0,
        "AskQty": 0,
    }
    results = parse_push_message(payload)
    assert results == []


def test_parse_book_fallback_partial_bid_only() -> None:
    """BidPrice のみ存在し AskPrice がない場合は bids のみの OrderBookSnapshot。"""
    payload = {
        "Symbol": "7203",
        "CurrentPriceTime": "2026-04-25T09:00:00+09:00",
        "BidPrice": 999.0,
        "BidQty": 100,
    }
    results = parse_push_message(payload)

    assert len(results) == 1
    book = results[0]
    assert isinstance(book, OrderBookSnapshot)
    assert len(book.bids) == 1
    assert book.asks == []
