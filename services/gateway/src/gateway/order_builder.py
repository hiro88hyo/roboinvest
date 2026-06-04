"""Phase 1 OrderRequest 組み立て(純関数)。

バリデーション済みの ``UnifiedTradeSignal`` と ``adjusted_quantity`` を結合し、
``OrderRequest`` を生成する。Phase 1 では ``order_type=MARKET`` 固定。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.enums import Action, OrderType, Side, TradeMode
from trade_contracts.order import OrderRequest
from trade_contracts.signal import UnifiedTradeSignal


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
    default_stop_loss_spread_pct: Decimal | None = None,
    created_at: datetime | None = None,
) -> OrderRequest:
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got: {quantity}")
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
        order_type=OrderType.MARKET,
        trade_mode=trade_mode,
        signal_source=signal.signal_source,
        stop_loss_price=stop_loss_price,
        target_price=signal.target_price,
        trailing_stop_pct=signal.trailing_stop_pct,
        max_hold_days=signal.max_hold_days,
        created_at=created_at or datetime.now(UTC),
    )


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
