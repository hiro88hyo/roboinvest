"""Phase 2 backtest runner.

OrderRequest と OrderBookSnapshot をタイムスタンプ順にマージし、最新の板で
擬似約定する。fill / position transition は Phase 1 の純関数を呼ぶだけ。

ここでは pnl は計算しない (paper では daily_pnl はキルスイッチ集計外であり、
評価損益は Feature Engine の責務)。Phase 2 はあくまで「板情報による約定が
妥当か」「Position 状態が想定通り遷移するか」の検証ツールとして機能する。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from trade_contracts.enums import Side, TradingStyle
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest

from ..fill_simulator import simulate_fill
from ..models import PaperFillRecord, PaperPosition
from ..position_updater import apply_fill, build_fill_record

logger = logging.getLogger(__name__)


class NoFillRecord(BaseModel):
    """擬似約定が成立しなかった注文の記録 (backtest 出力用)。"""

    unified_signal_id: UUID
    symbol: str
    side: Side
    quantity: int
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    fills: list[PaperFillRecord]
    no_fills: list[NoFillRecord]
    final_positions: dict[str, PaperPosition]

    @property
    def order_count(self) -> int:
        return len(self.fills) + len(self.no_fills)

    @property
    def fill_count(self) -> int:
        return len(self.fills)

    @property
    def no_fill_count(self) -> int:
        return len(self.no_fills)


def _merge_events(
    orders: Iterable[OrderRequest],
    books: Iterable[OrderBookSnapshot],
) -> Iterator[tuple[datetime, Literal["book", "order"], OrderBookSnapshot | OrderRequest]]:
    """orders と books をタイムスタンプ順にマージ。

    タイムスタンプが同値のときは book を先に流す (注文到着前に板が更新される
    のが現実に近い)。
    """
    events: list[tuple[datetime, int, Literal["book", "order"], object]] = []
    for book in books:
        events.append((book.timestamp, 0, "book", book))
    for order in orders:
        events.append((order.created_at, 1, "order", order))
    events.sort(key=lambda e: (e[0], e[1]))
    for ts, _, kind, payload in events:
        # narrowed by `kind`
        assert isinstance(payload, OrderBookSnapshot | OrderRequest)
        yield ts, kind, payload


def run_backtest(
    *,
    orders: Iterable[OrderRequest],
    books: Iterable[OrderBookSnapshot],
    initial_positions: Mapping[str, PaperPosition] | None = None,
    default_holding_type: TradingStyle = TradingStyle.DAY,
) -> BacktestSummary:
    """OrderRequest を順に板で約定させ、最終ポジションと約定履歴を返す。

    * 1 シンボルにつき最新の ``OrderBookSnapshot`` をメモリ保持し、注文到着時に
      その板で擬似約定する。板未受信のシンボルへの注文は ``no_book`` で no-fill。
    * BUY で新規ポジションを作る場合の ``holding_type`` は
      ``default_holding_type`` を使う (``OrderRequest`` は holding_type を持たない)。
    * 既存ポジションがある場合の ``holding_type`` は既存値を維持する。
    * pnl 計算・スイング自動決済は対象外 (Phase 4 以降)。
    """
    positions: dict[str, PaperPosition] = dict(initial_positions or {})
    book_cache: dict[str, OrderBookSnapshot] = {}
    fills: list[PaperFillRecord] = []
    no_fills: list[NoFillRecord] = []

    for _ts, kind, payload in _merge_events(orders, books):
        if kind == "book":
            assert isinstance(payload, OrderBookSnapshot)
            book_cache[payload.symbol] = payload
            continue

        assert isinstance(payload, OrderRequest)
        order = payload
        book = book_cache.get(order.symbol)
        if book is None:
            no_fills.append(_to_no_fill(order, reason="no_book"))
            continue

        fill = simulate_fill(order=order, book=book)
        if fill.filled_quantity == 0 or fill.fill_price is None:
            no_fills.append(_to_no_fill(order, reason=fill.reason))
            continue

        existing = positions.get(order.symbol)
        holding_type = existing.holding_type if existing is not None else default_holding_type
        update = apply_fill(
            order=order,
            fill=fill,
            existing=existing,
            holding_type=holding_type,
            executed_at=order.created_at,
        )
        if update.error is not None:
            no_fills.append(_to_no_fill(order, reason=update.error))
            continue

        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is not None:
            fills.append(record)
        if update.delete:
            positions.pop(order.symbol, None)
        elif update.position is not None:
            positions[order.symbol] = update.position

    summary = BacktestSummary(fills=fills, no_fills=no_fills, final_positions=positions)
    logger.info(
        "backtest done: orders=%d fills=%d no_fills=%d positions=%d",
        summary.order_count,
        summary.fill_count,
        summary.no_fill_count,
        len(summary.final_positions),
    )
    return summary


def _to_no_fill(order: OrderRequest, *, reason: str) -> NoFillRecord:
    return NoFillRecord(
        unified_signal_id=order.unified_signal_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        reason=reason,
        created_at=order.created_at,
    )
