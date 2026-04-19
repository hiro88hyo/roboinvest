from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from trade_contracts.market import OrderBookSnapshot, TickData

from feature_engine.clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from feature_engine.clients.supabase import SupabaseReader, SupabaseWriter
from feature_engine.config import FeatureEngineSettings
from feature_engine.storage.warm import WarmWriter
from feature_engine.streaming.feature_state import StreamingFeatureState
from feature_engine.streaming.position_updater import update_positions_for_tick
from feature_engine.streaming.session import TickDecision, TickSession

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


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
    idle_backoff_seconds: float = 1.0
    sleep: Sleep = field(default=asyncio.sleep)

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

        return BatchStats(
            received=len(messages),
            ticks_processed=ticks_processed,
            books_processed=books_processed,
            ticks_dropped=ticks_dropped,
            acked=len(ack_ids),
            parse_errors=parse_errors,
            process_errors=process_errors,
        )

    async def _handle_message(self, msg: PulledMessage) -> str:
        parsed = _parse_payload(msg.data)
        if parsed is None:
            logger.exception("parse failed: message_id=%s len=%d", msg.message_id, len(msg.data))
            return "parse_error"

        try:
            if isinstance(parsed, OrderBookSnapshot):
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
        await update_positions_for_tick(tick, reader=self.reader, writer=self.writer)
        return "tick_processed"


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
