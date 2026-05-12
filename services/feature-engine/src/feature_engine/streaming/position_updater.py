from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from trade_contracts.market import TickData

from feature_engine.clients.supabase import PositionSnapshot, SupabaseReader, SupabaseWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PositionUpdate:
    """`positions` の時価更新 1 行分。"""

    symbol: str
    trade_type: str
    current_price: Decimal
    unrealized_pnl: Decimal


def apply_tick(tick: TickData, positions: list[PositionSnapshot]) -> list[PositionUpdate]:
    """`tick` を `positions` に適用し、更新行のみを返す純関数。

    同一 symbol の live / paper 両方を一度に更新できる。保有数量が 0 以下のものは無視。
    評価損益は現物ロング前提で `(current_price - entry_price) * quantity` を `Decimal` で算出する。
    """
    if not positions:
        return []
    updates: list[PositionUpdate] = []
    for pos in positions:
        if pos.symbol != tick.symbol:
            continue
        if pos.quantity <= 0:
            continue
        pnl = (tick.price - pos.entry_price) * Decimal(pos.quantity)
        updates.append(
            PositionUpdate(
                symbol=pos.symbol,
                trade_type=pos.trade_type.value,
                current_price=tick.price,
                unrealized_pnl=pnl,
            )
        )
    return updates


async def update_positions_for_tick(
    tick: TickData,
    *,
    reader: SupabaseReader,
    writer: SupabaseWriter,
) -> int:
    """tick で特定される symbol の全ポジションを Supabase 上で時価更新する。

    更新件数を返す。保有がない銘柄は no-op。
    """
    positions = await reader.fetch_positions(tick.symbol)
    updates = apply_tick(tick, positions)
    if not updates:
        return 0
    for u in updates:
        await writer.patch(
            "positions",
            filters={"symbol": f"eq.{u.symbol}", "trade_type": f"eq.{u.trade_type}"},
            values={"current_price": str(u.current_price), "unrealized_pnl": str(u.unrealized_pnl)},
        )
    logger.info(
        "positions updated: symbol=%s rows=%d price=%s",
        tick.symbol,
        len(updates),
        tick.price,
    )
    return len(updates)
