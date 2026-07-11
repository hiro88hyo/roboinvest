"""Phase 1 ポジション遷移 (純関数)。

擬似約定の結果と既存ポジションから、新しいポジション状態と ``trades_paper``
向けの約定レコードを生成する。

呼び出し側は ``simulate_fill`` の結果を受けて本関数を呼ぶ。新規 BUY の相対 stop
intent はここで実際の約定価格に固定し、streaming と backtest で同じポジション
遷移を使う。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from trade_contracts.enums import Side, TradingStyle
from trade_contracts.order import OrderRequest

from .calendar import nth_tse_business_day_after
from .models import FillResult, PaperFillRecord, PaperPosition, PositionUpdate

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


def _resolve_stop_loss_price(
    *,
    fill_price: Decimal,
    stop_loss_price: Decimal | None,
    stop_loss_pct: Decimal | None,
) -> Decimal | None:
    """Resolve a relative stop intent against the actual BUY fill price."""

    if stop_loss_pct is None:
        return stop_loss_price
    return fill_price * (Decimal("1") - stop_loss_pct)


def build_fill_record(
    *,
    order: OrderRequest,
    fill: FillResult,
    executed_at: datetime,
) -> PaperFillRecord | None:
    """``trades_paper`` 1 行に対応する約定レコードを生成する。不約定時は ``None``。"""

    if fill.filled_quantity <= 0 or fill.fill_price is None:
        return None
    return PaperFillRecord(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=fill.filled_quantity,
        price=fill.fill_price,
        signal_source=order.signal_source,
        unified_signal_id=order.unified_signal_id,
        executed_at=executed_at,
    )


def apply_fill(
    *,
    order: OrderRequest,
    fill: FillResult,
    existing: PaperPosition | None,
    holding_type: TradingStyle,
    stop_loss_price: Decimal | None = None,
    stop_loss_pct: Decimal | None = None,
    target_price: Decimal | None = None,
    max_hold_days: int | None = None,
    scheduled_exit_date: date | None = None,
    trailing_stop_pct: Decimal | None = None,
    executed_at: datetime,
) -> PositionUpdate:
    """擬似約定の結果を既存ポジションに適用する。

    - BUY 既存なし → 新規ポジション (相対 stop は実約定価格に固定)
    - BUY 既存あり → 数量加算 + 平均取得単価更新 (holding_type / stop 等は既存を維持)
    - SELL 既存あり → 数量減算 (残量 0 で ``delete=True``)
    - SELL 既存なし → スキップ + ``error="no_position_for_sell"``
    - SELL 既存数量超過 → スキップ + ``error="oversell"`` (Gateway で防がれる前提)
    - 不約定 (filled_quantity=0) → 既存をそのまま返し ``error="no_fill"``
    """

    if fill.filled_quantity <= 0 or fill.fill_price is None:
        return PositionUpdate(position=existing, delete=False, error="no_fill")

    if order.side is Side.BUY:
        if existing is None:
            resolved_stop_loss_price = _resolve_stop_loss_price(
                fill_price=fill.fill_price,
                stop_loss_price=stop_loss_price,
                stop_loss_pct=stop_loss_pct,
            )
            new = PaperPosition(
                symbol=order.symbol,
                quantity=fill.filled_quantity,
                entry_price=fill.fill_price,
                holding_type=holding_type,
                stop_loss_price=resolved_stop_loss_price,
                target_price=target_price,
                max_hold_days=max_hold_days,
                scheduled_exit_date=scheduled_exit_date
                or nth_tse_business_day_after(executed_at.date(), max_hold_days),
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
    new_qty = existing.quantity - fill.filled_quantity
    if new_qty == 0:
        return PositionUpdate(position=None, delete=True)
    reduced = existing.model_copy(update={"quantity": new_qty})
    return PositionUpdate(position=reduced, delete=False)
