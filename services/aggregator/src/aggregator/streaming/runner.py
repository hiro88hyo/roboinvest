"""Streaming aggregation loop.

Pulls `StrategySignal` messages from both `strategy-signals-a` (RULE) and
`strategy-signals-b` (AI) subscriptions, buffers them into
`(symbol, bucket, strategy_key, candidate_id)` groups, and emits
`UnifiedTradeSignal` via the consensus function once a bucket is ready.
Emissions are published to the
`trade-signals` topic and written to Supabase `aggregator_logs` in that
order (Pub/Sub first — Supabase is the durable record for dashboards).

At-least-once: ack only after both publish and insert succeed. If either
fails, the signals stay unacked and Pub/Sub will redeliver; duplicates are
tolerated because the Supabase write upserts on `signal_id`.
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

from pydantic import ValidationError
from trade_contracts.enums import Action
from trade_contracts.signal import StrategySignal, UnifiedTradeSignal

from ..clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseWriter
from ..config import AggregatorSettings
from ..consensus import ConsensusConfig, aggregate
from .buffer import Origin, PairingBuffer, ReadyBucket

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BatchStats:
    """Result of one `run_once` tick."""

    pulled_a: int
    pulled_b: int
    parse_errors: int
    buckets_emitted: int
    unified_emitted: int
    acked_a: int
    acked_b: int


@dataclass(slots=True)
class StreamRunner:
    subscriber_a: PubSubSubscriber
    subscriber_b: PubSubSubscriber
    publisher: PubSubPublisher
    writer: SupabaseWriter
    settings: AggregatorSettings
    consensus_config: ConsensusConfig
    idle_backoff_seconds: float = 0.5
    sleep: Sleep = field(default=asyncio.sleep)
    monotonic: MonotonicClock = field(default=time.monotonic)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))
    _buffer: PairingBuffer = field(init=False)

    def __post_init__(self) -> None:
        self._buffer = PairingBuffer(
            bucket_ms=self.settings.pairing_bucket_ms,
            window_ms=self.settings.pairing_window_ms,
        )

    async def run(self, *, iterations: int | None = None) -> list[BatchStats]:
        results: list[BatchStats] = []
        count = 0
        while iterations is None or count < iterations:
            stats = await self.run_once()
            results.append(stats)
            if (
                stats.pulled_a == 0
                and stats.pulled_b == 0
                and stats.buckets_emitted == 0
                and self.idle_backoff_seconds > 0
            ):
                await self.sleep(self.idle_backoff_seconds)
            count += 1
        final = await self.flush()
        if final is not None:
            results.append(final)
        return results

    async def run_once(self) -> BatchStats:
        (
            (pulled_a, parse_a, poison_acked_a),
            (pulled_b, parse_b, poison_acked_b),
        ) = await asyncio.gather(
            self._pull_into_buffer(
                self.subscriber_a,
                self.settings.pubsub_subscription_signals_a,
                origin="a",
            ),
            self._pull_into_buffer(
                self.subscriber_b,
                self.settings.pubsub_subscription_signals_b,
                origin="b",
            ),
        )

        ready = self._buffer.drain_ready(self.monotonic())
        buckets_emitted, unified_emitted, acked_a, acked_b = await self._emit(ready)

        return BatchStats(
            pulled_a=pulled_a,
            pulled_b=pulled_b,
            parse_errors=parse_a + parse_b,
            buckets_emitted=buckets_emitted,
            unified_emitted=unified_emitted,
            acked_a=acked_a + poison_acked_a,
            acked_b=acked_b + poison_acked_b,
        )

    async def flush(self) -> BatchStats | None:
        """Drain every outstanding bucket (used at shutdown)."""
        remaining = self._buffer.drain_all()
        if not remaining:
            return None
        buckets_emitted, unified_emitted, acked_a, acked_b = await self._emit(remaining)
        return BatchStats(
            pulled_a=0,
            pulled_b=0,
            parse_errors=0,
            buckets_emitted=buckets_emitted,
            unified_emitted=unified_emitted,
            acked_a=acked_a,
            acked_b=acked_b,
        )

    async def _pull_into_buffer(
        self, subscriber: PubSubSubscriber, subscription: str, *, origin: Origin
    ) -> tuple[int, int, int]:
        """Pull a batch, parse, buffer the good ones, ack the poison ones.

        Returns ``(pulled, parse_errors, poison_acked)``.
        """
        messages = await subscriber.pull(
            subscription,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        parse_errors = 0
        poison_ack_ids: list[str] = []
        now = self.monotonic()
        for msg in messages:
            signal = _parse_signal(msg)
            if signal is None:
                parse_errors += 1
                poison_ack_ids.append(msg.ack_id)
                continue
            self._buffer.add(signal=signal, origin=origin, ack_id=msg.ack_id, now_monotonic=now)
        if poison_ack_ids:
            await subscriber.acknowledge(subscription, poison_ack_ids)
        return len(messages), parse_errors, len(poison_ack_ids)

    async def _emit(self, ready: list[ReadyBucket]) -> tuple[int, int, int, int]:
        unified_batch: list[UnifiedTradeSignal] = []
        ack_ids_a: list[str] = []
        ack_ids_b: list[str] = []
        for bucket in ready:
            merged = aggregate(
                bucket.signals,
                config=self.consensus_config,
                now=self.wall_clock(),
            )
            if merged is not None:
                unified_batch.append(merged)
            ack_ids_a.extend(bucket.ack_ids_a)
            ack_ids_b.extend(bucket.ack_ids_b)

        unified_batch = await self._filter_sell_without_position(unified_batch)

        for unified in unified_batch:
            await self.publisher.publish(
                self.settings.pubsub_topic_trade_signals,
                data=unified.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": unified.symbol,
                    "signal_source": unified.signal_source.value,
                    "routing_intent": unified.routing_intent.value,
                    "strategy_key": unified.strategy_key or "",
                    "candidate_id": unified.candidate_id or "",
                },
            )
        if unified_batch:
            await self.writer.insert_aggregator_logs(unified_batch)

        if ack_ids_a:
            await self.subscriber_a.acknowledge(
                self.settings.pubsub_subscription_signals_a, ack_ids_a
            )
        if ack_ids_b:
            await self.subscriber_b.acknowledge(
                self.settings.pubsub_subscription_signals_b, ack_ids_b
            )

        return len(ready), len(unified_batch), len(ack_ids_a), len(ack_ids_b)

    async def _filter_sell_without_position(
        self, signals: list[UnifiedTradeSignal]
    ) -> list[UnifiedTradeSignal]:
        if not self.settings.sell_requires_position or not any(
            signal.action is Action.SELL for signal in signals
        ):
            return signals

        trade_mode = await self.writer.read_trade_mode()
        qty_cache: dict[str, int] = {}
        out: list[UnifiedTradeSignal] = []
        for signal in signals:
            if signal.action is not Action.SELL:
                out.append(signal)
                continue
            qty = qty_cache.get(signal.symbol)
            if qty is None:
                qty = await self.writer.read_long_quantity(
                    symbol=signal.symbol,
                    trade_mode=trade_mode,
                )
                qty_cache[signal.symbol] = qty
            if qty > 0:
                out.append(signal)
            else:
                logger.info(
                    "sell signal dropped without position: symbol=%s trade_mode=%s signal_id=%s",
                    signal.symbol,
                    trade_mode.value,
                    signal.signal_id,
                )
        return out


def _parse_signal(msg: PulledMessage) -> StrategySignal | None:
    try:
        payload: Any = json.loads(msg.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("parse failed: message_id=%s", msg.message_id)
        return None
    if not isinstance(payload, dict):
        logger.warning("parse skipped (not an object): message_id=%s", msg.message_id)
        return None
    try:
        return StrategySignal.model_validate(payload)
    except ValidationError:
        logger.exception("schema invalid: message_id=%s", msg.message_id)
        return None
