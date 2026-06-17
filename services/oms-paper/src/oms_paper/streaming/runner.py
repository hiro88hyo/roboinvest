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
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle
from trade_contracts.logging import event_extra
from trade_contracts.market import OrderBookSnapshot
from trade_contracts.order import OrderRequest

from ..clients.pubsub import PubSubError, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseClient, SupabaseError
from ..closeout import build_closeout_orders
from ..config import OmsPaperSettings
from ..day_monitor import evaluate_day_exit
from ..fill_simulator import simulate_fill
from ..models import PaperPosition
from ..position_updater import apply_fill, build_fill_record
from ..swing_monitor import evaluate_swing_exit

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")

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
    skipped_duplicate: int = 0
    swing_exits: int = 0
    swing_trails: int = 0
    swing_no_fills: int = 0
    swing_write_errors: int = 0
    day_stop_exits: int = 0
    day_stop_trails: int = 0
    day_stop_no_fills: int = 0
    day_stop_write_errors: int = 0


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
    # swing 自動決済 (Phase 4) 用キャッシュ。symbol → PaperPosition (holding_type=swing)
    # のみ保持。TTL 経過で list_paper_positions から再フェッチ。
    swing_position_cache: dict[str, PaperPosition] = field(default_factory=dict)
    day_position_cache: dict[str, PaperPosition] = field(default_factory=dict)
    swing_cache_ttl_seconds: float = 30.0
    swing_cache_loaded_at: float | None = None
    summary_log_interval_seconds: float = 60.0
    _summary_started_at: float | None = field(default=None, init=False)
    _summary_books_pulled: int = field(default=0, init=False)
    _summary_books_applied: int = field(default=0, init=False)
    _summary_orders_pulled: int = field(default=0, init=False)
    _summary_parse_errors: int = field(default=0, init=False)
    _summary_filled: int = field(default=0, init=False)
    _summary_no_fills: int = field(default=0, init=False)
    _summary_write_errors: int = field(default=0, init=False)
    _latest_book_timestamp: datetime | None = field(default=None, init=False)
    _market_data_is_stale: bool = field(default=False, init=False)

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
        books_applied, book_acks, updated_symbols = self._consume_books(books_msgs)
        if book_acks:
            await self.subscriber.acknowledge(
                self.settings.pubsub_subscription_raw_market_data, book_acks
            )

        # 板更新があった symbol について swing position を評価
        (
            swing_exits,
            swing_trails,
            swing_no_fills,
            swing_write_errors,
        ) = await self._evaluate_swing_for_symbols(updated_symbols)
        day_stop_exits = 0
        day_stop_trails = 0
        day_stop_no_fills = 0
        day_stop_write_errors = 0
        if self.settings.paper_day_stop_monitor_enabled:
            (
                day_stop_exits,
                day_stop_trails,
                day_stop_no_fills,
                day_stop_write_errors,
            ) = await self._evaluate_day_stops_for_symbols(updated_symbols)

        order_msgs = await self.subscriber.pull(
            self.settings.pubsub_subscription_paper_orders,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        parse_errors = 0
        filled = 0
        no_fills = 0
        write_errors = 0
        skipped_duplicate = 0
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
            elif outcome == "skipped_duplicate":
                skipped_duplicate += 1
            else:
                no_fills += 1
            order_acks.append(msg.ack_id)

        if order_acks:
            await self.subscriber.acknowledge(
                self.settings.pubsub_subscription_paper_orders, order_acks
            )

        stats = BatchStats(
            books_pulled=len(books_msgs),
            books_applied=books_applied,
            orders_pulled=len(order_msgs),
            parse_errors=parse_errors,
            filled=filled,
            no_fills=no_fills,
            write_errors=write_errors,
            acked=len(book_acks) + len(order_acks),
            skipped_duplicate=skipped_duplicate,
            swing_exits=swing_exits,
            swing_trails=swing_trails,
            swing_no_fills=swing_no_fills,
            swing_write_errors=swing_write_errors,
            day_stop_exits=day_stop_exits,
            day_stop_trails=day_stop_trails,
            day_stop_no_fills=day_stop_no_fills,
            day_stop_write_errors=day_stop_write_errors,
        )
        self._record_summary(stats)
        return stats

    async def run_closeout(self) -> CloseoutStats:
        """14:50 JST cron から呼ばれる前提の paper 全建玉強制決済。

        ``system_status.trading_style != day`` の場合は何もせず ``skipped`` を返す。
        各ポジションは最新の板で擬似約定する。板未受信なら no_fill。

        closeout 由来の OrderRequest / 約定行は対応する ``aggregator_logs`` 行を
        持たないため ``unified_signal_id`` は ``None`` で書き込む。
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

        now = self.wall_clock()
        orders = build_closeout_orders(positions=positions, created_at=now)

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

    def _consume_books(self, messages: list[PulledMessage]) -> tuple[int, list[str], set[str]]:
        applied = 0
        acks: list[str] = []
        updated: set[str] = set()
        for msg in messages:
            book = _parse_book(msg)
            acks.append(msg.ack_id)  # parse error / TickData も ack
            if book is None:
                continue
            existing = self.book_cache.get(book.symbol)
            if existing is not None and book.timestamp < existing.timestamp:
                logger.info(
                    "book skipped: stale update symbol=%s incoming=%s cached=%s",
                    book.symbol,
                    book.timestamp.isoformat(),
                    existing.timestamp.isoformat(),
                )
                continue
            self.book_cache[book.symbol] = book
            self._latest_book_timestamp = book.timestamp
            updated.add(book.symbol)
            applied += 1
        return applied, acks, updated

    def _record_summary(self, stats: BatchStats) -> None:
        now = self.monotonic()
        if self._summary_started_at is None:
            self._summary_started_at = now
        self._summary_books_pulled += stats.books_pulled
        self._summary_books_applied += stats.books_applied
        self._summary_orders_pulled += stats.orders_pulled
        self._summary_parse_errors += stats.parse_errors
        self._summary_filled += stats.filled
        self._summary_no_fills += stats.no_fills
        self._summary_write_errors += stats.write_errors

        elapsed = now - self._summary_started_at
        if elapsed < self.summary_log_interval_seconds:
            return

        latest_book_age_seconds = None
        if self._latest_book_timestamp is not None:
            age = self.wall_clock() - self._latest_book_timestamp
            latest_book_age_seconds = max(0.0, age.total_seconds())

        logger.info(
            "market data summary: books_pulled=%d books_applied=%d orders=%d "
            "filled=%d no_fills=%d parse_errors=%d write_errors=%d "
            "latest_book_age_seconds=%s",
            self._summary_books_pulled,
            self._summary_books_applied,
            self._summary_orders_pulled,
            self._summary_filled,
            self._summary_no_fills,
            self._summary_parse_errors,
            self._summary_write_errors,
            latest_book_age_seconds,
            extra=event_extra(
                "market_data_summary",
                books_pulled=self._summary_books_pulled,
                books_applied=self._summary_books_applied,
                orders_pulled=self._summary_orders_pulled,
                filled=self._summary_filled,
                no_fills=self._summary_no_fills,
                parse_errors=self._summary_parse_errors,
                write_errors=self._summary_write_errors,
                latest_book_age_seconds=latest_book_age_seconds,
                window_seconds=round(elapsed, 3),
            ),
        )
        self._log_stale_market_data_if_needed(latest_book_age_seconds)
        self._summary_started_at = now
        self._summary_books_pulled = 0
        self._summary_books_applied = 0
        self._summary_orders_pulled = 0
        self._summary_parse_errors = 0
        self._summary_filled = 0
        self._summary_no_fills = 0
        self._summary_write_errors = 0

    def _log_stale_market_data_if_needed(self, latest_book_age_seconds: float | None) -> None:
        threshold = self.settings.market_data_stale_warn_seconds
        if threshold is None or threshold <= 0 or latest_book_age_seconds is None:
            return
        now = self.wall_clock()
        if not _is_jpx_continuous_auction_session(now):
            return
        is_stale = latest_book_age_seconds > threshold
        if not is_stale:
            if self._market_data_is_stale:
                logger.info(
                    "market data recovered: latest_book_age_seconds=%.3f threshold=%.3f",
                    latest_book_age_seconds,
                    threshold,
                    extra=event_extra(
                        "market_data_recovered",
                        latest_book_age_seconds=latest_book_age_seconds,
                        threshold_seconds=threshold,
                        kind="order_book",
                    ),
                )
            self._market_data_is_stale = False
            return

        if self._market_data_is_stale:
            return
        self._market_data_is_stale = True
        logger.warning(
            "market data stale: latest_book_age_seconds=%.3f threshold=%.3f",
            latest_book_age_seconds,
            threshold,
            extra=event_extra(
                "market_data_stale",
                latest_book_age_seconds=latest_book_age_seconds,
                threshold_seconds=threshold,
                kind="order_book",
            ),
        )

    async def _ensure_position_caches_fresh(self) -> None:
        """``swing_cache_ttl_seconds`` を超過していたら positions cache を再取得。

        ``list_paper_positions`` は day/swing monitor で共有し、同一 run_once 内の
        DB fetch とテスト fixture 消費を増やさない。
        """
        now = self.monotonic()
        if (
            self.swing_cache_loaded_at is not None
            and now - self.swing_cache_loaded_at < self.swing_cache_ttl_seconds
        ):
            return
        positions = await self.supabase.list_paper_positions()
        self.swing_position_cache = {
            p.symbol: p for p in positions if p.holding_type is TradingStyle.SWING
        }
        self.day_position_cache = {
            p.symbol: p for p in positions if p.holding_type is TradingStyle.DAY
        }
        self.swing_cache_loaded_at = now

    async def _ensure_swing_cache_fresh(self) -> None:
        """Backward-compatible wrapper for swing tests/call sites."""
        await self._ensure_position_caches_fresh()

    async def _evaluate_swing_for_symbols(self, symbols: set[str]) -> tuple[int, int, int, int]:
        """板更新のあった symbol について swing 自動決済を評価する。

        Returns ``(exits, trails, no_fills, write_errors)``。

        * cache refresh 失敗時は ``write_errors=1`` を返す (1 サイクル分)。
          次の板で再評価する。
        * ``bids`` が空 / 非 swing position は ``no_fills`` でも ``exits`` でもなく
          単にスキップ (no_fills は "swing position があるのに約定できなかった"
          ケースのみ計上)。
        * ``exit`` で書き込み成功 → cache から該当を pop。書き込み失敗 → cache 維持
          (次の板で retry)。
        * ``trail`` で書き込み成功 → cache の position の ``stop_loss_price`` を更新。
        """
        if not symbols:
            return 0, 0, 0, 0
        try:
            await self._ensure_swing_cache_fresh()
        except SupabaseError:
            logger.exception("swing cache refresh failed; will retry next cycle")
            return 0, 0, 0, 1

        exits = 0
        trails = 0
        no_fills = 0
        write_errors = 0
        now = self.wall_clock()

        for symbol in symbols:
            position = self.swing_position_cache.get(symbol)
            if position is None:
                continue
            book = self.book_cache.get(symbol)
            if book is None or not book.bids:
                logger.info("swing skip: no bids for symbol=%s", symbol)
                no_fills += 1
                continue
            latest_price = book.bids[0].price
            decision = evaluate_swing_exit(position=position, latest_price=latest_price, now=now)
            if decision.action == "hold":
                continue
            try:
                if decision.action == "exit":
                    outcome = await self._run_swing_exit(
                        position=position,
                        book=book,
                        reason=decision.reason or "",
                        now=now,
                    )
                    if outcome == "exit":
                        exits += 1
                        self.swing_position_cache.pop(symbol, None)
                    else:
                        no_fills += 1
                else:  # "trail"
                    assert decision.new_stop_loss_price is not None
                    await self._run_swing_trail(
                        symbol=symbol,
                        new_stop_loss_price=decision.new_stop_loss_price,
                    )
                    self.swing_position_cache[symbol] = position.model_copy(
                        update={"stop_loss_price": decision.new_stop_loss_price}
                    )
                    trails += 1
            except SupabaseError:
                logger.exception(
                    "swing decision write failed: symbol=%s action=%s",
                    symbol,
                    decision.action,
                )
                write_errors += 1
        return exits, trails, no_fills, write_errors

    async def _run_swing_exit(
        self,
        *,
        position: PaperPosition,
        book: OrderBookSnapshot,
        reason: str,
        now: datetime,
    ) -> str:
        """swing exit 注文を組み立て、擬似約定 → trades_paper INSERT → positions DELETE。

        Returns ``'exit'`` / ``'no_fill'``。 ``SupabaseError`` は呼び出し側で捕捉。
        closeout と同じく ``unified_signal_id`` は ``None`` (対応する
        ``aggregator_logs`` 行を持たないため)。
        """
        order = OrderRequest(
            unified_signal_id=None,
            symbol=position.symbol,
            side=Side.SELL,
            quantity=position.quantity,
            order_type=OrderType.MARKET,
            trade_mode=TradeMode.PAPER,
            signal_source=SignalSource.CONSENSUS,
            created_at=now,
        )
        fill = simulate_fill(order=order, book=book)
        if fill.filled_quantity == 0 or fill.fill_price is None:
            logger.warning(
                "swing exit no_fill: symbol=%s reason=%s fill_reason=%s",
                position.symbol,
                reason,
                fill.reason,
            )
            return "no_fill"
        update = apply_fill(
            order=order,
            fill=fill,
            existing=position,
            holding_type=position.holding_type,
            executed_at=now,
        )
        if update.error is not None:
            logger.warning(
                "swing exit apply_fill error: symbol=%s error=%s",
                position.symbol,
                update.error,
            )
            return "no_fill"
        record = build_fill_record(order=order, fill=fill, executed_at=now)
        if record is None:
            return "no_fill"

        await self.supabase.insert_trade_paper(record)
        if update.delete:
            await self.supabase.delete_paper_position(symbol=position.symbol)
        elif update.position is not None:
            # 部分約定 (paper では稀): 残量を PATCH
            await self.supabase.update_paper_position_quantity(
                symbol=position.symbol,
                quantity=update.position.quantity,
                entry_price=str(update.position.entry_price),
            )
        logger.info(
            "swing exit filled: symbol=%s reason=%s qty=%d price=%s",
            position.symbol,
            reason,
            record.quantity,
            record.price,
        )
        return "exit"

    async def _run_swing_trail(self, *, symbol: str, new_stop_loss_price: Decimal) -> None:
        """``stop_loss_price`` のみ PATCH。 ``SupabaseError`` は呼び出し側で捕捉。"""
        await self.supabase.update_paper_position_stop_loss(
            symbol=symbol, stop_loss_price=str(new_stop_loss_price)
        )
        logger.info("swing trail: symbol=%s new_stop=%s", symbol, new_stop_loss_price)

    async def _evaluate_day_stops_for_symbols(
        self, symbols: set[str]
    ) -> tuple[int, int, int, int]:
        """板更新のあった symbol について day stop/target/trailing を評価する."""
        if not symbols:
            return 0, 0, 0, 0
        try:
            await self._ensure_position_caches_fresh()
        except SupabaseError:
            logger.exception("day stop cache refresh failed; will retry next cycle")
            return 0, 0, 0, 1

        exits = 0
        trails = 0
        no_fills = 0
        write_errors = 0
        now = self.wall_clock()

        for symbol in symbols:
            position = self.day_position_cache.get(symbol)
            if position is None:
                continue
            book = self.book_cache.get(symbol)
            if book is None or not book.bids:
                logger.info("day stop skip: no bids for symbol=%s", symbol)
                no_fills += 1
                continue
            latest_price = book.bids[0].price
            decision = evaluate_day_exit(position=position, latest_price=latest_price, now=now)
            if decision.action == "hold":
                continue
            try:
                if decision.action == "exit":
                    outcome = await self._run_day_stop_exit(
                        position=position,
                        book=book,
                        reason=decision.reason or "",
                        now=now,
                    )
                    if outcome == "exit":
                        exits += 1
                        self.day_position_cache.pop(symbol, None)
                    else:
                        no_fills += 1
                else:
                    assert decision.new_stop_loss_price is not None
                    await self._run_day_stop_trail(
                        symbol=symbol,
                        new_stop_loss_price=decision.new_stop_loss_price,
                    )
                    self.day_position_cache[symbol] = position.model_copy(
                        update={"stop_loss_price": decision.new_stop_loss_price}
                    )
                    trails += 1
            except SupabaseError:
                logger.exception(
                    "day stop decision write failed: symbol=%s action=%s",
                    symbol,
                    decision.action,
                )
                write_errors += 1
        return exits, trails, no_fills, write_errors

    async def _run_day_stop_exit(
        self,
        *,
        position: PaperPosition,
        book: OrderBookSnapshot,
        reason: str,
        now: datetime,
    ) -> str:
        order = OrderRequest(
            unified_signal_id=None,
            symbol=position.symbol,
            side=Side.SELL,
            quantity=position.quantity,
            order_type=OrderType.MARKET,
            trade_mode=TradeMode.PAPER,
            signal_source=SignalSource.CONSENSUS,
            created_at=now,
        )
        fill = simulate_fill(order=order, book=book)
        if fill.filled_quantity == 0 or fill.fill_price is None:
            logger.warning(
                "day stop exit no_fill: symbol=%s reason=%s fill_reason=%s",
                position.symbol,
                reason,
                fill.reason,
            )
            return "no_fill"
        update = apply_fill(
            order=order,
            fill=fill,
            existing=position,
            holding_type=position.holding_type,
            executed_at=now,
        )
        if update.error is not None:
            logger.warning(
                "day stop exit apply_fill error: symbol=%s error=%s",
                position.symbol,
                update.error,
            )
            return "no_fill"
        record = build_fill_record(order=order, fill=fill, executed_at=now)
        if record is None:
            return "no_fill"

        await self.supabase.insert_trade_paper(record)
        if update.delete:
            await self.supabase.delete_paper_position(symbol=position.symbol)
        elif update.position is not None:
            await self.supabase.update_paper_position_quantity(
                symbol=position.symbol,
                quantity=update.position.quantity,
                entry_price=str(update.position.entry_price),
            )
        logger.info(
            "day stop exit filled: symbol=%s reason=%s qty=%d price=%s",
            position.symbol,
            reason,
            record.quantity,
            record.price,
            extra=event_extra(
                "day_stop_exit",
                symbol=position.symbol,
                reason=reason,
                quantity=record.quantity,
                price=str(record.price),
            ),
        )
        return "exit"

    async def _run_day_stop_trail(self, *, symbol: str, new_stop_loss_price: Decimal) -> None:
        await self.supabase.update_paper_position_stop_loss(
            symbol=symbol, stop_loss_price=str(new_stop_loss_price)
        )
        logger.info(
            "day stop trail: symbol=%s new_stop=%s",
            symbol,
            new_stop_loss_price,
            extra=event_extra(
                "day_stop_trail",
                symbol=symbol,
                new_stop_loss_price=str(new_stop_loss_price),
            ),
        )

    async def _process_order(self, order: OrderRequest) -> str:
        """Returns 'filled', 'no_fill', or 'skipped_duplicate'.

        Raises SupabaseError on write failure.
        """
        # 0) idempotency: 同一 signal_id が再配信された場合は skip して ack
        if (
            order.unified_signal_id is not None
            and await self.supabase.paper_trade_exists_for_signal(order.unified_signal_id)
        ):
            logger.info(
                "order skipped_duplicate: symbol=%s signal_id=%s",
                order.symbol,
                order.unified_signal_id,
            )
            return "skipped_duplicate"

        book = self.book_cache.get(order.symbol)
        if book is None:
            logger.info(
                "order no_fill: no book yet symbol=%s signal_id=%s",
                order.symbol,
                order.unified_signal_id,
            )
            return "no_fill"
        if self._is_book_too_old_for_order(book=book, order=order):
            logger.warning(
                "order no_fill: stale book symbol=%s book_ts=%s order_ts=%s signal_id=%s",
                order.symbol,
                book.timestamp.isoformat(),
                order.created_at.isoformat(),
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
            stop_loss_price=order.stop_loss_price,
            target_price=order.target_price,
            max_hold_days=order.max_hold_days,
            trailing_stop_pct=order.trailing_stop_pct,
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

    def _is_book_too_old_for_order(self, *, book: OrderBookSnapshot, order: OrderRequest) -> bool:
        max_age = self.settings.order_book_max_age_seconds
        if max_age is None or max_age <= 0:
            return False
        age = (order.created_at - book.timestamp).total_seconds()
        return age > max_age

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


def _is_jpx_continuous_auction_session(now: datetime) -> bool:
    local = now.astimezone(JST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return (9 * 60 <= minutes < 11 * 60 + 30) or (12 * 60 + 30 <= minutes < 15 * 60 + 30)


# 公開エイリアス。runner を直接インスタンス化するテスト向け。
__all__ = [
    "BatchStats",
    "CloseoutStats",
    "PubSubError",  # re-export for convenience
    "StreamRunner",
]
