from __future__ import annotations

from decimal import Decimal

from oms_paper._testing import make_order_book, make_order_request
from oms_paper.fill_simulator import simulate_fill
from trade_contracts.enums import OrderType, Side


def test_buy_fully_filled_at_best_ask() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    book = make_order_book(asks=(("1000", 200), ("1001", 500)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.filled_quantity == 100
    assert result.fill_price == Decimal("1000")


def test_buy_partial_when_book_exhausted() -> None:
    order = make_order_request(side=Side.BUY, quantity=1000)
    book = make_order_book(asks=(("1000", 200), ("1001", 300)))
    # filled=500, vwap=(1000*200+1001*300)/500=500_300/500=1000.6 -> ROUND_HALF_UP -> 1001
    result = simulate_fill(order=order, book=book)
    assert result.reason == "partial"
    assert result.filled_quantity == 500
    assert result.fill_price == Decimal("1001")


def test_buy_vwap_walks_multiple_levels() -> None:
    order = make_order_request(side=Side.BUY, quantity=300)
    book = make_order_book(asks=(("1000", 200), ("1010", 500)))
    # consumed: (1000,200)+(1010,100) -> 200_000+101_000 = 301_000 / 300 = 1003.33 -> 1003
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.filled_quantity == 300
    assert result.fill_price == Decimal("1003")


def test_buy_vwap_round_half_up() -> None:
    order = make_order_request(side=Side.BUY, quantity=200)
    book = make_order_book(asks=(("1000", 100), ("1001", 100)))
    # vwap = (100_000 + 100_100)/200 = 1000.5 -> ROUND_HALF_UP -> 1001
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.fill_price == Decimal("1001")


def test_sell_fully_filled_at_best_bid() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    book = make_order_book(bids=(("999", 200), ("998", 500)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.filled_quantity == 100
    assert result.fill_price == Decimal("999")


def test_sell_partial_when_bids_exhausted() -> None:
    order = make_order_request(side=Side.SELL, quantity=1000)
    book = make_order_book(bids=(("999", 200), ("998", 300)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "partial"
    assert result.filled_quantity == 500


def test_buy_empty_asks() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    book = make_order_book(asks=())
    result = simulate_fill(order=order, book=book)
    assert result.reason == "empty_book"
    assert result.filled_quantity == 0
    assert result.fill_price is None


def test_sell_empty_bids() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    book = make_order_book(bids=())
    result = simulate_fill(order=order, book=book)
    assert result.reason == "empty_book"


def test_zero_quantity_levels_treated_as_no_liquidity() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    book = make_order_book(asks=(("1000", 0), ("1001", 0)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "no_liquidity"
    assert result.filled_quantity == 0


def test_buy_limit_fills_when_ask_crosses_limit() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1000"),
    )
    book = make_order_book(asks=(("999", 50), ("1000", 100), ("1001", 100)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.filled_quantity == 100
    assert result.fill_price == Decimal("1000")


def test_buy_limit_not_crossed_when_best_ask_above_limit() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("999"),
    )
    result = simulate_fill(order=order, book=make_order_book(asks=(("1000", 200),)))
    assert result.reason == "limit_not_crossed"
    assert result.filled_quantity == 0
    assert result.fill_price is None


def test_buy_limit_partial_when_crossing_depth_exhausted() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=300,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1000"),
    )
    book = make_order_book(asks=(("999", 100), ("1000", 100), ("1001", 500)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "partial"
    assert result.filled_quantity == 200
    assert result.fill_price == Decimal("1000")


def test_sell_limit_fills_when_bid_crosses_limit() -> None:
    order = make_order_request(
        side=Side.SELL,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("999"),
    )
    book = make_order_book(bids=(("1000", 50), ("999", 100), ("998", 100)))
    result = simulate_fill(order=order, book=book)
    assert result.reason == "filled"
    assert result.filled_quantity == 100
    assert result.fill_price == Decimal("1000")


def test_limit_without_price_rejects() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=None,
    )
    result = simulate_fill(order=order, book=make_order_book())
    assert result.reason == "missing_limit_price"
    assert result.filled_quantity == 0


def test_symbol_mismatch() -> None:
    order = make_order_request(symbol="7203")
    book = make_order_book(symbol="9984")
    result = simulate_fill(order=order, book=book)
    assert result.reason == "symbol_mismatch"
    assert result.filled_quantity == 0
