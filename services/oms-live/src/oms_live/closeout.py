"""Phase 2 デイクローズアウト用注文生成 (純関数)。

``trading_style=day`` の live ポジション全件に対して ``Side.SELL`` の成行注文を
生成する。発火タイミング (14:50 JST) と ``system_status`` の参照は
Streaming Runner / scheduler が担う。

oms-paper の同名モジュールと同型。``trade_mode`` のデフォルトが ``LIVE`` で
あること、そして closeout の ``unified_signal_id`` は呼び出し側が生成するので
**aggregator_logs に対応行が無く本番 Supabase で trades_live の FK 違反になる**
持ち越し課題は oms-paper と共通 (Phase 4 でセンチネル INSERT or FK nullable 化で決着)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode
from trade_contracts.order import OrderRequest

from .models import LivePosition


def build_closeout_orders(
    *,
    positions: Sequence[LivePosition],
    closeout_signal_id: UUID,
    created_at: datetime,
    trade_mode: TradeMode = TradeMode.LIVE,
    signal_source: SignalSource = SignalSource.CONSENSUS,
) -> list[OrderRequest]:
    """全 live ポジションに対する成行 SELL 注文を生成する。

    数量が 0 のポジションはスキップ (定義上 ``LivePosition.quantity > 0`` だが防御的)。
    並びは ``positions`` の引数順を維持する。
    """

    return [
        OrderRequest(
            unified_signal_id=closeout_signal_id,
            symbol=p.symbol,
            side=Side.SELL,
            quantity=p.quantity,
            order_type=OrderType.MARKET,
            trade_mode=trade_mode,
            signal_source=signal_source,
            created_at=created_at,
        )
        for p in positions
        if p.quantity > 0
    ]
