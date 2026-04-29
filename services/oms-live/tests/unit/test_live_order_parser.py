"""order_parser の単体テスト。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from oms_live._testing import make_kabu_order_payload
from oms_live.order_parser import parse_order_state, to_fill_result
from trade_contracts.enums import Side


def test_parse_order_state_full_filled() -> None:
    payload = make_kabu_order_payload(
        order_id="ID-1",
        side="2",
        order_qty=200,
        cum_qty=200,
        state=3,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-04-29T09:00:01+09:00",
                "Price": 1000,
                "Qty": 200,
            }
        ],
    )
    state = parse_order_state(payload)
    assert state.order_id == "ID-1"
    assert state.side is Side.BUY
    assert state.order_qty == 200
    assert state.cum_qty == 200
    assert state.state == 3
    assert state.details[0].price == Decimal("1000")
    assert state.details[0].quantity == 200

    fill = to_fill_result(state)
    assert fill.reason == "filled"
    assert fill.filled_quantity == 200
    assert fill.fill_price == Decimal("1000")


def test_to_fill_result_partial_uses_vwap_with_round_half_up() -> None:
    payload = make_kabu_order_payload(
        order_qty=300,
        cum_qty=200,
        state=3,
        details=[
            {"ExecutionID": "E-1", "ExecutionTime": None, "Price": 1000, "Qty": 100},
            {"ExecutionID": "E-2", "ExecutionTime": None, "Price": 1001, "Qty": 100},
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    # vwap = (1000*100 + 1001*100) / 200 = 1000.5 -> ROUND_HALF_UP -> 1001
    assert fill.reason == "partial"
    assert fill.filled_quantity == 200
    assert fill.fill_price == Decimal("1001")


def test_to_fill_result_pending_when_state_waiting() -> None:
    payload = make_kabu_order_payload(state=1, cum_qty=0, details=[])
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "pending"
    assert fill.filled_quantity == 0
    assert fill.fill_price is None


def test_to_fill_result_pending_when_state_processing() -> None:
    payload = make_kabu_order_payload(state=2, cum_qty=0, details=[])
    state = parse_order_state(payload)
    assert to_fill_result(state).reason == "pending"


def test_to_fill_result_cancelled_when_state_cancelling() -> None:
    payload = make_kabu_order_payload(state=5, cum_qty=0, details=[])
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "cancelled"
    assert fill.filled_quantity == 0
    assert fill.fill_price is None


def test_to_fill_result_done_with_zero_cum_qty_is_treated_as_cancelled() -> None:
    payload = make_kabu_order_payload(state=3, cum_qty=0, details=[])
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "cancelled"


def test_to_fill_result_uses_state_price_when_details_missing() -> None:
    payload = make_kabu_order_payload(
        state=3,
        cum_qty=100,
        order_qty=100,
        price=987.5,
        details=[],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    # Details が空でも cum_qty>0 なら state.price を fallback として使う
    assert fill.reason == "filled"
    assert fill.fill_price == Decimal("987.5")


def test_parse_order_state_sell_side_maps_correctly() -> None:
    payload = make_kabu_order_payload(side="1")
    state = parse_order_state(payload)
    assert state.side is Side.SELL


def test_parse_order_state_unknown_side_raises() -> None:
    payload = make_kabu_order_payload(side="9")
    with pytest.raises(ValueError, match="unexpected kabu Side"):
        parse_order_state(payload)


def test_parse_order_state_naive_recv_time_assumes_jst() -> None:
    payload = make_kabu_order_payload(recv_time="2026-04-29T09:00:00")
    state = parse_order_state(payload)
    assert state.recv_time is not None
    assert state.recv_time.tzinfo is not None
    # JST = UTC+9
    assert state.recv_time.utcoffset() is not None


def test_parse_order_state_decimal_from_float_avoids_precision_loss() -> None:
    payload = make_kabu_order_payload(
        state=3,
        cum_qty=100,
        order_qty=100,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": None,
                "Price": 1234.5,
                "Qty": 100,
            }
        ],
    )
    state = parse_order_state(payload)
    # str() 経由で Decimal 化していれば 1234.5 をそのまま保持できる
    assert state.details[0].price == Decimal("1234.5")
