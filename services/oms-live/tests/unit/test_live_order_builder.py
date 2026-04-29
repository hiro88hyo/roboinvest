"""order_builder の単体テスト。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from oms_live._testing import make_order_request
from oms_live.order_builder import build_sendorder_payload
from trade_contracts.enums import OrderType, Side


def test_market_buy_payload() -> None:
    order = make_order_request(side=Side.BUY, quantity=300)
    payload = build_sendorder_payload(order, password="pw", exchange=1)
    assert payload == {
        "Symbol": order.symbol,
        "Exchange": 1,
        "SecurityType": 1,
        "Side": "2",
        "CashMargin": 1,
        "DelivType": 2,
        "AccountType": 4,
        "FundType": "AA",
        "Qty": 300,
        "FrontOrderType": 10,
        "Price": 0.0,
        "ExpireDay": 0,
        "Password": "pw",
    }


def test_market_sell_payload_uses_sell_side_and_blank_fund() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    payload = build_sendorder_payload(order, password="pw", exchange=1)
    assert payload["Side"] == "1"
    assert payload["DelivType"] == 0
    # 現物売却の規約値: 半角空白 2 文字
    assert payload["FundType"] == "  "
    assert payload["FrontOrderType"] == 10
    assert payload["Price"] == 0.0


def test_limit_buy_uses_limit_price_and_front_order_type_20() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1234"),
    )
    payload = build_sendorder_payload(order, password="pw", exchange=1)
    assert payload["FrontOrderType"] == 20
    assert payload["Price"] == 1234.0
    assert payload["Side"] == "2"


def test_limit_without_limit_price_raises() -> None:
    order = make_order_request(
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=None,
    )
    with pytest.raises(ValueError, match="limit_price"):
        build_sendorder_payload(order, password="pw", exchange=1)


def test_exchange_and_account_type_are_passed_through() -> None:
    order = make_order_request()
    payload = build_sendorder_payload(order, password="pw", exchange=3, account_type=2)
    assert payload["Exchange"] == 3
    assert payload["AccountType"] == 2


def test_password_is_included_verbatim() -> None:
    order = make_order_request()
    payload = build_sendorder_payload(order, password="my-secret", exchange=1)
    assert payload["Password"] == "my-secret"
