"""Streaming loop for OMS Paper.

paper-orders と raw-market-data の 2 本の subscription を pull し、
* OrderBookSnapshot をシンボル別の最新板キャッシュに反映
* OrderRequest を最新板で擬似約定 → trades_paper INSERT + positions UPSERT/DELETE

至 at-least-once 規約:

* 板更新メッセージは Supabase 書き込みを伴わないため、parse 後に常に ack。
* 注文メッセージは Supabase 書き込みが完了した時点で ack。書き込みに失敗した
  場合は ack 列に積まず、Pub/Sub の再配信に委ねる (fail-closed)。
* 板未受信のシンボルへの注文・板枯渇による不約定・apply_fill エラー
  (oversell 等) は **業務上の no_fill** として ack する (再配信しても
  状況が変わらないため、redelivery hell を避ける)。
* スキーマ不正・JSON パース失敗の poison message は ack する。

擬似約定の純関数 (simulate_fill / apply_fill / build_fill_record) は
Phase 1 のものをそのまま呼び出す。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from trade_contracts.enums import TradingStyle
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest

from ..clients.pubsub import PubSubError, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseClient, SupabaseError
from ..closeout import build_closeout_orders
from ..config import OmsPaperSettings
from ..fill_simulator import simulate_fill
from ..models import PaperPosition
from ..position_updater import apply_fill, build_fill_record

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BatchStats:
    books_pulled: int
    books_applied: int
    orders_pulled: int
    parse_errors: int
    filled: int
    no_fills: int
    write_errors: int
    acked: int


@dataclass(frozen=True, slots=True)
class CloseoutStats:
    triggered: bool
    skipped_reason: str | None
    positions_seen: int
    closed: int
    no_fills: int
    write_errors: int


@dataclass(slots=True)
class StreamRunner:
    subscriber: PubSubSubscriber
    supabase: SupabaseClient
    settings: OmsPaperSettings
    book_cache: dict[str, OrderBookSnapshot] = field(default_factory=dict)
    idle_backoff_seconds: float = 0.5
    sleep: Sleep = field(default=asyncio.sleep)
    monotonic: MonotonicClock = field(default=time.monotonic)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))

    async def run(self, *, iterations: int | None = None) -> list[BatchStats]:
        results: list[BatchStats] = []
        count = 0
        while iterations is None or count < iterations:
            stats = await self.run_once()
            results.append(stats)
            if (
                stats.books_pulled == 0
                and stats.orders_pulled == 0
                and self.idle_backoff_seconds > 0
            ):
                await self.sleep(self.idle_backoff_seconds)
            count += 1
        return results

    async def run_once(self) -> BatchStats:
        books_msgs = await self.subscriber.pull(
            self.settings.pubsub_subscription_raw_market_data,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        books_applied, book_acks = self._consume_books(books_msgs)
        if book_acks:
            await self.subscriber.acknowledge(
                self.settings.pubsub_subscription_raw_market_data, book_acks
            )

        order_msgs = await self.subscriber.pull(
            self.settings.pubsub_subscription_paper_orders,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        parse_errors = 0
        filled = 0
        no_fills = 0
        write_errors = 0
        order_acks: list[str] = []

        for msg in order_msgs:
            order = _parse_order(msg)
            if order is None:
                parse_errors += 1
                order_acks.append(msg.ack_id)
                continue

            try:
                outcome = await self._process_order(order)
            except SupabaseError:
                logger.exception(
                    "supabase write failed: leaving message unacked symbol=%s signal_id=%s",
                    order.symbol,
                    order.unified_signal_id,
                )
                write_errors += 1
                continue

            if outcome == "filled":
                filled += 1
            else:
                no_fills += 1
            order_acks.append(msg.ack_id)

        if order_acks:
            await self.subscriber.acknowledge(
                self.settings.pubsub_subscription_paper_orders, order_acks
            )

        return BatchStats(
            books_pulled=len(books_msgs),
            books_applied=books_applied,
            orders_pulled=len(order_msgs),
            parse_errors=parse_errors,
            filled=filled,
            no_fills=no_fills,
            write_errors=write_errors,
            acked=len(book_acks) + len(order_acks),
        )

    async def run_closeout(self, *, signal_id: UUID | None = None) -> CloseoutStats:
        """14:50 JST cron から呼ばれる前提の paper 全建玉強制決済。

        ``system_status.trading_style != day`` の場合は何もせず ``skipped`` を返す。
        各ポジションは最新の板で擬似約定する。板未受信なら no_fill。
        """
        try:
            state = await self.supabase.read_system_status()
        except SupabaseError:
            logger.exception("closeout: read_system_status failed")
            return CloseoutStats(
                triggered=False,
                skipped_reason="read_system_status_failed",
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )
        if state.trading_style is not TradingStyle.DAY:
            logger.info("closeout: skipped (trading_style=%s)", state.trading_style.value)
            return CloseoutStats(
                triggered=False,
                skipped_reason=f"trading_style_{state.trading_style.value}",
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )

        positions = await self.supabase.list_paper_positions()
        if not positions:
            return CloseoutStats(
                triggered=True,
                skipped_reason=None,
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )

        sentinel = signal_id or uuid4()
        now = self.wall_clock()
        orders = build_closeout_orders(
            positions=positions, closeout_signal_id=sentinel, created_at=now
        )

        closed = 0
        no_fills = 0
        write_errors = 0
        for order in orders:
            book = self.book_cache.get(order.symbol)
            if book is None:
                logger.warning("closeout no_fill: no book in cache symbol=%s", order.symbol)
                no_fills += 1
                continue
            fill = simulate_fill(order=order, book=book)
            if fill.filled_quantity == 0 or fill.fill_price is None:
                logger.warning(
                    "closeout no_fill: book has no liquidity symbol=%s reason=%s",
                    order.symbol,
                    fill.reason,
                )
                no_fills += 1
                continue
            existing = next((p for p in positions if p.symbol == order.symbol), None)
            update = apply_fill(
                order=order,
                fill=fill,
                existing=existing,
                holding_type=existing.holding_type if existing else TradingStyle.DAY,
                executed_at=order.created_at,
            )
            if update.error is not None:
                logger.warning(
                    "closeout apply_fill error: symbol=%s error=%s", order.symbol, update.error
                )
                no_fills += 1
                continue

            record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
            if record is None:
                no_fills += 1
                continue

            try:
                await self.supabase.insert_trade_paper(record)
                if update.delete:
                    await self.supabase.delete_paper_position(symbol=order.symbol)
                elif update.position is not None:
                    await self.supabase.update_paper_position_quantity(
                        symbol=order.symbol,
                        quantity=update.position.quantity,
                        entry_price=str(update.position.entry_price),
                    )
            except SupabaseError:
                logger.exception("closeout: write failure symbol=%s", order.symbol)
                write_errors += 1
                continue
            closed += 1

        return CloseoutStats(
            triggered=True,
            skipped_reason=None,
            positions_seen=len(positions),
            closed=closed,
            no_fills=no_fills,
            write_errors=write_errors,
        )

    def _consume_books(self, messages: list[PulledMessage]) -> tuple[int, list[str]]:
        applied = 0
        acks: list[str] = []
        for msg in messages:
            book = _parse_book(msg)
            acks.append(msg.ack_id)  # parse error / TickData も ack
            if book is None:
                continue
            self.book_cache[book.symbol] = book
            applied += 1
        return applied, acks

    async def _process_order(self, order: OrderRequest) -> str:
        """Returns 'filled' or 'no_fill'. Raises SupabaseError on write failure."""
        book = self.book_cache.get(order.symbol)
        if book is None:
            logger.info(
                "order no_fill: no book yet symbol=%s signal_id=%s",
                order.symbol,
                order.unified_signal_id,
            )
            return "no_fill"

        fill = simulate_fill(order=order, book=book)
        if fill.filled_quantity == 0 or fill.fill_price is None:
            logger.info(
                "order no_fill: symbol=%s reason=%s signal_id=%s",
                order.symbol,
                fill.reason,
                order.unified_signal_id,
            )
            return "no_fill"

        existing = await self.supabase.read_paper_position(symbol=order.symbol)
        holding_type = (
            existing.holding_type if existing is not None else self.settings.default_holding_type
        )
        update = apply_fill(
            order=order,
            fill=fill,
            existing=existing,
            holding_type=holding_type,
            executed_at=order.created_at,
        )
        if update.error is not None:
            logger.warning(
                "order apply_fill error: symbol=%s error=%s signal_id=%s",
                order.symbol,
                update.error,
                order.unified_signal_id,
            )
            return "no_fill"

        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is None:
            return "no_fill"

        # 書き込み順序: trades_paper INSERT → positions の write
        await self.supabase.insert_trade_paper(record)
        await self._write_position_change(
            existing=existing,
            update_position=update.position,
            delete=update.delete,
            symbol=order.symbol,
        )
        logger.info(
            "order filled: symbol=%s side=%s qty=%d price=%s signal_id=%s",
            record.symbol,
            record.side.value,
            record.quantity,
            record.price,
            order.unified_signal_id,
        )
        return "filled"

    async def _write_position_change(
        self,
        *,
        existing: PaperPosition | None,
        update_position: PaperPosition | None,
        delete: bool,
        symbol: str,
    ) -> None:
        if delete:
            await self.supabase.delete_paper_position(symbol=symbol)
            return
        if update_position is None:
            return  # no-op (apply_fill guarantees error path returns earlier)
        if existing is None:
            await self.supabase.insert_paper_position(update_position)
        else:
            await self.supabase.update_paper_position_quantity(
                symbol=symbol,
                quantity=update_position.quantity,
                entry_price=str(update_position.entry_price),
            )


def _parse_order(msg: PulledMessage) -> OrderRequest | None:
    try:
        payload: Any = json.loads(msg.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("order parse failed: message_id=%s", msg.message_id)
        return None
    if not isinstance(payload, dict):
        logger.warning("order parse skipped (not an object): message_id=%s", msg.message_id)
        return None
    try:
        return OrderRequest.model_validate(payload)
    except ValidationError:
        logger.exception("order schema invalid: message_id=%s", msg.message_id)
        return None


def _parse_book(msg: PulledMessage) -> OrderBookSnapshot | None:
    """raw-market-data メッセージから OrderBookSnapshot を取り出す。

    TickData は無視 (None を返す)。判定は payload の形 (`bids`/`asks` フィールド有無) で行う。
    """
    try:
        payload: Any = json.loads(msg.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("book parse failed: message_id=%s", msg.message_id)
        return None
    if not isinstance(payload, dict):
        logger.warning("book parse skipped (not an object): message_id=%s", msg.message_id)
        return None
    if "bids" not in payload or "asks" not in payload:
        # TickData (price/volume) は対象外
        return None
    try:
        return OrderBookSnapshot.model_validate(payload)
    except ValidationError:
        logger.exception("book schema invalid: message_id=%s", msg.message_id)
        return None


# 公開エイリアス。runner を直接インスタンス化するテスト向け。
__all__ = [
    "BatchStats",
    "CloseoutStats",
    "PubSubError",  # re-export for convenience
    "StreamRunner",
]
