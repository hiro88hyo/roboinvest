"""Streaming loop for the gateway.

Pulls ``UnifiedTradeSignal`` from the ``trade-signals`` subscription, runs
the Phase 1 validator against live Supabase state (``system_status`` +
``positions``), and publishes approved ``OrderRequest`` to either
``live-orders`` or ``paper-orders`` per ``system_status.trade_mode``.

At-least-once semantics:

* ack only after the signal has been fully processed (published or
  decided-to-reject). Transient failures leave the message unacked and
  Pub/Sub will redeliver.
* Unparseable or schema-invalid messages are acked immediately to avoid
  redelivery hell (poison messages).
* When the kill-switch fires on a pnl limit, the runner flips
  ``system_status.is_trading_allowed = false`` *before* acking. The UPDATE
  is idempotent so a redelivered message after a crash is harmless.

Entry price for BUY lot calculation comes from
``positions.current_price`` for the same symbol (Feature Engine writes the
latest tick there). If no price is available, BUY is rejected with
``missing_entry_price``. SELL never reads the price — it closes the
existing LONG quantity as-is.
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

from pydantic import ValidationError
from trade_contracts.enums import Action, TradeMode
from trade_contracts.signal import UnifiedTradeSignal

from .. import kill_switch
from ..clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseClient
from ..config import GatewaySettings, RiskConfig
from ..order_builder import build as build_order
from ..router import TopicRouting, resolve_topic
from ..validator import validate

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]

# Kill-switch reasons whose Supabase state needs to be flipped false.
# ``kill_switch_off`` is already false so no UPDATE is required.
_PNL_LIMIT_REASONS = frozenset({"daily_loss_limit", "weekly_loss_limit", "monthly_loss_limit"})


@dataclass(frozen=True, slots=True)
class BatchStats:
    pulled: int
    parse_errors: int
    approved: int
    rejected: int
    kill_switch_triggered: int
    acked: int


@dataclass(slots=True)
class StreamRunner:
    subscriber: PubSubSubscriber
    publisher: PubSubPublisher
    supabase: SupabaseClient
    settings: GatewaySettings
    risk_config: RiskConfig
    routing: TopicRouting
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
            if stats.pulled == 0 and self.idle_backoff_seconds > 0:
                await self.sleep(self.idle_backoff_seconds)
            count += 1
        return results

    async def run_once(self) -> BatchStats:
        messages = await self.subscriber.pull(
            self.settings.pubsub_subscription_trade_signals,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )

        parse_errors = 0
        approved = 0
        rejected = 0
        kill_switch_triggered = 0
        to_ack: list[str] = []

        for msg in messages:
            signal = _parse_signal(msg)
            if signal is None:
                parse_errors += 1
                to_ack.append(msg.ack_id)
                continue

            decision = await self._process(signal)
            if decision.approved:
                approved += 1
            else:
                rejected += 1
            if decision.kill_switch_fired:
                kill_switch_triggered += 1
            to_ack.append(msg.ack_id)

        if to_ack:
            await self.subscriber.acknowledge(
                self.settings.pubsub_subscription_trade_signals, to_ack
            )

        return BatchStats(
            pulled=len(messages),
            parse_errors=parse_errors,
            approved=approved,
            rejected=rejected,
            kill_switch_triggered=kill_switch_triggered,
            acked=len(to_ack),
        )

    async def _process(self, signal: UnifiedTradeSignal) -> _Decision:
        state = await self.supabase.read_system_status()
        trade_mode = state.trade_mode

        # Kill-switch first — cheapest reject, and avoids price/position reads
        # when trading is already off or a pnl limit has been breached.
        ks = kill_switch.evaluate(state)
        if not ks.passed:
            kill_switch_fired = False
            if ks.reason in _PNL_LIMIT_REASONS:
                await self.supabase.disable_trading(now=self.wall_clock())
                kill_switch_fired = True
            self._log_reject(signal, ks.reason or "kill_switch", trade_mode)
            return _Decision(approved=False, kill_switch_fired=kill_switch_fired)

        entry_price: Decimal | None = None
        existing_qty: int | None = None

        if signal.action is Action.BUY:
            # BUY needs the latest market price for lot calc, and an existence
            # check to prevent double-pileups.
            existing_qty = await self.supabase.read_long_quantity(
                symbol=signal.symbol, trade_mode=trade_mode
            )
            if existing_qty == 0:
                entry_price = await self.supabase.read_latest_price(symbol=signal.symbol)
                if entry_price is None:
                    self._log_reject(signal, "missing_entry_price", trade_mode)
                    return _Decision(approved=False, kill_switch_fired=False)
        elif signal.action is Action.SELL:
            existing_qty = await self.supabase.read_long_quantity(
                symbol=signal.symbol, trade_mode=trade_mode
            )

        check = validate(
            signal=signal,
            state=state,
            risk_config=self.risk_config,
            entry_price=entry_price if entry_price is not None else Decimal("0"),
            existing_long_quantity=existing_qty,
        )

        if not check.passed:
            self._log_reject(signal, check.reason or "unknown", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        quantity = check.adjusted_quantity
        if quantity is None or quantity <= 0:
            # Defensive: validator should never return passed=True without a
            # positive adjusted_quantity, but fail-closed if it somehow does.
            self._log_reject(signal, "no_quantity", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        order = build_order(
            signal=signal,
            quantity=quantity,
            trade_mode=trade_mode,
            created_at=self.wall_clock(),
        )
        topic = resolve_topic(trade_mode, self.routing)
        await self.publisher.publish(
            topic,
            data=order.model_dump_json().encode("utf-8"),
            attributes={
                "symbol": order.symbol,
                "side": order.side.value,
                "trade_mode": order.trade_mode.value,
                "signal_source": order.signal_source.value,
            },
        )
        logger.info(
            "order approved: symbol=%s side=%s qty=%d trade_mode=%s signal_id=%s",
            order.symbol,
            order.side.value,
            order.quantity,
            order.trade_mode.value,
            signal.signal_id,
        )
        return _Decision(approved=True, kill_switch_fired=False)

    def _log_reject(self, signal: UnifiedTradeSignal, reason: str, trade_mode: TradeMode) -> None:
        logger.info(
            "signal rejected: symbol=%s action=%s reason=%s trade_mode=%s signal_id=%s",
            signal.symbol,
            signal.action.value,
            reason,
            trade_mode.value,
            signal.signal_id,
        )


@dataclass(frozen=True, slots=True)
class _Decision:
    approved: bool
    kill_switch_fired: bool


def _parse_signal(msg: PulledMessage) -> UnifiedTradeSignal | None:
    try:
        payload: Any = json.loads(msg.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("parse failed: message_id=%s", msg.message_id)
        return None
    if not isinstance(payload, dict):
        logger.warning("parse skipped (not an object): message_id=%s", msg.message_id)
        return None
    try:
        return UnifiedTradeSignal.model_validate(payload)
    except ValidationError:
        logger.exception("schema invalid: message_id=%s", msg.message_id)
        return None
