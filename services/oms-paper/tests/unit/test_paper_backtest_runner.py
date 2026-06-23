from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from oms_paper._testing import DEFAULT_TS, make_order_book, make_order_request, make_paper_position
from oms_paper.backtest.runner import run_backtest
from trade_contracts.enums import Side, TradingStyle


def _ts(seconds: int) -> datetime:
    return DEFAULT_TS + timedelta(seconds=seconds)


def test_no_book_yet_for_symbol_results_in_no_fill() -> None:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(0))
    summary = run_backtest(orders=[order], books=[])
    assert summary.fill_count == 0
    assert summary.no_fill_count == 1
    assert summary.no_fills[0].reason == "no_book"
    assert summary.final_positions == {}


def test_book_received_before_order_enables_fill() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[order], books=[book])
    assert summary.fill_count == 1
    assert summary.fills[0].price == Decimal("1000")
    assert summary.fills[0].quantity == 100
    assert summary.no_fill_count == 0
    assert "7203" in summary.final_positions
    assert summary.final_positions["7203"].quantity == 100
    assert len(summary.execution_quality) == 1
    quality = summary.execution_quality[0]
    assert quality.fill_ratio == Decimal("1")
    assert quality.opposite_depth_quantity == 200
    assert quality.same_side_depth_quantity == 700
    assert quality.spread_bps == Decimal("10.00500250125062531265632816")
    assert quality.tick_size == Decimal("1")
    assert quality.spread_ticks == Decimal("1")


def test_book_at_same_timestamp_applies_before_order() -> None:
    # 同時刻の book と order が来た場合、book が先に処理されて約定する
    book = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(0))
    summary = run_backtest(orders=[order], books=[book])
    assert summary.fill_count == 1
    assert summary.no_fill_count == 0


def test_later_book_overwrites_cache() -> None:
    book1 = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    book2 = make_order_book(symbol="7203", asks=(("1100", 500),), timestamp=_ts(2))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(3))
    summary = run_backtest(orders=[order], books=[book1, book2])
    assert summary.fill_count == 1
    assert summary.fills[0].price == Decimal("1100")  # 最新板で約定


def test_buy_then_sell_full_close() -> None:
    book_buy = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    book_sell = make_order_book(symbol="7203", bids=(("1100", 200),), timestamp=_ts(2))
    buy = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    sell = make_order_request(symbol="7203", side=Side.SELL, quantity=100, created_at=_ts(3))
    summary = run_backtest(orders=[buy, sell], books=[book_buy, book_sell])
    assert summary.fill_count == 2
    assert summary.no_fill_count == 0
    assert summary.final_positions == {}  # SELL で全決済 → DELETE
    assert summary.realized_pnl == Decimal("9792.1")


def test_initial_positions_seed_used_for_sell() -> None:
    seed = {"7203": make_paper_position(symbol="7203", quantity=200, entry_price=Decimal("900"))}
    book = make_order_book(symbol="7203", bids=(("1000", 500),), timestamp=_ts(0))
    sell = make_order_request(symbol="7203", side=Side.SELL, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[sell], books=[book], initial_positions=seed)
    assert summary.fill_count == 1
    assert summary.final_positions["7203"].quantity == 100  # 200 - 100
    assert summary.realized_pnl == Decimal("9811.9")


def test_open_buy_does_not_count_as_realized_pnl() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    buy = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[buy], books=[book])
    assert summary.fill_count == 1
    assert summary.realized_pnl == Decimal("0")


def test_oversell_logged_as_no_fill_and_position_unchanged() -> None:
    seed = {"7203": make_paper_position(symbol="7203", quantity=100, entry_price=Decimal("1000"))}
    book = make_order_book(symbol="7203", bids=(("1000", 500),), timestamp=_ts(0))
    sell = make_order_request(symbol="7203", side=Side.SELL, quantity=200, created_at=_ts(1))
    summary = run_backtest(orders=[sell], books=[book], initial_positions=seed)
    assert summary.fill_count == 0
    assert summary.no_fill_count == 1
    assert summary.no_fills[0].reason == "oversell"
    assert summary.final_positions["7203"].quantity == 100  # 不変


def test_sell_without_position_logged_as_no_fill() -> None:
    book = make_order_book(symbol="7203", bids=(("1000", 200),), timestamp=_ts(0))
    sell = make_order_request(symbol="7203", side=Side.SELL, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[sell], books=[book])
    assert summary.fill_count == 0
    assert summary.no_fill_count == 1
    assert summary.no_fills[0].reason == "no_position_for_sell"


def test_partial_fill_recorded_with_filled_quantity() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 50),), timestamp=_ts(0))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=200, created_at=_ts(1))
    summary = run_backtest(orders=[order], books=[book])
    assert summary.fill_count == 1
    assert summary.fills[0].quantity == 50
    assert summary.final_positions["7203"].quantity == 50
    assert summary.execution_quality[0].fill_ratio == Decimal("0.25")
    assert summary.execution_quality[0].reason == "partial"


