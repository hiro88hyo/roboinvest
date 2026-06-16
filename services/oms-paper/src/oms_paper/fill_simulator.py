"""擬似約定 (純関数)。

``OrderRequest`` と ``OrderBookSnapshot`` から ``FillResult`` を返す。
価格計算は ``Decimal``、最終 VWAP は ``ROUND_HALF_UP`` で 1 円単位 (市場慣習) に揃える。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from trade_contracts.enums import OrderType, Side
from trade_contracts.market import OrderBookSnapshot, PriceLevel
from trade_contracts.order import OrderRequest

from .models import FillResult

_PRICE_QUANT = Decimal("1")


def _consume_levels(
    levels: Sequence[PriceLevel],
    needed: int,
) -> tuple[int, list[tuple[Decimal, int]]]:
    consumed: list[tuple[Decimal, int]] = []
    remaining = needed
    for lvl in levels:
        if remaining <= 0:
            break
        take = min(lvl.quantity, remaining)
        if take <= 0:
            continue
        consumed.append((lvl.price, take))
        remaining -= take
    filled = needed - remaining
    return filled, consumed


def _limit_crossing_levels(
    *,
    order: OrderRequest,
    levels: Sequence[PriceLevel],
) -> list[PriceLevel]:
    if order.order_type is OrderType.MARKET:
        return list(levels)
    if order.limit_price is None:
        return []
    if order.side is Side.BUY:
        return [level for level in levels if level.price <= order.limit_price]
    return [level for level in levels if level.price >= order.limit_price]


def _vwap(consumed: Sequence[tuple[Decimal, int]]) -> Decimal:
    total_qty = sum(qty for _, qty in consumed)
    total_value = sum((price * qty for price, qty in consumed), Decimal("0"))
    raw = total_value / Decimal(total_qty)
    return raw.quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)


def simulate_fill(
    *,
    order: OrderRequest,
    book: OrderBookSnapshot,
) -> FillResult:
    """注文を板情報で擬似約定する。

    BUY は asks (安値から) を、SELL は bids (高値から) を上から消費する。
    LIMIT は約定可能な価格帯の板だけを消費し、届かなければ no-fill にする。
    部分約定時も VWAP を返し、``reason`` で全約定 / 部分約定を区別する。
    """

    if order.symbol != book.symbol:
        return FillResult(filled_quantity=0, fill_price=None, reason="symbol_mismatch")
    if order.order_type is OrderType.LIMIT and order.limit_price is None:
        return FillResult(filled_quantity=0, fill_price=None, reason="missing_limit_price")

    levels: Sequence[PriceLevel] = book.asks if order.side is Side.BUY else book.bids
    if not levels:
        return FillResult(filled_quantity=0, fill_price=None, reason="empty_book")

    crossing_levels = _limit_crossing_levels(order=order, levels=levels)
    if not crossing_levels:
        reason = "limit_not_crossed" if order.order_type is OrderType.LIMIT else "no_liquidity"
        return FillResult(filled_quantity=0, fill_price=None, reason=reason)

    filled_qty, consumed = _consume_levels(crossing_levels, order.quantity)
    if filled_qty == 0 or not consumed:
        return FillResult(filled_quantity=0, fill_price=None, reason="no_liquidity")

    vwap = _vwap(consumed)
    reason = "filled" if filled_qty == order.quantity else "partial"
    return FillResult(filled_quantity=filled_qty, fill_price=vwap, reason=reason)
