"""Phase 2 ポジション遷移 (純関数)。

実約定の結果と既存ポジションから、新しいポジション状態と ``trades_live`` 向け
の約定レコード、そして SELL 時の実現損益を生成する。oms-paper の同名モジュール
と同型だが、SELL 時に ``realized_pnl = (price - entry_price) * qty`` を返す点が
追加の責務。

呼び出し側 (StreamRunner) は ``apply_fill`` の戻り値を見て:
- ``delete=True`` → ``delete_live_position`` + ``add_realized_pnl(realized_pnl)``
- ``position is not None`` → INSERT or PATCH
- ``error is not None`` → no_fill としてカウント (再配信は冪等性のため避ける)
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from trade_contracts.enums import Side, TradingStyle
from trade_contracts.order import OrderRequest

from .models import FillResult, LiveFillRecord, LivePosition, PositionUpdate

_PRICE_QUANT = Decimal("1")


def _weighted_average_entry(
    *,
    existing_qty: int,
    existing_price: Decimal,
    add_qty: int,
    add_price: Decimal,
) -> Decimal:
    total_cost = existing_price * existing_qty + add_price * add_qty
    raw = total_cost / Decimal(existing_qty + add_qty)
    return raw.quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)


def build_fill_record(
    *,
    order: OrderRequest,
    fill: FillResult,
    executed_at: datetime,
) -> LiveFillRecord | None:
    """``trades_live`` 1 行に対応する約定レコードを生成する。不約定時は ``None``。

    ``order.order_id`` は冪等性キーとして ``LiveFillRecord.order_id`` に carry し、
    Supabase 側の partial unique index で二重 INSERT を弾けるようにする。
    """

    if fill.filled_quantity <= 0 or fill.fill_price is None:
        return None
    return LiveFillRecord(
        symbol=order.symbol,
        side=order.side,
        quantity=fill.filled_quantity,
        price=fill.fill_price,
        signal_source=order.signal_source,
        unified_signal_id=order.unified_signal_id,
        order_id=order.order_id,
        executed_at=executed_at,
    )


def realized_pnl(
    *,
    entry_price: Decimal,
    fill_price: Decimal,
    quantity: int,
) -> Decimal:
    """LONG 決済の実現損益。 ``(fill - entry) * qty``。

    現物 LONG のみ。手数料・税は ここでは無視 (Phase 4 以降で
    ``OrderResult`` か ``positions`` 列で扱う想定)。
    """
    return (fill_price - entry_price) * Decimal(quantity)


def apply_fill(
    *,
    order: OrderRequest,
    fill: FillResult,
    existing: LivePosition | None,
    holding_type: TradingStyle,
    stop_loss_price: Decimal | None = None,
    target_price: Decimal | None = None,
    max_hold_days: int | None = None,
    trailing_stop_pct: Decimal | None = None,
    executed_at: datetime,
) -> PositionUpdate:
    """実約定の結果を既存ポジションに適用する。oms-paper と同じ判定 + SELL 時の PnL。

    - BUY 既存なし → 新規ポジション
    - BUY 既存あり → 数量加算 + 平均取得単価更新 (holding_type / stop 等は既存を維持)
    - SELL 既存あり → 数量減算 (残量 0 で ``delete=True``、実現損益を ``realized_pnl`` に)
    - SELL 既存なし → スキップ + ``error="no_position_for_sell"``
    - SELL 既存数量超過 → スキップ + ``error="oversell"`` (Gateway で防がれる前提)
    - 不約定 (filled_quantity=0) → 既存をそのまま返し ``error="no_fill"``
    """

    if fill.filled_quantity <= 0 or fill.fill_price is None:
        return PositionUpdate(position=existing, delete=False, error="no_fill")

    if order.side is Side.BUY:
        if existing is None:
            new = LivePosition(
                symbol=order.symbol,
                quantity=fill.filled_quantity,
                entry_price=fill.fill_price,
                holding_type=holding_type,
                stop_loss_price=stop_loss_price,
                target_price=target_price,
                max_hold_days=max_hold_days,
                trailing_stop_pct=trailing_stop_pct,
                opened_at=executed_at,
            )
            return PositionUpdate(position=new, delete=False)
        new_entry = _weighted_average_entry(
            existing_qty=existing.quantity,
            existing_price=existing.entry_price,
            add_qty=fill.filled_quantity,
            add_price=fill.fill_price,
        )
        merged = existing.model_copy(
            update={
                "quantity": existing.quantity + fill.filled_quantity,
                "entry_price": new_entry,
            }
        )
        return PositionUpdate(position=merged, delete=False)

    # SELL
    if existing is None:
        return PositionUpdate(position=None, delete=False, error="no_position_for_sell")
    if fill.filled_quantity > existing.quantity:
        return PositionUpdate(position=existing, delete=False, error="oversell")
    pnl = realized_pnl(
        entry_price=existing.entry_price,
        fill_price=fill.fill_price,
        quantity=fill.filled_quantity,
    )
    new_qty = existing.quantity - fill.filled_quantity
    if new_qty == 0:
        return PositionUpdate(position=None, delete=True, realized_pnl=pnl)
    reduced = existing.model_copy(update={"quantity": new_qty})
    return PositionUpdate(position=reduced, delete=False, realized_pnl=pnl)