def test_default_holding_type_applied_to_new_buy() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 500),), timestamp=_ts(0))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[order], books=[book], default_holding_type=TradingStyle.SWING)
    assert summary.final_positions["7203"].holding_type is TradingStyle.SWING


def test_buy_carries_order_management_fields_to_new_position() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 500),), timestamp=_ts(0))
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
        trailing_stop_pct=Decimal("0.03"),
        max_hold_days=3,
        created_at=_ts(1),
    )
    summary = run_backtest(orders=[order], books=[book])

    position = summary.final_positions["7203"]
    assert position.stop_loss_price == Decimal("950")
    assert position.target_price == Decimal("1100")
    assert position.trailing_stop_pct == Decimal("0.03")
    assert position.max_hold_days == 3


def test_day_target_exit_runs_on_later_book() -> None:
    book_buy = make_order_book(symbol="7203", asks=(("1000", 500),), timestamp=_ts(0))
    book_target = make_order_book(symbol="7203", bids=(("1100", 500),), timestamp=_ts(2))
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        target_price=Decimal("1100"),
        stop_loss_price=Decimal("950"),
        created_at=_ts(1),
    )

    summary = run_backtest(orders=[order], books=[book_buy, book_target])

    assert summary.fill_count == 2
    assert summary.final_positions == {}
    assert len(summary.closed_trades) == 1
    assert summary.closed_trades[0].exit_price == Decimal("1100")
    assert summary.realized_pnl == Decimal("9792.1")


def test_day_stop_exit_runs_on_later_book() -> None:
    book_buy = make_order_book(symbol="7203", asks=(("1000", 500),), timestamp=_ts(0))
    book_stop = make_order_book(symbol="7203", bids=(("950", 500),), timestamp=_ts(2))
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        target_price=Decimal("1100"),
        stop_loss_price=Decimal("950"),
        created_at=_ts(1),
    )

    summary = run_backtest(orders=[order], books=[book_buy, book_stop])

    assert summary.fill_count == 2
    assert summary.final_positions == {}
    assert len(summary.closed_trades) == 1
    assert summary.closed_trades[0].exit_price == Decimal("950")
    assert summary.realized_pnl == Decimal("-5193.05")


def test_existing_position_holding_type_preserved_on_buy_average() -> None:
    seed = {
        "7203": make_paper_position(
            symbol="7203",
            quantity=100,
            entry_price=Decimal("1000"),
            holding_type=TradingStyle.SWING,
        )
    }
    book = make_order_book(symbol="7203", asks=(("1100", 500),), timestamp=_ts(0))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    summary = run_backtest(
        orders=[order],
        books=[book],
        initial_positions=seed,
        default_holding_type=TradingStyle.DAY,  # 既存 SWING を上書きしない
    )
    assert summary.final_positions["7203"].holding_type is TradingStyle.SWING
    assert summary.final_positions["7203"].quantity == 200


def test_multi_symbol_independent_books() -> None:
    book_a = make_order_book(symbol="A", asks=(("1000", 200),), timestamp=_ts(0))
    book_b = make_order_book(symbol="B", asks=(("2000", 200),), timestamp=_ts(0))
    order_a = make_order_request(symbol="A", side=Side.BUY, quantity=100, created_at=_ts(1))
    order_b = make_order_request(symbol="B", side=Side.BUY, quantity=100, created_at=_ts(2))
    summary = run_backtest(orders=[order_a, order_b], books=[book_a, book_b])
    assert summary.fill_count == 2
    assert summary.final_positions["A"].entry_price == Decimal("1000")
    assert summary.final_positions["B"].entry_price == Decimal("2000")


def test_orders_processed_in_timestamp_order_regardless_of_input_order() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200), ("1010", 200)), timestamp=_ts(0))
    # 入力順は逆だが、created_at の小さい方が先に処理される
    later = make_order_request(symbol="7203", side=Side.BUY, quantity=200, created_at=_ts(2))
    earlier = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1))
    summary = run_backtest(orders=[later, earlier], books=[book])
    # 1 つ目の約定 (earlier, 100 株) は 1000 のみ消費。2 つ目 (later, 200 株) は VWAP で 1005
    # ただしこの runner は board_cache を「タイムスタンプ順」で見て約定するが、
    # 板を消費する仕組みは無い (各注文は同じ静的 board に対して独立に約定する)
    # — Phase 2 は「最新板スナップショット」基準なので OK
    assert summary.fill_count == 2
    assert summary.fills[0].quantity == 100  # earlier が先
    assert summary.fills[1].quantity == 200


def test_summary_counts_match_input() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),), timestamp=_ts(0))
    orders = [
        make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=_ts(1)),
        make_order_request(
            symbol="9984", side=Side.BUY, quantity=100, created_at=_ts(2)
        ),  # no_book
    ]
    summary = run_backtest(orders=orders, books=[book])
    assert summary.order_count == 2
    assert summary.fill_count + summary.no_fill_count == 2


def test_empty_inputs_produce_empty_summary() -> None:
    summary = run_backtest(orders=[], books=[])
    assert summary.fill_count == 0
    assert summary.no_fill_count == 0
    assert summary.final_positions == {}
