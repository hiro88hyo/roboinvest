"""Phase 1 デイクローズアウト用注文生成 (純関数)。

``trading_style=day`` の paper ポジションだけに対して ``Side.SELL`` の成行注文を
生成する。``holding_type=swing`` はシステムの day closeout 状態に関係なく
保持する。実際の発火タイミング (14:50 JST 等) と ``system_status`` の参照は Phase 3 の
ストリーミングランナーが担う。

closeout 由来の注文は対応する ``aggregator_logs`` 行を持たないため、
``unified_signal_id`` は ``None`` で出力する (``trades_paper.unified_signal_id``
は nullable FK)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle
from trade_contracts.order import OrderRequest

from .models import PaperPosition


def build_closeout_orders(
    *,
    positions: Sequence[PaperPosition],
    created_at: datetime,
    trade_mode: TradeMode = TradeMode.PAPER,
    signal_source: SignalSource = SignalSource.CONSENSUS,
) -> list[OrderRequest]:
    """Day paper ポジションに対する成行 SELL 注文を生成する。

    ``holding_type=swing`` と数量 0 はスキップする (定義上
    ``PaperPosition.quantity > 0`` だが後者は防御的にチェック)。並びは
    ``positions`` の引数順を維持する。
    """

    return [
        OrderRequest(
            unified_signal_id=None,
            symbol=p.symbol,
            side=Side.SELL,
            quantity=p.quantity,
            order_type=OrderType.MARKET,
            trade_mode=trade_mode,
            signal_source=signal_source,
            created_at=created_at,
        )
        for p in positions
        if p.holding_type is TradingStyle.DAY and p.quantity > 0
    ]
