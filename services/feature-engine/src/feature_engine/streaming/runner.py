from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from trade_contracts.logging import event_extra
from trade_contracts.market import OrderBookSnapshot, TickData

from feature_engine.clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from feature_engine.clients.supabase import PositionSnapshot, SupabaseReader, SupabaseWriter
from feature_engine.config import FeatureEngineSettings
from feature_engine.storage.book import BookWarmWriter
from feature_engine.storage.warm import WarmWriter
from feature_engine.streaming.exit_orders import (
    ExitOrderMonitor,
    build_exit_order,
    topic_for_exit_order,
)
from feature_engine.streaming.feature_state import StreamingFeatureState
from feature_engine.streaming.position_updater import update_positions_for_tick
from feature_engine.streaming.session import TickDecision, TickSession

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BatchStats:
    """`run_once` 1 回分の結果。"""

    received: int
    ticks_processed: int
    books_processed: int
    ticks_dropped: int
    acked: int
    parse_errors: int
    process_errors: int


@dataclass(slots=True)
class StreamRunner:
    """`raw-market-data` を pull し、指標を組み立てて `processed-features` に publish する
    中核ループ。

    責務:
    - Pub/Sub から pull → メッセージ種別判定 (tick / order_book)
    - tick は `TickSession` で順序保証/ベキ等を通した後に `StreamingFeatureState` へ投入し、
      `ProcessedFeatures` を publish。併せて `update_positions_for_tick` で Supabase の
      `positions.current_price` / `unrealized_pnl` を更新
    - `warm_writer` が渡されていれば ACCEPT した tick を Warm レイヤに best-effort 永続化
      (失敗はログのみで pipeline は続行)
    - order_book は `feature_state.record_order_book` のみ (次の tick に紐付けられる)
    - 処理済みメッセージは ack。パース不能はポイズン扱いで ack、処理中の一過性エラーは
      ack せずに Pub/Sub の再配信に任せる
    """

    subscriber: PubSubSubscriber
    publisher: PubSubPublisher
    reader: SupabaseReader
    writer: SupabaseWriter
    feature_state: StreamingFeatureState
    tick_session: TickSession
    settings: FeatureEngineSettings
    warm_writer: WarmWriter | None = None
    book_writer: BookWarmWriter | None = None
    exit_monitor: ExitOrderMonitor = field(default_factory=ExitOrderMonitor)
    idle_backoff_seconds: float = 1.0
    sleep: Sleep = field(default=asyncio.sleep)
    monotonic: MonotonicClock = field(default=time.monotonic)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))
    summary_log_interval_seconds: float = 60.0
    _summary_started_at: float | None = field(default=None, init=False)
    _summary_received: int = field(default=0, init=False)
    _summary_ticks_processed: int = field(default=0, init=False)
    _summary_books_processed: int = field(default=0, init=False)
    _summary_ticks_dropped: int = field(default=0, init=False)
    _summary_parse_errors: int = field(default=0, init=False)
    _summary_process_errors: int = field(default=0, init=False)
    _latest_tick_timestamp: datetime | None = field(default=None, init=False)
    _market_data_is_stale: bool = field(default=False, init=False)

    async def run(self, *, iterations: int | None = None) -> list[BatchStats]:
        """pull ループ本体。`iterations=None` で無限ループ、値指定でテスト向けに有限化。

        ループ内のメッセージ処理例外はロガーに吐いてループは継続する。
        `asyncio.CancelledError` はそのまま伝播してループを終了させる。
        """
        results: list[BatchStats] = []
        count = 0
        while iterations is None or count < iterations:
            stats = await self.run_once()
            results.append(stats)
            if stats.received == 0 and self.idle_backoff_seconds > 0:
                await self.sleep(self.idle_backoff_seconds)
            count += 1
        return results

    async def run_once(self) -> BatchStats:
        """1 バッチだけ pull → 処理 → ack する。"""
        messages = await self.subscriber.pull(
            self.settings.pubsub_subscription_raw,
            max_messages=self.settings.pubsub_pull_max_messages,
        )
        ticks_processed = 0
        books_processed = 0
        ticks_dropped = 0
        parse_errors = 0
        process_errors = 0
        ack_ids: list[str] = []

        for msg in messages:
            outcome = await self._handle_message(msg)
            if outcome == "tick_processed":
                ticks_processed += 1
                ack_ids.append(msg.ack_id)
            elif outcome == "book_processed":
                books_processed += 1
                ack_ids.append(msg.ack_id)
            elif outcome == "tick_dropped":
                ticks_dropped += 1
                ack_ids.append(msg.ack_id)
            elif outcome == "parse_error":
                parse_errors += 1
                ack_ids.append(msg.ack_id)  # ポイズンメッセージは ack して抜け出す
            else:  # process_error
                process_errors += 1
                # ack しない → 再配信に任せる

        if ack_ids:
            await self.subscriber.acknowledge(self.settings.pubsub_subscription_raw, ack_ids)

        stats = BatchStats(
            received=len(messages),
            ticks_processed=ticks_processed,
            books_processed=books_processed,
            ticks_dropped=ticks_dropped,
            acked=len(ack_ids),
            parse_errors=parse_errors,
            process_errors=process_errors,
        )
        self._record_summary(stats)
        return stats

    async def _handle_message(self, msg: PulledMessage) -> str:
        parsed = _parse_payload(msg.data)
        if parsed is None:
            logger.exception("parse failed: message_id=%s len=%d", msg.message_id, len(msg.data))
            return "parse_error"

        try:
            if isinstance(parsed, OrderBookSnapshot):
                if self.book_writer is not None:
                    try:
                        self.book_writer.record_book(parsed)
                    except Exception:
                        logger.exception(
                            "book persist failed: symbol=%s timestamp=%s",
                            parsed.symbol,
                            parsed.timestamp,
                        )
                self.feature_state.record_order_book(parsed)
                return "book_processed"
            return await self._handle_tick(parsed)
        except Exception:
            logger.exception("process failed: message_id=%s", msg.message_id)
            return "process_error"

    async def _handle_tick(self, tick: TickData) -> str:
        decision = self.tick_session.observe(tick)
        if decision != TickDecision.ACCEPT:
            return "tick_dropped"
        self._latest_tick_timestamp = tick.timestamp

        if self.warm_writer is not None:
            try:
                self.warm_writer.record_tick(tick)
            except Exception:
                logger.exception(
                    "warm persist failed: symbol=%s timestamp=%s",
                    tick.symbol,
                    tick.timestamp,
                )

        features = self.feature_state.record_tick(tick)
        await self.publisher.publish(
            self.settings.pubsub_topic_features,
            data=features.model_dump_json().encode("utf-8"),
            attributes={"symbol": tick.symbol},
        )
        positions = await self.reader.fetch_positions(tick.symbol)
        await self._publish_exit_orders(tick, positions)
        await update_positions_for_tick(
            tick, reader=self.reader, writer=self.writer, positions=positions
        )
        return "tick_processed"

    async def _publish_exit_orders(self, tick: TickData, positions: list[PositionSnapshot]) -> None:
        triggers = self.exit_monitor.collect_triggers(
            tick=tick,
            positions=positions,
            max_hold_minutes=self.settings.max_hold_minutes,
        )
        for trigger in triggers:
            order = build_exit_order(trigger, created_at=self.wall_clock())
            topic = topic_for_exit_order(
                trigger,
                live_topic=self.settings.pubsub_topic_live_orders,
                paper_topic=self.settings.pubsub_topic_paper_orders,
            )
            await self.publisher.publish(
                topic,
                data=order.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": trigger.symbol,
                    "exit_reason": trigger.reason,
                    "trade_type": trigger.trade_type.value,
                },
            )
            logger.warning(
                "exit order published: symbol=%s trade_type=%s reason=%s "
                "price=%s threshold=%s qty=%d",
                trigger.symbol,
                trigger.trade_type.value,
                trigger.reason,
                trigger.price,
                trigger.threshold,
                trigger.quantity,
                extra=event_extra(
                    "exit_order_published",
                    symbol=trigger.symbol,
                    trade_type=trigger.trade_type.value,
                    reason=trigger.reason,
                    price=str(trigger.price),
                    threshold=str(trigger.threshold),
                    quantity=trigger.quantity,
                ),
            )

    def _record_summary(self, stats: BatchStats) -> None:
        now = self.monotonic()
        if self._summary_started_at is None:
            self._summary_started_at = now
        self._summary_received += stats.received
        self._summary_ticks_processed += stats.ticks_processed
        self._summary_books_processed += stats.books_processed
        self._summary_ticks_dropped += stats.ticks_dropped
        self._summary_parse_errors += stats.parse_errors
        self._summary_process_errors += stats.process_errors

        elapsed = now - self._summary_started_at
        if elapsed < self.summary_log_interval_seconds:
            return

        latest_tick_age_seconds = None
        if self._latest_tick_timestamp is not None:
            age = self.wall_clock() - self._latest_tick_timestamp
            latest_tick_age_seconds = max(0.0, age.total_seconds())

        logger.info(
            "market data summary: received=%d ticks=%d books=%d dropped=%d "
            "parse_errors=%d process_errors=%d latest_tick_age_seconds=%s",
            self._summary_received,
            self._summary_ticks_processed,
            self._summary_books_processed,
            self._summary_ticks_dropped,
            self._summary_parse_errors,
            self._summary_process_errors,
            latest_tick_age_seconds,
            extra=event_extra(
                "market_data_summary",
                received=self._summary_received,
                ticks_processed=self._summary_ticks_processed,
                books_processed=self._summary_books_processed,
                ticks_dropped=self._summary_ticks_dropped,
                parse_errors=self._summary_parse_errors,
                process_errors=self._summary_process_errors,
                latest_tick_age_seconds=latest_tick_age_seconds,
                window_seconds=round(elapsed, 3),
            ),
        )
        self._log_stale_market_data_if_needed(latest_tick_age_seconds)
        self._summary_started_at = now
        self._summary_received = 0
        self._summary_ticks_processed = 0
        self._summary_books_processed = 0
        self._summary_ticks_dropped = 0
        self._summary_parse_errors = 0
        self._summary_process_errors = 0

    def _log_stale_market_data_if_needed(self, latest_tick_age_seconds: float | None) -> None:
        threshold = self.settings.market_data_stale_warn_seconds
        if threshold is None or threshold <= 0 or latest_tick_age_seconds is None:
            return
        now = self.wall_clock()
        if not _is_jpx_continuous_auction_session(now):
            return
        is_stale = latest_tick_age_seconds > threshold
        if not is_stale:
            if self._market_data_is_stale:
                logger.info(
                    "market data recovered: latest_tick_age_seconds=%.3f threshold=%.3f",
                    latest_tick_age_seconds,
                    threshold,
                    extra=event_extra(
                        "market_data_recovered",
                        latest_tick_age_seconds=latest_tick_age_seconds,
                        threshold_seconds=threshold,
                        kind="tick",
                    ),
                )
            self._market_data_is_stale = False
            return

        if self._market_data_is_stale:
            return
        self._market_data_is_stale = True
        logger.warning(
            "market data stale: latest_tick_age_seconds=%.3f threshold=%.3f",
            latest_tick_age_seconds,
            threshold,
            extra=event_extra(
                "market_data_stale",
                latest_tick_age_seconds=latest_tick_age_seconds,
                threshold_seconds=threshold,
                kind="tick",
            ),
        )


def _parse_payload(data: bytes) -> TickData | OrderBookSnapshot | None:
    """ペイロード (JSON bytes) を型判別してパースする。

    `bids` / `asks` を含む場合は `OrderBookSnapshot`、それ以外は `TickData` と見なす。
    JSON パースや Pydantic バリデーションに失敗したら None。
    """
    try:
        payload: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        if "bids" in payload or "asks" in payload:
            return OrderBookSnapshot.model_validate(payload)
        return TickData.model_validate(payload)
    except ValidationError:
        return None


def _is_jpx_continuous_auction_session(now: datetime) -> bool:
    local = now.astimezone(JST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return (9 * 60 <= minutes < 11 * 60 + 30) or (12 * 60 + 30 <= minutes < 15 * 60 + 30)
