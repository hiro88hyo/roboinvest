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
import contextlib
import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.logging import event_extra
from trade_contracts.order import OrderRequest
from trade_contracts.risk import KillSwitchState
from trade_contracts.signal import UnifiedTradeSignal

from .. import lot_calculator
from ..clients.kabu import KabuWalletClient
from ..clients.pubsub import PubSubPublisher, PubSubSubscriber, PulledMessage
from ..clients.supabase import DailyLiquiditySnapshot, MarketRegimeState, SupabaseClient
from ..config import GatewaySettings, RiskConfig
from ..order_archive import OrderArchiveWriter
from ..order_builder import build as build_order
from ..router import TopicRouting, resolve_topic
from ..validator import validate

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


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
    kabu: KabuWalletClient | None = None
    order_archive: OrderArchiveWriter | None = None
    idle_backoff_seconds: float = 0.5
    sleep: Sleep = field(default=asyncio.sleep)
    monotonic: MonotonicClock = field(default=time.monotonic)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))
    reject_summary_log_interval_seconds: float = 60.0
    publish_summary_log_interval_seconds: float = 60.0
    _reject_summary_started_at: float | None = field(default=None, init=False)
    _reject_summary_reasons: Counter[str] = field(default_factory=Counter, init=False)
    _publish_summary_started_at: float | None = field(default=None, init=False)
    _publish_summary_trade_modes: Counter[str] = field(default_factory=Counter, init=False)
    _publish_summary_sides: Counter[str] = field(default_factory=Counter, init=False)
    _publish_summary_topics: Counter[str] = field(default_factory=Counter, init=False)
    _pending_live_order_deadlines: dict[tuple[str, str, str], float] = field(
        default_factory=dict, init=False
    )
    _cached_live_capital: Decimal | None = field(default=None, init=False)

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
        kill_switch_decision = await self.supabase.check_kill_switch()
        state = kill_switch_decision.state
        trade_mode = state.trade_mode
        now = self.wall_clock()

        # Kill-switch first — cheapest reject, and avoids price/position reads
        # when trading is already off or a pnl limit has been breached.
        if not kill_switch_decision.passed:
            self._log_reject(signal, kill_switch_decision.reason or "kill_switch", trade_mode)
            return _Decision(
                approved=False,
                kill_switch_fired=kill_switch_decision.disabled,
            )

        if self._is_stale_signal(signal=signal, now=now):
            self._log_reject(signal, "stale_signal", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._is_day_session_closed(holding_type=signal.holding_type, trade_mode=trade_mode):
            self._log_reject(signal, "market_closed", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._is_day_late_buy(signal=signal, trade_mode=trade_mode, now=now):
            self._log_reject(signal, "late_live_buy", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._is_day_opening_buy(signal=signal, trade_mode=trade_mode, now=now):
            self._log_reject(signal, "opening_live_buy", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if await self._has_same_symbol_sell_today(signal=signal, trade_mode=trade_mode, now=now):
            self._log_reject(signal, "same_day_reentry_after_sell", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._has_pending_live_order(signal=signal, trade_mode=trade_mode):
            self._log_reject(signal, "pending_live_order", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        if self._soft_loss_throttle_blocks_buy(signal=signal, state=state):
            reason = "soft_loss_rule_only_buy"
            if self.settings.soft_loss_throttle_guard_enabled:
                self._log_reject(signal, reason, trade_mode)
                return _Decision(approved=False, kill_switch_fired=False)
            self._log_soft_loss_throttle_would_reject(
                signal=signal,
                trade_mode=trade_mode,
                state=state,
                reason=reason,
            )

        regime = await self._read_market_regime_for_signal(signal=signal, now=now)
        if regime is not None and self._market_regime_blocks_buy(signal=signal, regime=regime):
            reason = "market_regime_risk_off"
            if self.settings.market_regime_gateway_guard_enabled:
                self._log_reject(signal, reason, trade_mode)
                return _Decision(approved=False, kill_switch_fired=False)
            self._log_market_regime_would_reject(
                signal=signal,
                trade_mode=trade_mode,
                regime=regime,
                reason=reason,
            )

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

        quantity = await self._rebudget_buy_quantity(
            signal=signal,
            trade_mode=trade_mode,
            entry_price=entry_price,
            quantity=quantity,
        )
        if quantity is None:
            self._log_reject(signal, "insufficient_live_budget", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        quantity = self._cap_buy_quantity(
            signal=signal,
            trade_mode=trade_mode,
            quantity=quantity,
        )
        if quantity is None:
            self._log_reject(signal, "live_qty_cap_below_min_lot", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        quantity = await self._cap_buy_quantity_by_liquidity(
            signal=signal,
            trade_mode=trade_mode,
            quantity=quantity,
        )
        if quantity is None:
            self._log_reject(signal, "liquidity_qty_cap_below_min_lot", trade_mode)
            return _Decision(approved=False, kill_switch_fired=False)

        order = build_order(
            signal=signal,
            quantity=quantity,
            trade_mode=trade_mode,
            entry_price=entry_price,
            default_stop_loss_spread_pct=self.risk_config.default_stop_loss_spread_pct,
            created_at=now,
        )
        reservation_risk = self._risk_amount_for_order(order=order, entry_price=entry_price)
        if reservation_risk is not None:
            reservation = await self.supabase.reserve_order_risk(
                order_id=order.order_id,
                trade_mode=order.trade_mode,
                trading_date=order.created_at.astimezone(
                    ZoneInfo(self.settings.day_closeout_timezone)
                ).date(),
                symbol=order.symbol,
                side=order.side.value,
                risk_amount=reservation_risk,
                notional_amount=(entry_price or Decimal("0")) * Decimal(order.quantity),
            )
            if not reservation.passed:
                self._log_reject(signal, reservation.reason or "risk_reservation", trade_mode)
                return _Decision(approved=False, kill_switch_fired=False)

        topic = resolve_topic(trade_mode, self.routing)
        try:
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
        except Exception:
            if reservation_risk is not None:
                with contextlib.suppress(Exception):
                    await self.supabase.release_risk_reservation(
                        order_id=order.order_id,
                        reason="publish_failed",
                    )
            raise
        if self.order_archive is not None:
            try:
                self.order_archive.record_order(order)
            except Exception:
                logger.exception(
                    "order archive failed: symbol=%s order_id=%s",
                    order.symbol,
                    order.order_id,
                )
        self._mark_pending_live_order(signal=signal, trade_mode=trade_mode)
        self._record_publish_summary(
            trade_mode=order.trade_mode.value,
            side=order.side.value,
            destination_topic=topic,
        )
        logger.info(
            "order approved: symbol=%s side=%s qty=%d trade_mode=%s signal_id=%s",
            order.symbol,
            order.side.value,
            order.quantity,
            order.trade_mode.value,
            signal.signal_id,
            extra=event_extra(
                "order_published",
                trade_mode=order.trade_mode.value,
                symbol=order.symbol,
                order_id=str(order.order_id),
                signal_id=str(signal.signal_id),
                side=order.side.value,
                quantity=order.quantity,
                destination_topic=topic,
                source=order.signal_source.value,
            ),
        )
        return _Decision(approved=True, kill_switch_fired=False)

    def _risk_amount_for_order(
        self, *, order: OrderRequest, entry_price: Decimal | None
    ) -> Decimal | None:
        if order.trade_mode is not TradeMode.LIVE or order.side.value != "BUY":
            return None
        if entry_price is None or order.stop_loss_price is None:
            return None
        risk_per_share = entry_price - order.stop_loss_price
        if risk_per_share <= 0:
            return None
        return risk_per_share * Decimal(order.quantity)

    def _pending_live_order_key(
        self, *, signal: UnifiedTradeSignal, trade_mode: TradeMode
    ) -> tuple[str, str, str]:
        return (trade_mode.value, signal.symbol, signal.action.value)

    def _prune_pending_live_orders(self, *, now: float) -> None:
        expired = [
            key for key, deadline in self._pending_live_order_deadlines.items() if deadline <= now
        ]
        for key in expired:
            self._pending_live_order_deadlines.pop(key, None)

    def _has_pending_live_order(self, *, signal: UnifiedTradeSignal, trade_mode: TradeMode) -> bool:
        now = self.monotonic()
        self._prune_pending_live_orders(now=now)
        return (
            self._pending_live_order_key(signal=signal, trade_mode=trade_mode)
            in self._pending_live_order_deadlines
        )

    def _mark_pending_live_order(
        self, *, signal: UnifiedTradeSignal, trade_mode: TradeMode
    ) -> None:
        cooldown = self.settings.live_symbol_order_cooldown_seconds
        if cooldown <= 0:
            return
        now = self.monotonic()
        self._prune_pending_live_orders(now=now)
        self._pending_live_order_deadlines[
            self._pending_live_order_key(signal=signal, trade_mode=trade_mode)
        ] = now + cooldown

    async def _rebudget_buy_quantity(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        entry_price: Decimal | None,
        quantity: int,
    ) -> int | None:
        if signal.action is not Action.BUY:
            return quantity
        if entry_price is None:
            return quantity

        capital = (
            await self._resolve_live_capital()
            if trade_mode is TradeMode.LIVE
            else self.risk_config.capital
        )
        exposure = await self.supabase.read_capital_in_use(trade_mode=trade_mode)
        remaining_capital = capital - exposure
        if remaining_capital <= 0:
            logger.warning(
                "buy budget exhausted: symbol=%s trade_mode=%s exposure=%s capital=%s",
                signal.symbol,
                trade_mode.value,
                exposure,
                capital,
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
                "buy rejected by remaining budget: "
                "symbol=%s trade_mode=%s exposure=%s remaining_capital=%s reason=%s",
                signal.symbol,
                trade_mode.value,
                exposure,
                remaining_capital,
                check.reason,
            )
            return None
        if rebudgeted < quantity:
            logger.info(
                "buy quantity reduced by exposure budget: "
                "symbol=%s trade_mode=%s qty=%d rebudgeted=%d exposure=%s remaining_capital=%s",
                signal.symbol,
                trade_mode.value,
                quantity,
                rebudgeted,
                exposure,
                remaining_capital,
            )
        return rebudgeted

    async def _resolve_live_capital(self) -> Decimal:
        if self.kabu is None:
            return self.risk_config.capital
        try:
            capital = await self.kabu.read_stock_account_wallet()
        except Exception as exc:
            if self._cached_live_capital is not None:
                logger.warning(
                    "kabu wallet read failed; using cached live capital: capital=%s error=%r",
                    self._cached_live_capital,
                    exc,
                )
                return self._cached_live_capital
            logger.warning(
                "kabu wallet read failed; using configured capital fallback: capital=%s error=%r",
                self.risk_config.capital,
                exc,
            )
            return self.risk_config.capital
        self._cached_live_capital = capital
        return capital

    def _signal_age_seconds(self, *, signal: UnifiedTradeSignal, now: datetime) -> float:
        return max(0.0, (now - signal.created_at).total_seconds())

    def _is_stale_signal(
        self,
        *,
        signal: UnifiedTradeSignal,
        now: datetime,
    ) -> bool:
        max_age = self.settings.live_signal_max_age_seconds
        if max_age is None:
            return False
        return self._signal_age_seconds(signal=signal, now=now) > max_age

    def _is_day_session_closed(
        self,
        *,
        holding_type: TradingStyle,
        trade_mode: TradeMode,
    ) -> bool:
        if holding_type is not TradingStyle.DAY:
            return False
        now = self.wall_clock().astimezone(ZoneInfo(self.settings.day_closeout_timezone))
        hh, mm = self.settings.day_closeout_time.split(":", 1)
        close_h, close_m = int(hh), int(mm)
        return (now.hour, now.minute) >= (close_h, close_m)

    def _is_day_late_buy(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        now: datetime,
    ) -> bool:
        if signal.holding_type is not TradingStyle.DAY or signal.action is not Action.BUY:
            return False
        local_now = now.astimezone(ZoneInfo(self.settings.day_closeout_timezone))
        hh, mm = self.settings.live_day_new_buy_cutoff_time.split(":", 1)
        cutoff_h, cutoff_m = int(hh), int(mm)
        return (local_now.hour, local_now.minute) >= (cutoff_h, cutoff_m)

    def _is_day_opening_buy(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        now: datetime,
    ) -> bool:
        if signal.holding_type is not TradingStyle.DAY or signal.action is not Action.BUY:
            return False
        local_now = now.astimezone(ZoneInfo(self.settings.day_closeout_timezone))
        hh, mm = self.settings.live_day_new_buy_start_time.split(":", 1)
        start_h, start_m = int(hh), int(mm)
        return (local_now.hour, local_now.minute) < (start_h, start_m)

    async def _has_same_symbol_sell_today(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        now: datetime,
    ) -> bool:
        if (
            not self.settings.day_same_symbol_reentry_block_enabled
            or signal.holding_type is not TradingStyle.DAY
            or signal.action is not Action.BUY
        ):
            return False
        tz = ZoneInfo(self.settings.day_closeout_timezone)
        local_now = now.astimezone(tz)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        return await self.supabase.has_sell_since(
            symbol=signal.symbol, trade_mode=trade_mode, since=day_start
        )

    def _soft_loss_throttle_blocks_buy(
        self, *, signal: UnifiedTradeSignal, state: KillSwitchState
    ) -> bool:
        if (
            signal.action is not Action.BUY
            or signal.signal_source is not SignalSource.RULE
            or (
                not self.settings.soft_loss_throttle_log_only_enabled
                and not self.settings.soft_loss_throttle_guard_enabled
            )
        ):
            return False
        if self.settings.soft_loss_limit_jpy <= 0:
            return False
        return state.daily_pnl <= -self.settings.soft_loss_limit_jpy

    async def _read_market_regime_for_signal(
        self, *, signal: UnifiedTradeSignal, now: datetime
    ) -> MarketRegimeState | None:
        if signal.action is not Action.BUY or (
            not self.settings.market_regime_gateway_log_only_enabled
            and not self.settings.market_regime_gateway_guard_enabled
        ):
            return None
        tz = ZoneInfo(self.settings.day_closeout_timezone)
        valid_date = now.astimezone(tz).date()
        return await self.supabase.read_market_regime(valid_date=valid_date)

    def _market_regime_blocks_buy(
        self, *, signal: UnifiedTradeSignal, regime: MarketRegimeState
    ) -> bool:
        if signal.action is not Action.BUY:
            return False
        return regime.regime in {"RISK_OFF", "CRASH"} or not regime.buy_enabled

    def _cap_buy_quantity(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        quantity: int,
    ) -> int | None:
        if signal.action is not Action.BUY:
            return quantity

        cap = self.settings.oms_live_max_qty_per_order
        if cap is None or quantity <= cap:
            return quantity

        capped = (cap // self.risk_config.min_lot_size) * self.risk_config.min_lot_size
        if capped < self.risk_config.min_lot_size:
            logger.warning(
                "buy quantity cap below min lot: symbol=%s trade_mode=%s qty=%d cap=%d min_lot=%d",
                signal.symbol,
                trade_mode.value,
                quantity,
                cap,
                self.risk_config.min_lot_size,
            )
            return None

        logger.info(
            "buy quantity capped: symbol=%s trade_mode=%s qty=%d capped=%d cap=%d",
            signal.symbol,
            trade_mode.value,
            quantity,
            capped,
            cap,
        )
        return capped

    async def _cap_buy_quantity_by_liquidity(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        quantity: int,
    ) -> int | None:
        if signal.action is not Action.BUY or not self.settings.liquidity_sizing_enabled:
            return quantity

        snapshot = await self.supabase.read_latest_daily_liquidity(symbol=signal.symbol)
        if snapshot is None:
            cap = (
                self.settings.liquidity_missing_daily_max_qty_per_order
                // self.risk_config.min_lot_size
            ) * self.risk_config.min_lot_size
            if cap < self.risk_config.min_lot_size:
                logger.warning(
                    "missing daily liquidity cap below min lot: "
                    "symbol=%s trade_mode=%s qty=%d cap=%d min_lot=%d",
                    signal.symbol,
                    trade_mode.value,
                    quantity,
                    cap,
                    self.risk_config.min_lot_size,
                )
                return None
            if quantity > cap:
                logger.info(
                    "buy quantity capped without daily liquidity: "
                    "symbol=%s trade_mode=%s qty=%d capped=%d",
                    signal.symbol,
                    trade_mode.value,
                    quantity,
                    cap,
                )
                return cap
            logger.info(
                "liquidity sizing kept min-size order without daily row: "
                "symbol=%s trade_mode=%s qty=%d",
                signal.symbol,
                trade_mode.value,
                quantity,
            )
            return quantity

        liquidity_cap = self._liquidity_quantity_cap(snapshot)
        if liquidity_cap is None or quantity <= liquidity_cap:
            return quantity
        if liquidity_cap < self.risk_config.min_lot_size:
            logger.warning(
                "liquidity quantity cap below min lot: symbol=%s trade_mode=%s "
                "qty=%d cap=%d min_lot=%d daily_volume=%d daily_turnover=%s",
                signal.symbol,
                trade_mode.value,
                quantity,
                liquidity_cap,
                self.risk_config.min_lot_size,
                snapshot.volume,
                snapshot.turnover,
            )
            return None
        logger.info(
            "buy quantity capped by liquidity: symbol=%s trade_mode=%s qty=%d capped=%d "
            "daily_volume=%d daily_turnover=%s",
            signal.symbol,
            trade_mode.value,
            quantity,
            liquidity_cap,
            snapshot.volume,
            snapshot.turnover,
        )
        return liquidity_cap

    def _liquidity_quantity_cap(self, snapshot: DailyLiquiditySnapshot) -> int | None:
        caps: list[int] = []
        lot = self.risk_config.min_lot_size
        participation = self.settings.liquidity_max_daily_volume_participation_pct
        if snapshot.volume > 0 and participation > 0:
            raw_cap = int(Decimal(snapshot.volume) * participation)
            cap = (raw_cap // lot) * lot
            caps.append(max(cap, lot))
        if (
            snapshot.volume < self.settings.liquidity_thin_daily_volume
            or snapshot.turnover < self.settings.liquidity_thin_daily_turnover_jpy
        ):
            thin_cap = (self.settings.liquidity_thin_max_qty_per_order // lot) * lot
            caps.append(thin_cap)
        if not caps:
            return None
        return min(caps)

    def _log_reject(self, signal: UnifiedTradeSignal, reason: str, trade_mode: TradeMode) -> None:
        self._record_reject_summary(reason=reason)
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
            extra=event_extra(
                "signal_rejected",
                trade_mode=trade_mode.value,
                symbol=signal.symbol,
                signal_id=str(signal.signal_id),
                reason=reason,
                source=signal.signal_source.value,
                holding_type=signal.holding_type.value,
                action=signal.action.value,
                confidence=signal.confidence,
                signal_created_at=signal.created_at.isoformat(),
                age_seconds=round(
                    self._signal_age_seconds(signal=signal, now=self.wall_clock()),
                    3,
                ),
                strategy_signal_id_a=(
                    str(signal.strategy_signal_id_a)
                    if signal.strategy_signal_id_a is not None
                    else None
                ),
                strategy_signal_id_b=(
                    str(signal.strategy_signal_id_b)
                    if signal.strategy_signal_id_b is not None
                    else None
                ),
                has_price=signal.price is not None,
            ),
        )

    def _log_market_regime_would_reject(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        regime: MarketRegimeState,
        reason: str,
    ) -> None:
        logger.info(
            (
                "market regime would reject: symbol=%s action=%s reason=%s "
                "trade_mode=%s signal_id=%s regime=%s confidence=%s buy_enabled=%s"
            ),
            signal.symbol,
            signal.action.value,
            reason,
            trade_mode.value,
            signal.signal_id,
            regime.regime,
            regime.confidence,
            regime.buy_enabled,
            extra=event_extra(
                "market_regime_would_reject",
                trade_mode=trade_mode.value,
                symbol=signal.symbol,
                signal_id=str(signal.signal_id),
                reason=reason,
                source=signal.signal_source.value,
                holding_type=signal.holding_type.value,
                action=signal.action.value,
                confidence=signal.confidence,
                market_regime=regime.regime,
                market_regime_confidence=float(regime.confidence),
                market_regime_buy_enabled=regime.buy_enabled,
                market_regime_position_size_multiplier=float(regime.position_size_multiplier),
                market_regime_source=regime.source,
                market_regime_valid_date=regime.valid_date.isoformat(),
                market_regime_rationale=regime.rationale,
                market_regime_metrics=regime.metrics,
                guard_enabled=self.settings.market_regime_gateway_guard_enabled,
            ),
        )

    def _log_soft_loss_throttle_would_reject(
        self,
        *,
        signal: UnifiedTradeSignal,
        trade_mode: TradeMode,
        state: KillSwitchState,
        reason: str,
    ) -> None:
        logger.info(
            (
                "soft loss throttle would reject: symbol=%s action=%s reason=%s "
                "trade_mode=%s signal_id=%s daily_pnl=%s soft_loss_limit=%s"
            ),
            signal.symbol,
            signal.action.value,
            reason,
            trade_mode.value,
            signal.signal_id,
            state.daily_pnl,
            self.settings.soft_loss_limit_jpy,
            extra=event_extra(
                "soft_loss_throttle_would_reject",
                trade_mode=trade_mode.value,
                symbol=signal.symbol,
                signal_id=str(signal.signal_id),
                reason=reason,
                source=signal.signal_source.value,
                holding_type=signal.holding_type.value,
                action=signal.action.value,
                confidence=signal.confidence,
                daily_pnl=float(state.daily_pnl),
                soft_loss_limit_jpy=float(self.settings.soft_loss_limit_jpy),
                guard_enabled=self.settings.soft_loss_throttle_guard_enabled,
            ),
        )

    def _record_reject_summary(self, *, reason: str) -> None:
        now = self.monotonic()
        if self._reject_summary_started_at is None:
            self._reject_summary_started_at = now
        self._reject_summary_reasons[reason] += 1

        elapsed = now - self._reject_summary_started_at
        if elapsed < self.reject_summary_log_interval_seconds:
            return

        reason_counts = dict(sorted(self._reject_summary_reasons.items()))
        logger.info(
            "signal reject summary: total=%d reasons=%s",
            sum(reason_counts.values()),
            reason_counts,
            extra=event_extra(
                "signal_reject_summary",
                total=sum(reason_counts.values()),
                reason_counts=reason_counts,
                window_seconds=round(elapsed, 3),
            ),
        )
        self._reject_summary_started_at = now
        self._reject_summary_reasons.clear()

    def _record_publish_summary(
        self, *, trade_mode: str, side: str, destination_topic: str
    ) -> None:
        now = self.monotonic()
        if self._publish_summary_started_at is None:
            self._publish_summary_started_at = now
        self._publish_summary_trade_modes[trade_mode] += 1
        self._publish_summary_sides[side] += 1
        self._publish_summary_topics[destination_topic] += 1

        elapsed = now - self._publish_summary_started_at
        if elapsed < self.publish_summary_log_interval_seconds:
            return

        trade_mode_counts = dict(sorted(self._publish_summary_trade_modes.items()))
        side_counts = dict(sorted(self._publish_summary_sides.items()))
        destination_topic_counts = dict(sorted(self._publish_summary_topics.items()))
        total = sum(trade_mode_counts.values())
        logger.info(
            "order publish summary: total=%d trade_modes=%s sides=%s topics=%s",
            total,
            trade_mode_counts,
            side_counts,
            destination_topic_counts,
            extra=event_extra(
                "order_publish_summary",
                total=total,
                trade_mode_counts=trade_mode_counts,
                side_counts=side_counts,
                destination_topic_counts=destination_topic_counts,
                window_seconds=round(elapsed, 3),
            ),
        )
        self._publish_summary_started_at = now
        self._publish_summary_trade_modes.clear()
        self._publish_summary_sides.clear()
        self._publish_summary_topics.clear()


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
