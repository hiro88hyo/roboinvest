"""Phase 2 backtest runner.

OrderRequest と OrderBookSnapshot をタイムスタンプ順にマージし、最新の板で
擬似約定する。fill / position transition は Phase 1 の純関数を呼ぶだけ。

評価損益は Feature Engine の責務だが、backtest の収益評価に必要な実現損益は
summary に含める。paper の daily_pnl / kill switch には反映しない。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest
from trade_contracts.tick_size import tse_tick_size

from ..day_monitor import evaluate_day_exit
from ..fill_simulator import simulate_fill
from ..models import PaperFillRecord, PaperPosition
from ..position_updater import apply_fill, build_fill_record
from .report import ClosedTrade, ExecutionQualityRecord

logger = logging.getLogger(__name__)

_COMMISSION_RATE = Decimal("0.00099")


class NoFillRecord(BaseModel):
    """擬似約定が成立しなかった注文の記録 (backtest 出力用)。"""

    unified_signal_id: UUID | None = None
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
    closed_trades: list[ClosedTrade]
    execution_quality: list[ExecutionQualityRecord]
    realized_pnl: Decimal = Decimal("0")

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
      ``OrderRequest`` の値を優先し、未指定なら ``default_holding_type`` を使う。
    * 既存ポジションがある場合の ``holding_type`` は既存値を維持する。
    * 実現損益は SELL 決済時に計算し、約定代金 0.099% の往復手数料を控除する。
      評価損益・スイング自動決済は対象外 (Phase 4 以降)。
    """
    positions: dict[str, PaperPosition] = dict(initial_positions or {})
    book_cache: dict[str, OrderBookSnapshot] = {}
    fills: list[PaperFillRecord] = []
    no_fills: list[NoFillRecord] = []
    closed_trades: list[ClosedTrade] = []
    execution_quality: list[ExecutionQualityRecord] = []
    realized_pnl = Decimal("0")

    for _ts, kind, payload in _merge_events(orders, books):
        if kind == "book":
            assert isinstance(payload, OrderBookSnapshot)
            book_cache[payload.symbol] = payload
            realized_pnl += _run_day_exit_if_needed(
                book=payload,
                positions=positions,
                fills=fills,
                no_fills=no_fills,
                closed_trades=closed_trades,
                execution_quality=execution_quality,
            )
            continue

        assert isinstance(payload, OrderRequest)
        order = payload
        book = book_cache.get(order.symbol)
        if book is None:
            no_fills.append(_to_no_fill(order, reason="no_book"))
            continue

        fill = simulate_fill(order=order, book=book)
        execution_quality.append(
            _execution_quality_for_order(
                order=order, book=book, filled_quantity=fill.filled_quantity, reason=fill.reason
            )
        )
        if fill.filled_quantity == 0 or fill.fill_price is None:
            no_fills.append(_to_no_fill(order, reason=fill.reason))
            continue

        existing = positions.get(order.symbol)
        holding_type = (
            existing.holding_type
            if existing is not None
            else order.holding_type or default_holding_type
        )
        update = apply_fill(
            order=order,
            fill=fill,
            existing=existing,
            holding_type=holding_type,
            stop_loss_price=order.stop_loss_price,
            stop_loss_pct=order.stop_loss_pct,
            target_price=order.target_price,
            max_hold_days=order.max_hold_days,
            scheduled_exit_date=order.scheduled_exit_date,
            scheduled_exit_time=order.scheduled_exit_time,
            trailing_stop_pct=order.trailing_stop_pct,
            executed_at=order.created_at,
        )
        if update.error is not None:
            no_fills.append(_to_no_fill(order, reason=update.error))
            continue

        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is not None:
            fills.append(record)
            closed = _closed_trade_for_fill(record=record, existing=existing)
            if closed is not None:
                closed_trades.append(closed)
                realized_pnl += closed.net_pnl_before_tax
        if update.delete:
            positions.pop(order.symbol, None)
        elif update.position is not None:
            positions[order.symbol] = update.position

    summary = BacktestSummary(
        fills=fills,
        no_fills=no_fills,
        final_positions=positions,
        closed_trades=closed_trades,
        execution_quality=execution_quality,
        realized_pnl=realized_pnl,
    )
    logger.info(
        "backtest done: orders=%d fills=%d no_fills=%d positions=%d realized_pnl=%s",
        summary.order_count,
        summary.fill_count,
        summary.no_fill_count,
        len(summary.final_positions),
        summary.realized_pnl,
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


def _run_day_exit_if_needed(
    *,
    book: OrderBookSnapshot,
    positions: dict[str, PaperPosition],
    fills: list[PaperFillRecord],
    no_fills: list[NoFillRecord],
    closed_trades: list[ClosedTrade],
    execution_quality: list[ExecutionQualityRecord],
) -> Decimal:
    position = positions.get(book.symbol)
    if position is None or not book.bids:
        return Decimal("0")

    decision = evaluate_day_exit(
        position=position, latest_price=book.bids[0].price, now=book.timestamp
    )
    if decision.action != "exit":
        return Decimal("0")

    order = OrderRequest(
        unified_signal_id=None,
        symbol=position.symbol,
        side=Side.SELL,
        quantity=position.quantity,
        order_type=OrderType.MARKET,
        trade_mode=TradeMode.PAPER,
        signal_source=SignalSource.CONSENSUS,
        created_at=book.timestamp,
    )
    fill = simulate_fill(order=order, book=book)
    execution_quality.append(
        _execution_quality_for_order(
            order=order, book=book, filled_quantity=fill.filled_quantity, reason=fill.reason
        )
    )
    if fill.filled_quantity == 0 or fill.fill_price is None:
        reason = f"day_exit_{decision.reason or 'exit'}_{fill.reason}"
        no_fills.append(_to_no_fill(order, reason=reason))
        return Decimal("0")

    update = apply_fill(
        order=order,
        fill=fill,
        existing=position,
        holding_type=position.holding_type,
        executed_at=book.timestamp,
    )
    if update.error is not None:
        no_fills.append(_to_no_fill(order, reason=f"day_exit_{update.error}"))
        return Decimal("0")

    record = build_fill_record(order=order, fill=fill, executed_at=book.timestamp)
    if record is None:
        no_fills.append(_to_no_fill(order, reason="day_exit_no_record"))
        return Decimal("0")

    fills.append(record)
    realized_pnl = Decimal("0")
    closed = _closed_trade_for_fill(record=record, existing=position)
    if closed is not None:
        closed_trades.append(closed)
        realized_pnl = closed.net_pnl_before_tax
    if update.delete:
        positions.pop(position.symbol, None)
    elif update.position is not None:
        positions[position.symbol] = update.position
    return realized_pnl


def _closed_trade_for_fill(
    *, record: PaperFillRecord, existing: PaperPosition | None
) -> ClosedTrade | None:
    if record.side is not Side.SELL or existing is None:
        return None

    qty = Decimal(record.quantity)
    entry_notional = existing.entry_price * qty
    exit_notional = record.price * qty
    gross_pnl = exit_notional - entry_notional
    commission = (entry_notional + exit_notional) * _COMMISSION_RATE
    return ClosedTrade(
        symbol=record.symbol,
        quantity=record.quantity,
        entry_price=existing.entry_price,
        exit_price=record.price,
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        gross_pnl=gross_pnl,
        commission=commission,
        net_pnl_before_tax=gross_pnl - commission,
        executed_at=record.executed_at,
    )


def _execution_quality_for_order(
    *,
    order: OrderRequest,
    book: OrderBookSnapshot,
    filled_quantity: int,
    reason: str,
) -> ExecutionQualityRecord:
    bid_qty = sum(level.quantity for level in book.bids)
    ask_qty = sum(level.quantity for level in book.asks)
    best_bid = book.bids[0].price if book.bids else None
    best_ask = book.asks[0].price if book.asks else None
    return ExecutionQualityRecord(
        unified_signal_id=order.unified_signal_id,
        symbol=order.symbol,
        side=order.side,
        requested_quantity=order.quantity,
        filled_quantity=filled_quantity,
        fill_ratio=Decimal(filled_quantity) / Decimal(order.quantity),
        reason=reason,
        order_created_at=order.created_at,
        book_timestamp=book.timestamp,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=_spread_bps(best_bid=best_bid, best_ask=best_ask),
        tick_size=_tick_size(best_bid=best_bid, best_ask=best_ask),
        spread_ticks=_spread_ticks(best_bid=best_bid, best_ask=best_ask),
        opposite_depth_quantity=ask_qty if order.side is Side.BUY else bid_qty,
        same_side_depth_quantity=bid_qty if order.side is Side.BUY else ask_qty,
        order_book_imbalance=_book_imbalance(bid_qty=bid_qty, ask_qty=ask_qty),
    )


def _spread_bps(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / Decimal("2")
    if mid <= 0:
        return None
    return ((best_ask - best_bid) / mid) * Decimal("10000")


def _tick_size(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / Decimal("2")
    if mid <= 0:
        return None
    return tse_tick_size(mid)


def _spread_ticks(*, best_bid: Decimal | None, best_ask: Decimal | None) -> Decimal | None:
    tick_size = _tick_size(best_bid=best_bid, best_ask=best_ask)
    if best_bid is None or best_ask is None or tick_size is None:
        return None
    spread = best_ask - best_bid
    if spread < 0:
        return None
    return spread / tick_size


def _book_imbalance(*, bid_qty: int, ask_qty: int) -> Decimal | None:
    total = bid_qty + ask_qty
    if total <= 0:
        return None
    return Decimal(bid_qty - ask_qty) / Decimal(total)
