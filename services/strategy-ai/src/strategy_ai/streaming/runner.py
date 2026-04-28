from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

from strategy_ai.clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from strategy_ai.clients.supabase import SupabaseWriter
from strategy_ai.config import StrategyAiSettings
from strategy_ai.engine import StrategyAiEngine

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BatchStats:
    """`run_once` 1 回分の結果。"""

    received: int
    features_processed: int
    signals_emitted: int
    acked: int
    parse_errors: int
    process_errors: int


@dataclass(slots=True)
class StreamRunner:
    """`processed-features` を pull し、AiStrategy を評価して `strategy-signals-b` に
    publish しつつ Supabase `strategy_logs` (source=AI) に書き込む中核ループ。

    責務:
    - Pub/Sub から pull → `ProcessedFeatures` にパース
    - `StrategyAiEngine.evaluate` で 0..N 件のシグナルを async に生成
    - 各 `StrategySignal` を `strategy-signals-b` に publish (1 メッセージ 1 シグナル)
    - 同じバッチを Supabase `strategy_logs` に upsert
    - 処理済みメッセージは ack。パース不能はポイズン扱いで ack、
      処理中の一過性エラー (Pub/Sub publish 失敗、Supabase 書込失敗) は
      ack せずに Pub/Sub の再配信に任せる

    LLM タイムアウト・5xx・パース不能は AiStrategy 内で `LLMError` を握り潰して
    `None` を返すので、ここまで例外が上がってくるのは Pub/Sub / Supabase 系の
    transient 障害。再配信で復旧できるので ack を保留する。
    at-least-once。重複は Aggregator 側で排除する想定。
    """

    subscriber: PubSubSubscriber
    publisher: PubSubPublisher
    writer: SupabaseWriter
    engine: StrategyAiEngine
    settings: StrategyAiSettings
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
            self.settings.pubsub_subscription_features,
            max_messages=self.settings.pubsub_pull_max_messages,
        )
        features_processed = 0
        signals_emitted = 0
        parse_errors = 0
        process_errors = 0
        ack_ids: list[str] = []

        for msg in messages:
            outcome, emitted = await self._handle_message(msg)
            if outcome == "processed":
                features_processed += 1
                signals_emitted += emitted
                ack_ids.append(msg.ack_id)
            elif outcome == "parse_error":
                parse_errors += 1
                ack_ids.append(msg.ack_id)  # ポイズンは ack して抜け出す
            else:  # process_error
                process_errors += 1
                # ack しない → 再配信に任せる (Pub/Sub publish / Supabase 障害が主)

        if ack_ids:
            await self.subscriber.acknowledge(self.settings.pubsub_subscription_features, ack_ids)

        return BatchStats(
            received=len(messages),
            features_processed=features_processed,
            signals_emitted=signals_emitted,
            acked=len(ack_ids),
            parse_errors=parse_errors,
            process_errors=process_errors,
        )

    async def _handle_message(self, msg: PulledMessage) -> tuple[str, int]:
        features = _parse_features(msg.data)
        if features is None:
            logger.exception("parse failed: message_id=%s len=%d", msg.message_id, len(msg.data))
            return "parse_error", 0

        try:
            signals = await self.engine.evaluate(features)
            await self._dispatch_signals(features.symbol, signals)
        except Exception:
            logger.exception("process failed: message_id=%s", msg.message_id)
            return "process_error", 0
        return "processed", len(signals)

    async def _dispatch_signals(self, symbol: str, signals: list[StrategySignal]) -> None:
        if not signals:
            return
        for signal in signals:
            await self.publisher.publish(
                self.settings.pubsub_topic_signals,
                data=signal.model_dump_json().encode("utf-8"),
                attributes={"symbol": symbol, "source": signal.source.value},
            )
        await self.writer.insert_strategy_logs(signals)


def _parse_features(data: bytes) -> ProcessedFeatures | None:
    """ペイロード (JSON bytes) を `ProcessedFeatures` にパースする。

    JSON パースや Pydantic バリデーションに失敗したら None。
    """
    try:
        payload: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ProcessedFeatures.model_validate(payload)
    except ValidationError:
        return None
