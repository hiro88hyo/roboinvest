"""OrderRequest 組み立て(純関数)。

バリデーション済みの ``UnifiedTradeSignal`` と ``adjusted_quantity`` を結合し、
``OrderRequest`` を生成する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.enums import Action, OrderType, Side, TradeMode
from trade_contracts.order import OrderRequest
from trade_contracts.signal import UnifiedTradeSignal
from trade_contracts.tick_size import tse_tick_size


def _side_for(action: Action) -> Side:
    if action is Action.BUY:
        return Side.BUY
    if action is Action.SELL:
        return Side.SELL
    raise ValueError(f"cannot build OrderRequest for action={action}")


def build(
    *,
    signal: UnifiedTradeSignal,
    quantity: int,
    trade_mode: TradeMode,
    entry_price: Decimal | None = None,
    buy_limit_offset_ticks: int = 0,
    default_stop_loss_spread_pct: Decimal | None = None,
    created_at: datetime | None = None,
) -> OrderRequest:
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got: {quantity}")
    order_type, limit_price = _order_type_and_limit_price(
        signal=signal,
        entry_price=entry_price,
        buy_limit_offset_ticks=buy_limit_offset_ticks,
    )
    stop_loss_price = _stop_loss_price(
        signal=signal,
        entry_price=entry_price,
        default_stop_loss_spread_pct=default_stop_loss_spread_pct,
    )
    return OrderRequest(
        unified_signal_id=signal.signal_id,
        symbol=signal.symbol,
        side=_side_for(signal.action),
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        trade_mode=trade_mode,
        signal_source=signal.signal_source,
        stop_loss_price=stop_loss_price,
        target_price=signal.target_price,
        trailing_stop_pct=signal.trailing_stop_pct,
        max_hold_days=signal.max_hold_days,
        created_at=created_at or datetime.now(UTC),
    )


def _order_type_and_limit_price(
    *,
    signal: UnifiedTradeSignal,
    entry_price: Decimal | None,
    buy_limit_offset_ticks: int,
) -> tuple[OrderType, Decimal | None]:
    if signal.action is not Action.BUY:
        return OrderType.MARKET, None
    if entry_price is None or entry_price <= 0:
        raise ValueError("entry_price is required for BUY LIMIT order")
    if buy_limit_offset_ticks <= 0:
        return OrderType.LIMIT, entry_price
    tick_size = tse_tick_size(entry_price)
    return OrderType.LIMIT, entry_price + (tick_size * Decimal(buy_limit_offset_ticks))


def _stop_loss_price(
    *,
    signal: UnifiedTradeSignal,
    entry_price: Decimal | None,
    default_stop_loss_spread_pct: Decimal | None,
) -> Decimal | None:
    if signal.stop_loss_price is not None:
        return signal.stop_loss_price
    if signal.action is not Action.BUY:
        return None
    if entry_price is None or default_stop_loss_spread_pct is None:
        return None
    if entry_price <= 0 or default_stop_loss_spread_pct <= 0:
        return None
    return entry_price * (Decimal("1") - default_stop_loss_spread_pct)
