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

Entry price for BUY lot calculation prefers
``UnifiedTradeSignal.price`` carried through from ``ProcessedFeatures.price``.
This lets live BUY signals for flat positions use a fresh tick price without depending on an
existing ``positions`` row. When the signal price is missing, the runner
falls back to ``positions.current_price`` for the same symbol, and in
``trade_mode=paper`` only it may then fall back to the latest
``daily_ohlcv.close``. Live mode still does NOT fall back to daily data —
it stays fail-closed and rejects with ``missing_entry_price`` to avoid
sending real money on stale prices. SELL never reads the price — it
closes the existing LONG quantity as-is.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from trade_contracts.enums import Action, TradeMode, TradingStyle
from trade_contracts.signal import UnifiedTradeSignal

from .. import kill_switch, lot_calculator
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
        now = self.wall_clock()

        # Kill-switch first — cheapest reject, and avoids price/position reads
        # when trading is already off or a pnl limit has been breached.
        ks = kill_switch.evaluate(state)
        if not ks.passed:
            kill_switch_fired = False
            if ks.reason in _PNL_LIMIT_REASONS:
                await self.supabase.disable_trading(now=now)
                kill_switch_fired = True
            self._log_reject(signal, ks.reason or "kill_switch", trade_mode)
            return _Decision(approved=False, kill_switch_fired=kill_switch_fired)

        if self._is_stale_live_signal(signal=signal, trade_mode=trade_mode, now=now):
            self._log_reject(signal, "stale_signal", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._is_live_day_session_closed(
            trade_mode=trade_mode, holding_type=signal.holding_type
        ):
            self._log_reject(signal, "market_closed", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        entry_price: Decimal | None = None
        existing_qty: int | None = None

        if signal.action is Action.BUY:
            # BUY needs the latest market price for lot calc, and an existence
            # check to prevent double-pileups.
            existing_qty = await self.supabase.read_long_quantity(
                symbol=signal.symbol, trade_mode=trade_mode
            )
            if existing_qty == 0:
                entry_price = signal.price
                entry_price_source = "signal"
                if entry_price is None:
                    entry_price = await self.supabase.read_latest_price(symbol=signal.symbol)
                    entry_price_source = "positions"
                if entry_price is None and trade_mode is TradeMode.PAPER:
                    entry_price = await self.supabase.read_latest_daily_close(symbol=signal.symbol)
                    if entry_price is not None:
                        entry_price_source = "daily_ohlcv"
                if entry_price is None:
                    self._log_reject(signal, "missing_entry_price", trade_mode)
                    return _Decision(approved=False, kill_switch_fired=False)
                logger.info(
                    "entry_price resolved: symbol=%s source=%s trade_mode=%s",
                    signal.symbol,
                    entry_price_source,
                    trade_mode.value,
                )
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

        quantity = await self._rebudget_live_buy_quantity(
            signal=signal,
            trade_mode=trade_mode,
            entry_price=entry_price,
            quantity=quantity,
        )
        if quantity is None:
            self._log_reject(signal, "insufficient_live_budget", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        quantity = self._cap_live_buy_quantity(
            signal=signal,
            trade_mode=trade_mode,
            quantity=quantity,
        )
        if quantity is None:
            self._log_reject(signal, "live_qty_cap_below_min_lot", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        order = build_order(
            signal=signal,
            quantity=quantity,
            trade_mode=trade_mode,
            created_at=now,
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

    async def _rebudget_live_buy_quantity(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        entry_price: Decimal | None,
        quantity: int,
    ) -> int | None:
        if trade_mode is not TradeMode.LIVE or signal.action is not Action.BUY:
            return quantity
        if entry_price is None:
            return quantity

        live_exposure = await self.supabase.read_live_capital_in_use()
        remaining_capital = self.risk_config.capital - live_exposure
        if remaining_capital <= 0:
            logger.warning(
                "live buy budget exhausted: symbol=%s exposure=%s capital=%s",
                signal.symbol,
                live_exposure,
                self.risk_config.capital,
            )
            return None

        risk_config = replace(self.risk_config, capital=remaining_capital)
        check = lot_calculator.calculate(
            signal=signal,
            entry_price=entry_price,
            config=risk_config,
        )
        rebudgeted = check.adjusted_quantity if check.passed else None
        if rebudgeted is None or rebudgeted <= 0:
            logger.info(
                "live buy rejected by remaining budget: "
                "symbol=%s exposure=%s remaining_capital=%s reason=%s",
                signal.symbol,
                live_exposure,
                remaining_capital,
                check.reason,
            )
            return None
        if rebudgeted < quantity:
            logger.info(
                "live buy quantity reduced by exposure budget: "
                "symbol=%s qty=%d rebudgeted=%d exposure=%s remaining_capital=%s",
                signal.symbol,
                quantity,
                rebudgeted,
                live_exposure,
                remaining_capital,
            )
        return rebudgeted

    def _signal_age_seconds(self, *, signal: UnifiedTradeSignal, now: datetime) -> float:
        return max(0.0, (now - signal.created_at).total_seconds())

    def _is_stale_live_signal(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        now: datetime,
    ) -> bool:
        max_age = self.settings.live_signal_max_age_seconds
        if trade_mode is not TradeMode.LIVE or max_age is None:
            return False
        return self._signal_age_seconds(signal=signal, now=now) > max_age

    def _is_live_day_session_closed(
        self,
        *,
        trade_mode: TradeMode,
        holding_type: TradingStyle,
    ) -> bool:
        if trade_mode is not TradeMode.LIVE or holding_type is not TradingStyle.DAY:
            return False
        now = self.wall_clock().astimezone(ZoneInfo(self.settings.day_closeout_timezone))
        hh, mm = self.settings.day_closeout_time.split(":", 1)
        close_h, close_m = int(hh), int(mm)
        return (now.hour, now.minute) >= (close_h, close_m)

    def _cap_live_buy_quantity(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        quantity: int,
    ) -> int | None:
        if trade_mode is not TradeMode.LIVE or signal.action is not Action.BUY:
            return quantity

        cap = self.settings.oms_live_max_qty_per_order
        if cap is None or quantity <= cap:
            return quantity

        capped = (cap // self.risk_config.min_lot_size) * self.risk_config.min_lot_size
        if capped < self.risk_config.min_lot_size:
            logger.warning(
                "live buy quantity cap below min lot: symbol=%s qty=%d cap=%d min_lot=%d",
                signal.symbol,
                quantity,
                cap,
                self.risk_config.min_lot_size,
            )
            return None

        logger.info(
            "live buy quantity capped: symbol=%s qty=%d capped=%d cap=%d",
            signal.symbol,
            quantity,
            capped,
            cap,
        )
        return capped

    def _log_reject(self, signal: UnifiedTradeSignal, reason: str, trade_mode: TradeMode) -> None:
        logger.info(
            (
                "signal rejected: symbol=%s action=%s reason=%s trade_mode=%s "
                "signal_id=%s signal_source=%s has_price=%s age_seconds=%.3f "
                "strategy_signal_id_a=%s strategy_signal_id_b=%s"
            ),
            signal.symbol,
            signal.action.value,
            reason,
            trade_mode.value,
            signal.signal_id,
            signal.signal_source.value,
            signal.price is not None,
            self._signal_age_seconds(signal=signal, now=self.wall_clock()),
            signal.strategy_signal_id_a,
            signal.strategy_signal_id_b,
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
