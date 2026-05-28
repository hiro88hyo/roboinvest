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
            {"ExecutionID": "E-1", "ExecutionTime": None, "Price": 1000.005, "Qty": 100},
            {"ExecutionID": "E-2", "ExecutionTime": None, "Price": 1000.015, "Qty": 100},
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    # vwap = (1000.005*100 + 1000.015*100) / 200 = 1000.010 -> ROUND_HALF_UP -> 1000.01
    assert fill.reason == "partial"
    assert fill.filled_quantity == 200
    assert fill.fill_price == Decimal("1000.01")


def test_to_fill_result_preserves_subyen_fill_price() -> None:
    """NTT (9432) など 0.05/0.1 円刻みの実 fill 価格が落ちないことを保証する回帰テスト。

    2026-05-07 本番実機検証で 1 円丸め (Decimal("1")) のため
    BUY ¥151.50 → ¥152、SELL ¥151.45 → ¥151 と歪み、(151-152)*100 = -¥100 が
    daily_pnl に積まれた (実損は -¥5)。0.01 円丸めにより subyen fill が保持される。
    """
    payload = make_kabu_order_payload(
        state=5,
        cum_qty=100,
        order_qty=100,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-05-07T13:51:39+09:00",
                "Price": 151.45,
                "Qty": 100,
            }
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "filled"
    assert fill.fill_price == Decimal("151.45")


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


def test_to_fill_result_pending_when_state_amending() -> None:
    payload = make_kabu_order_payload(state=4, cum_qty=0, details=[])
    state = parse_order_state(payload)
    assert to_fill_result(state).reason == "pending"


def test_to_fill_result_cancelled_when_state_terminated_with_zero_cum_qty() -> None:
    """State=5 + CumQty=0 は取消完了 / 失効 (約定なしで終端)。"""
    payload = make_kabu_order_payload(state=5, cum_qty=0, details=[])
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "cancelled"
    assert fill.filled_quantity == 0
    assert fill.fill_price is None


def test_to_fill_result_filled_when_state_terminated_with_full_cum_qty() -> None:
    """State=5 + CumQty==OrderQty は約定完了で終端 (本番実機の通常パターン)。"""
    payload = make_kabu_order_payload(
        state=5,
        cum_qty=200,
        order_qty=200,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-05-07T09:00:01+09:00",
                "Price": 1500,
                "Qty": 200,
            }
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "filled"
    assert fill.filled_quantity == 200
    assert fill.fill_price == Decimal("1500")


def test_to_fill_result_partial_when_state_terminated_with_partial_cum_qty() -> None:
    """State=5 + 0<CumQty<OrderQty は部分約定で終端 (残数量は失効)。"""
    payload = make_kabu_order_payload(
        state=5,
        cum_qty=100,
        order_qty=300,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-05-07T09:00:01+09:00",
                "Price": 1500,
                "Qty": 100,
            }
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "partial"
    assert fill.filled_quantity == 100
    assert fill.fill_price == Decimal("1500")


def test_to_fill_result_pending_when_state_done_with_zero_cum_qty() -> None:
    """State=3 + CumQty=0 は「取引所に流れた中間状態」(Details RecType=1/4 のみ、
    約定 Detail RecType=8 未着)。Runner は poll を継続すべきなので pending を返す。

    2026-05-07 本番実機で発生した実害ケースの回帰テスト。
    """
    payload = make_kabu_order_payload(state=3, cum_qty=0, details=[])
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "pending"
    assert fill.filled_quantity == 0
    assert fill.fill_price is None


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


def test_parse_order_state_detail_accepts_transact_time() -> None:
    payload = make_kabu_order_payload(
        details=[
            {
                "ExecutionID": "ID-1",
                "TransactTime": "2026-05-28T15:30:01+09:00",
                "Price": 0,
                "Qty": 0,
                "RecType": 6,
            }
        ],
    )
    state = parse_order_state(payload)
    assert state.details[0].execution_time.isoformat() == "2026-05-28T15:30:01+09:00"
    assert state.details[0].rec_type == 6


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


def test_to_fill_result_excludes_non_fill_details_from_vwap() -> None:
    """RecType=1 (受付) / 4 (発注) の Price=0 を VWAP 計算から除外することを保証する。

    2026-05-07 本番 NTT 9432 成行 round_trip で発覚: kabu の Details に
    [RecType=1 Price=0 Qty=100, RecType=4 Price=0 Qty=100, RecType=8 Price=151.70 Qty=100]
    が入り、フィルタなしの VWAP は (0+0+15170)/300 = 50.57 と本来の 1/3 に
    希釈された。RecType=8 のみで VWAP を取れば正しく 151.70 になる。
    """
    payload = make_kabu_order_payload(
        state=5,
        cum_qty=100,
        order_qty=100,
        details=[
            {
                "ExecutionID": "ID-1",
                "ExecutionTime": "2026-05-07T14:04:29+09:00",
                "Price": 0,
                "Qty": 100,
                "RecType": 1,
            },
            {
                "ExecutionID": "ID-2",
                "ExecutionTime": "2026-05-07T14:04:29+09:00",
                "Price": 0,
                "Qty": 100,
                "RecType": 4,
            },
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-05-07T14:04:29+09:00",
                "Price": 151.70,
                "Qty": 100,
                "RecType": 8,
            },
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "filled"
    assert fill.fill_price == Decimal("151.70")


def test_to_fill_result_vwap_backward_compat_when_rec_type_missing() -> None:
    """RecType を含まないレガシーな Details (rec_type=0) も VWAP に含めて壊さない。

    既存テストや fixture がペイロードに ``RecType`` を渡さないケースを保護する。
    """
    payload = make_kabu_order_payload(
        state=5,
        cum_qty=100,
        order_qty=100,
        details=[
            {
                "ExecutionID": "E-1",
                "ExecutionTime": "2026-05-07T14:04:29+09:00",
                "Price": 1234.5,
                "Qty": 100,
            }
        ],
    )
    state = parse_order_state(payload)
    fill = to_fill_result(state)
    assert fill.reason == "filled"
    assert fill.fill_price == Decimal("1234.50")


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
