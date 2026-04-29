"""Phase 1 注文ペイロード生成 (純関数)。

``OrderRequest`` を kabu ``/sendorder`` 用の JSON 互換 dict に変換する。
現物株前提 (``CashMargin=1``, ``SecurityType=1``)。

kabu API の数値フィールド (``Price``) は仕様上 number 型なので、ここでは
``Decimal`` を ``float`` に落として返す (Decimal のまま渡すと httpx の
JSON エンコーダが文字列化して 4xx になる)。Decimal -> float の精度落ちを
避けるため呼び出し側 (Aggregator / Gateway) は呼び値単位で量子化済みの
``limit_price`` を渡してくる前提。
"""

from __future__ import annotations

from typing import Any

from trade_contracts.enums import OrderType, Side
from trade_contracts.order import OrderRequest


def build_sendorder_payload(
    order: OrderRequest,
    *,
    password: str,
    exchange: int,
    account_type: int = 4,
) -> dict[str, Any]:
    """``OrderRequest`` を kabu ``/sendorder`` 用 dict に変換する。

    現物株のみサポート:
    - BUY: ``Side="2"``, ``DelivType=2`` (お預り), ``FundType="AA"`` (現物買付)
    - SELL: ``Side="1"``, ``DelivType=0`` (現物売却時は不要), ``FundType="  "``
      (半角空白 2 つ, 現物売却の規約値)

    ``OrderType.MARKET`` -> ``FrontOrderType=10``, ``Price=0``
    ``OrderType.LIMIT`` -> ``FrontOrderType=20``, ``Price=order.limit_price``
    """

    if order.side is Side.BUY:
        side_code = "2"
        deliv_type = 2
        fund_type = "AA"
    else:
        side_code = "1"
        deliv_type = 0
        fund_type = "  "

    if order.order_type is OrderType.MARKET:
        front_order_type = 10
        price: float = 0.0
    else:
        front_order_type = 20
        if order.limit_price is None:
            raise ValueError("limit_price is required for LIMIT order")
        price = float(order.limit_price)

    return {
        "Symbol": order.symbol,
        "Exchange": exchange,
        "SecurityType": 1,
        "Side": side_code,
        "CashMargin": 1,
        "DelivType": deliv_type,
        "AccountType": account_type,
        "FundType": fund_type,
        "Qty": order.quantity,
        "FrontOrderType": front_order_type,
        "Price": price,
        "ExpireDay": 0,
        "Password": password,
    }
