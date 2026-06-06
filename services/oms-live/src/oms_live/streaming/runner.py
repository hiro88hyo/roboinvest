"""Streaming loop for OMS Live.

``live-orders`` を pull し、kabu API へ実発注 → 約定確定までポーリング → Supabase
に書き込み → ack するループ。

at-least-once 規約:

* 実発注 + Supabase 書込が完了した時点で ack。
* 冪等性: sendorder 前に ``trades_live.order_id`` (partial unique index) を
  ``live_trade_exists_for_order_id`` で確認し、重複なら ack して "skipped_duplicate"。
* **Supabase 書込失敗は ``SupabaseError`` を伝播してプロセスを fail-fast で停止**。
  自動再起動はせず、運用者が kabu ``/orders`` と ``trades_live`` を手動整合してから
  再起動する (再配信時は idempotency check で skip される前提)。
* sendorder の Result != 0、約定タイムアウト (cancel_order を best-effort)、
  poison message (parse 失敗) はいずれも ack。
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
from typing import Any, cast

from pydantic import ValidationError
from trade_contracts.enums import TradingStyle
from trade_contracts.logging import event_extra
from trade_contracts.order import OrderRequest

from ..clients.pubsub import PubSubError, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseClient, SupabaseError
from ..closeout import build_closeout_orders
from ..config import OmsLiveSettings
from ..kabu_client import KabuApiError, KabuLiveClient
from ..models import FillResult, LivePosition, PositionUpdate
from ..order_builder import build_sendorder_payload
from ..order_parser import parse_order_state, to_fill_result
from ..position_updater import apply_fill, build_fill_record
from ..reconciler import ReconcileActions, compute_position_diff, parse_kabu_positions

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class BatchStats:
    orders_pulled: int
    parse_errors: int
    filled: int
    no_fills: int
    acked: int
    skipped_duplicate: int = 0
    safety_rejected: int = 0
    dry_run_skipped: int = 0


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
    kabu: KabuLiveClient
    settings: OmsLiveSettings
    idle_backoff_seconds: float = 0.5
    sleep: Sleep = field(default=asyncio.sleep)
    monotonic: MonotonicClock = field(default=time.monotonic)
    wall_clock: WallClock = field(default_factory=lambda: lambda: datetime.now(UTC))
    _pending_broker_order_deadlines: dict[tuple[str, str], float] = field(
        default_factory=dict, init=False
    )

    async def run(self, *, iterations: int | None = None) -> list[BatchStats]:
        """Pull → process → ack を ``iterations`` 回繰り返す。``None`` で無限ループ。"""
        results: list[BatchStats] = []
        count = 0
        while iterations is None or count < iterations:
            stats = await self.run_once()
            results.append(stats)
            if stats.orders_pulled == 0 and self.idle_backoff_seconds > 0:
                await self.sleep(self.idle_backoff_seconds)
            count += 1
        return results

    async def run_once(self) -> BatchStats:
        """1 バッチ分の pull + 処理 + ack。``SupabaseError`` は ack flush 後に再 raise。"""
        order_msgs = await self.subscriber.pull(
            self.settings.pubsub_subscription_live_orders,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        parse_errors = 0
        filled = 0
        no_fills = 0
        skipped_duplicate = 0
        safety_rejected = 0
        dry_run_skipped = 0
        acks: list[str] = []

        try:
            for msg in order_msgs:
                order = _parse_order(msg)
                if order is None:
                    parse_errors += 1
                    acks.append(msg.ack_id)
                    continue
                outcome = await self._process_order(order)
                if outcome == "filled":
                    filled += 1
                elif outcome == "skipped_duplicate":
                    skipped_duplicate += 1
                elif outcome == "safety_rejected":
                    safety_rejected += 1
                elif outcome == "dry_run_skipped":
                    dry_run_skipped += 1
                else:
                    no_fills += 1
                acks.append(msg.ack_id)
        except SupabaseError:
            logger.critical(
                "supabase write failure detected: aborting runner (fail-fast). "
                "manual reconciliation required before restart "
                "(check kabu /orders and trades_live consistency)."
            )
            await self._flush_acks_on_shutdown(acks)
            raise

        if acks:
            await self.subscriber.acknowledge(self.settings.pubsub_subscription_live_orders, acks)

        return BatchStats(
            orders_pulled=len(order_msgs),
            parse_errors=parse_errors,
            filled=filled,
            no_fills=no_fills,
            acked=len(acks),
            skipped_duplicate=skipped_duplicate,
            safety_rejected=safety_rejected,
            dry_run_skipped=dry_run_skipped,
        )

    async def _flush_acks_on_shutdown(self, acks: list[str]) -> None:
        """fail-fast raise 直前の best-effort ack flush。失敗してもログのみで握り潰す。"""
        if not acks:
            return
        try:
            await self.subscriber.acknowledge(self.settings.pubsub_subscription_live_orders, acks)
        except Exception:
            logger.exception(
                "ack flush failed during fail-fast shutdown (acks=%d will redeliver)", len(acks)
            )

    async def _process_order(self, order: OrderRequest) -> str:
        """``"filled" | "no_fill" | "skipped_duplicate" | "safety_rejected" | "dry_run_skipped"``。

        Supabase 書込失敗時は ``SupabaseError`` を伝播する (``run_once`` で fail-fast)。
        """

        # 0) idempotency check: redeliver された (前回完全成功済) 注文は弾く
        if await self.supabase.live_trade_exists_for_order_id(order.order_id):
            logger.info(
                "live order skipped (already filled): order_id=%s symbol=%s signal_id=%s",
                order.order_id,
                order.symbol,
                order.unified_signal_id,
                extra=event_extra(
                    "order_skipped_duplicate",
                    order_id=str(order.order_id),
                    symbol=order.symbol,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                ),
            )
            return "skipped_duplicate"

        # 0.5) safety knobs (Phase 3): allowed_symbols / max_qty / dry_run
        allowed = self.settings.allowed_symbol_set
        if allowed and order.symbol not in allowed:
            logger.warning(
                "live order rejected by allowed_symbols: order_id=%s symbol=%s allowed=%s",
                order.order_id,
                order.symbol,
                sorted(allowed),
                extra=event_extra(
                    "order_safety_rejected",
                    reason="allowed_symbols",
                    order_id=str(order.order_id),
                    symbol=order.symbol,
                    allowed_symbols=sorted(allowed),
                ),
            )
            return "safety_rejected"
        max_qty = self.settings.oms_live_max_qty_per_order
        if max_qty is not None and order.side.value == "BUY" and order.quantity > max_qty:
            logger.warning(
                "live order rejected by max_qty_per_order: order_id=%s symbol=%s qty=%d max=%d",
                order.order_id,
                order.symbol,
                order.quantity,
                max_qty,
                extra=event_extra(
                    "order_safety_rejected",
                    reason="max_qty_per_order",
                    order_id=str(order.order_id),
                    symbol=order.symbol,
                    quantity=order.quantity,
                    max_quantity=max_qty,
                ),
            )
            return "safety_rejected"
        if self.settings.oms_live_dry_run:
            logger.info(
                "live order skipped (DRY_RUN): order_id=%s symbol=%s side=%s qty=%d",
                order.order_id,
                order.symbol,
                order.side.value,
                order.quantity,
                extra=event_extra(
                    "order_dry_run_skipped",
                    order_id=str(order.order_id),
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=order.quantity,
                ),
            )
            return "dry_run_skipped"

        if self._has_pending_broker_order(order):
            logger.warning(
                "live order rejected by pending broker order: order_id=%s symbol=%s side=%s",
                order.order_id,
                order.symbol,
                order.side.value,
                extra=event_extra(
                    "order_safety_rejected",
                    reason="pending_broker_order",
                    order_id=str(order.order_id),
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=order.quantity,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                ),
            )
            return "safety_rejected"

        # 1) sendorder
        payload = build_sendorder_payload(
            order,
            password=self.settings.kabu_order_password,
            exchange=self.settings.kabu_default_exchange,
            account_type=self.settings.kabu_account_type,
        )
        try:
            send_resp = await self._call_kabu_with_auth_retry(
                lambda: self.kabu.send_order(payload),
                operation=f"sendorder symbol={order.symbol}",
            )
        except KabuApiError as exc:
            broker_code, broker_message = _broker_error_fields(exc)
            logger.exception(
                "kabu sendorder failed: symbol=%s signal_id=%s",
                order.symbol,
                order.unified_signal_id,
                extra=event_extra(
                    "broker_order_failed",
                    phase="sendorder",
                    symbol=order.symbol,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                    order_id=str(order.order_id),
                    broker_status_code=exc.status_code,
                    broker_code=broker_code,
                    broker_message=broker_message,
                ),
            )
            return "no_fill"
        if int(send_resp.get("Result", -1)) != 0:
            logger.warning(
                "kabu sendorder rejected: symbol=%s signal_id=%s body=%s",
                order.symbol,
                order.unified_signal_id,
                send_resp,
                extra=event_extra(
                    "broker_order_rejected",
                    symbol=order.symbol,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                    order_id=str(order.order_id),
                    broker_result=send_resp.get("Result"),
                    broker_code=send_resp.get("Code"),
                    broker_message=send_resp.get("Message"),
                ),
            )
            return "no_fill"
        kabu_order_id = str(send_resp.get("OrderId") or "")
        if not kabu_order_id:
            logger.warning(
                "kabu sendorder returned empty OrderId: symbol=%s body=%s",
                order.symbol,
                send_resp,
                extra=event_extra(
                    "broker_order_failed",
                    phase="sendorder_empty_order_id",
                    symbol=order.symbol,
                    order_id=str(order.order_id),
                    broker_result=send_resp.get("Result"),
                    broker_code=send_resp.get("Code"),
                    broker_message=send_resp.get("Message"),
                ),
            )
            return "no_fill"
        self._mark_pending_broker_order(order)

        # 2) poll until State==3 (done) or timeout
        fill = await self._poll_until_filled(kabu_order_id)
        if fill is None:
            # タイムアウト → cancel して no_fill 扱いで ack
            await self._best_effort_cancel(kabu_order_id)
            return "no_fill"
        self._clear_pending_broker_order(order)
        if fill.filled_quantity == 0 or fill.fill_price is None:
            logger.info(
                "live order finished without fill: symbol=%s reason=%s signal_id=%s",
                order.symbol,
                fill.reason,
                order.unified_signal_id,
                extra=event_extra(
                    "order_no_fill",
                    symbol=order.symbol,
                    reason=fill.reason,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                    order_id=str(order.order_id),
                    broker_order_id=kabu_order_id,
                ),
            )
            return "no_fill"

        # 3) apply to existing position + write Supabase
        existing = await self.supabase.read_live_position(symbol=order.symbol)
        holding_type = existing.holding_type if existing is not None else TradingStyle.DAY
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
                "live apply_fill error: symbol=%s error=%s signal_id=%s",
                order.symbol,
                update.error,
                order.unified_signal_id,
                extra=event_extra(
                    "order_apply_fill_failed",
                    symbol=order.symbol,
                    reason=update.error,
                    unified_signal_id=str(order.unified_signal_id)
                    if order.unified_signal_id
                    else None,
                    order_id=str(order.order_id),
                    broker_order_id=kabu_order_id,
                ),
            )
            return "no_fill"

        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is None:
            return "no_fill"

        # 書込順序: trades_live INSERT → positions の write → realized PnL 加算
        await self.supabase.insert_trade_live(record)
        await self._write_position_change(
            existing=existing,
            update=update,
            symbol=order.symbol,
        )
        if update.realized_pnl is not None:
            await self.supabase.add_realized_pnl(update.realized_pnl)
        logger.info(
            "live order filled: symbol=%s side=%s qty=%d price=%s pnl=%s signal_id=%s",
            record.symbol,
            record.side.value,
            record.quantity,
            record.price,
            update.realized_pnl,
            order.unified_signal_id,
            extra=event_extra(
                "order_filled",
                symbol=record.symbol,
                side=record.side.value,
                quantity=record.quantity,
                price=str(record.price),
                realized_pnl=str(update.realized_pnl) if update.realized_pnl is not None else None,
                unified_signal_id=str(order.unified_signal_id) if order.unified_signal_id else None,
                order_id=str(order.order_id),
                broker_order_id=kabu_order_id,
            ),
        )
        return "filled"

    def _pending_broker_order_key(self, order: OrderRequest) -> tuple[str, str]:
        return (order.symbol, order.side.value)

    def _prune_pending_broker_orders(self, *, now: float) -> None:
        expired = [
            key for key, deadline in self._pending_broker_order_deadlines.items() if deadline <= now
        ]
        for key in expired:
            self._pending_broker_order_deadlines.pop(key, None)

    def _has_pending_broker_order(self, order: OrderRequest) -> bool:
        now = self.monotonic()
        self._prune_pending_broker_orders(now=now)
        return self._pending_broker_order_key(order) in self._pending_broker_order_deadlines

    def _mark_pending_broker_order(self, order: OrderRequest) -> None:
        cooldown = self.settings.oms_live_pending_order_cooldown_seconds
        if cooldown <= 0:
            return
        now = self.monotonic()
        self._prune_pending_broker_orders(now=now)
        self._pending_broker_order_deadlines[self._pending_broker_order_key(order)] = now + cooldown

    def _clear_pending_broker_order(self, order: OrderRequest) -> None:
        self._pending_broker_order_deadlines.pop(self._pending_broker_order_key(order), None)

    async def _poll_until_filled(
        self, kabu_order_id: str, *, timeout_seconds: float | None = None
    ) -> FillResult | None:
        """終端 (filled / partial / cancelled) までポーリング。タイムアウトで ``None``。

        kabu State の実態は ``order_parser.py`` の docstring 参照:
        ``State=3`` は中間状態 (約定 Detail 未着、CumQty=0) でも出現するため、
        ``state==3`` 単独で break してはならない。``to_fill_result`` の reason のみで
        終端判定する。
        """
        timeout = (
            self.settings.order_fill_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        deadline = self.monotonic() + timeout
        interval = max(self.settings.order_fill_poll_interval_seconds, 0.0)
        while True:
            try:
                payload = await self._call_kabu_with_auth_retry(
                    lambda: self.kabu.get_order(kabu_order_id),
                    operation=f"get_order order_id={kabu_order_id}",
                )
            except KabuApiError:
                logger.exception("kabu get_order failed: order_id=%s", kabu_order_id)
                return None
            try:
                state = parse_order_state(payload)
            except (ValueError, KeyError):
                logger.exception("kabu order parse failed: order_id=%s", kabu_order_id)
                return None
            fill = to_fill_result(state)
            if fill.reason in {"filled", "partial", "cancelled"}:
                logger.info(
                    "kabu order poll terminal: order_id=%s reason=%s summary=%s",
                    kabu_order_id,
                    fill.reason,
                    _summarize_order_state(state),
                    extra=event_extra(
                        "broker_order_terminal",
                        broker_order_id=kabu_order_id,
                        reason=fill.reason,
                        order_state=_summarize_order_state(state),
                    ),
                )
                return fill
            if self.monotonic() >= deadline:
                logger.warning(
                    "kabu order poll timeout: order_id=%s timeout=%s summary=%s",
                    kabu_order_id,
                    timeout,
                    _summarize_order_state(state),
                    extra=event_extra(
                        "broker_order_timeout",
                        broker_order_id=kabu_order_id,
                        timeout_seconds=timeout,
                        order_state=_summarize_order_state(state),
                    ),
                )
                return None
            if interval > 0:
                await self.sleep(interval)

    async def _best_effort_cancel(self, kabu_order_id: str) -> None:
        try:
            await self._call_kabu_with_auth_retry(
                lambda: self.kabu.cancel_order(
                    order_id=kabu_order_id,
                    password=self.settings.kabu_order_password,
                ),
                operation=f"cancel_order order_id={kabu_order_id}",
            )
        except KabuApiError:
            logger.exception("kabu cancel_order failed: order_id=%s", kabu_order_id)

    async def _write_position_change(
        self,
        *,
        existing: LivePosition | None,
        update: PositionUpdate,
        symbol: str,
    ) -> None:
        if update.delete:
            await self.supabase.delete_live_position(symbol=symbol)
            return
        if update.position is None:
            return
        if existing is None:
            await self.supabase.insert_live_position(update.position)
        else:
            await self.supabase.update_live_position_quantity(
                symbol=symbol,
                quantity=update.position.quantity,
                entry_price=str(update.position.entry_price),
            )

    # ------------------------------------------------------------------
    # closeout (14:50 JST cron)
    # ------------------------------------------------------------------

    async def run_closeout(self) -> CloseoutStats:
        """live 全建玉の強制決済。``trading_style != day`` なら no-op。

        closeout 由来の OrderRequest / 約定行は対応する ``aggregator_logs`` 行を
        持たないため ``unified_signal_id`` は ``None`` で書き込む。

        Phase 3 安全装備:
        - ``oms_live_dry_run=True`` のときは Supabase / kabu に一切触れず即 no-op
        - ``allowed_symbols`` / ``max_qty_per_order`` は closeout には適用しない
          (持ち越し決済を阻害しないため)
        """
        if self.settings.oms_live_dry_run:
            logger.info(
                "closeout: skipped (DRY_RUN)",
                extra=event_extra("closeout_skipped", reason="dry_run"),
            )
            return CloseoutStats(
                triggered=False,
                skipped_reason="dry_run",
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )
        try:
            state = await self.supabase.read_system_status()
        except SupabaseError:
            logger.exception(
                "closeout: read_system_status failed",
                extra=event_extra("closeout_skipped", reason="read_system_status_failed"),
            )
            return CloseoutStats(
                triggered=False,
                skipped_reason="read_system_status_failed",
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )
        if state.trading_style is not TradingStyle.DAY:
            logger.info(
                "closeout: skipped (trading_style=%s)",
                state.trading_style.value,
                extra=event_extra(
                    "closeout_skipped",
                    reason=f"trading_style_{state.trading_style.value}",
                    trading_style=state.trading_style.value,
                ),
            )
            return CloseoutStats(
                triggered=False,
                skipped_reason=f"trading_style_{state.trading_style.value}",
                positions_seen=0,
                closed=0,
                no_fills=0,
                write_errors=0,
            )

        positions = await self.supabase.list_live_positions()
        logger.info(
            "closeout: started positions=%d",
            len(positions),
            extra=event_extra("closeout_started", positions_seen=len(positions)),
        )
        precheck = await self._check_closeout_position_drift(
            supabase_positions=positions,
            phase="precheck",
        )
        if precheck is None:
            await self._disable_trading_after_closeout_failure(
                reason="position_check_failed",
                positions_seen=len(positions),
            )
            return CloseoutStats(
                triggered=False,
                skipped_reason="position_check_failed",
                positions_seen=len(positions),
                closed=0,
                no_fills=0,
                write_errors=0,
            )
        if precheck.has_drift:
            logger.critical(
                "closeout: blocked by position drift before sendorder",
                extra=event_extra(
                    "closeout_blocked",
                    reason="position_drift",
                    positions_seen=len(positions),
                ),
            )
            await self._disable_trading_after_closeout_failure(
                reason="position_drift",
                positions_seen=len(positions),
            )
            return CloseoutStats(
                triggered=False,
                skipped_reason="position_drift",
                positions_seen=len(positions),
                closed=0,
                no_fills=0,
                write_errors=0,
            )
        if not positions:
            logger.info(
                "closeout: completed positions=0 closed=0 no_fills=0 write_errors=0",
                extra=event_extra(
                    "closeout_completed",
                    positions_seen=0,
                    closed=0,
                    no_fills=0,
                    write_errors=0,
                    realized_pnl=str(Decimal("0")),
                ),
            )
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

        existing_by_symbol = {p.symbol: p for p in positions}
        tasks = [
            self._process_closeout_order(
                order=order,
                existing=existing_by_symbol.get(order.symbol),
            )
            for order in orders
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        closed = 0
        no_fills = 0
        write_errors = 0
        realized_pnl_total = Decimal("0")
        for order, outcome in zip(orders, outcomes, strict=True):
            if isinstance(outcome, SupabaseError):
                logger.exception(
                    "closeout: write failure symbol=%s",
                    order.symbol,
                    exc_info=outcome,
                    extra=event_extra("closeout_write_failed", symbol=order.symbol),
                )
                write_errors += 1
                continue
            if isinstance(outcome, Exception):
                logger.exception(
                    "closeout: unexpected failure symbol=%s",
                    order.symbol,
                    exc_info=outcome,
                    extra=event_extra("closeout_order_failed", symbol=order.symbol),
                )
                no_fills += 1
                continue
            status, realized_pnl = cast(tuple[str, Decimal], outcome)
            if status == "filled":
                closed += 1
                realized_pnl_total += realized_pnl
            else:
                no_fills += 1

        if realized_pnl_total != Decimal("0"):
            try:
                await self.supabase.add_realized_pnl(realized_pnl_total)
            except SupabaseError:
                logger.exception(
                    "closeout: aggregate pnl update failed",
                    extra=event_extra(
                        "closeout_write_failed",
                        phase="aggregate_pnl",
                        realized_pnl=str(realized_pnl_total),
                    ),
                )
                write_errors += 1

        invariant_ok = await self._log_closeout_remaining_positions()
        if invariant_ok is False:
            await self._disable_trading_after_closeout_failure(
                reason="remaining_positions",
                positions_seen=len(positions),
            )
        logger.info(
            "closeout: completed positions=%d closed=%d no_fills=%d write_errors=%d pnl=%s",
            len(positions),
            closed,
            no_fills,
            write_errors,
            realized_pnl_total,
            extra=event_extra(
                "closeout_completed",
                positions_seen=len(positions),
                closed=closed,
                no_fills=no_fills,
                write_errors=write_errors,
                realized_pnl=str(realized_pnl_total),
            ),
        )

        return CloseoutStats(
            triggered=True,
            skipped_reason=None,
            positions_seen=len(positions),
            closed=closed,
            no_fills=no_fills,
            write_errors=write_errors,
        )

    async def _check_closeout_position_drift(
        self,
        *,
        supabase_positions: list[LivePosition],
        phase: str,
    ) -> ReconcileActions | None:
        """closeout 前後の kabu/Supabase 建玉差分を強いログで可視化する。"""
        try:
            kabu_rows = await self._call_kabu_with_auth_retry(
                lambda: self.kabu.list_positions(product=1),
                operation=f"list_positions closeout {phase}",
            )
            kabu_positions = parse_kabu_positions(kabu_rows)
        except KabuApiError:
            logger.exception(
                "closeout: %s failed reading kabu positions",
                phase,
                extra=event_extra("closeout_position_check_failed", phase=phase),
            )
            return None

        actions = compute_position_diff(kabu_positions, supabase_positions)
        if not actions.has_drift:
            logger.info(
                "closeout: %s positions matched symbols=%s",
                phase,
                list(actions.matched),
                extra=event_extra(
                    "closeout_position_check_matched",
                    phase=phase,
                    matched_symbols=list(actions.matched),
                ),
            )
            return actions

        supabase_summary = [f"{p.symbol}:{p.quantity}@{p.entry_price}" for p in supabase_positions]
        kabu_summary = [f"{p.symbol}:{p.quantity}@{p.average_price}" for p in kabu_positions]
        mismatch_summary = [
            f"{m.symbol}:kabu={m.kabu_quantity}@{m.kabu_average_price},"
            f"supabase={m.supabase_quantity}@{m.supabase_entry_price}"
            for m in actions.quantity_mismatches
        ]
        logger.critical(
            "closeout: %s position drift supabase=%s kabu=%s matched=%s "
            "to_import=%s mismatches=%s supabase_orphans=%s",
            phase,
            supabase_summary,
            kabu_summary,
            list(actions.matched),
            [f"{p.symbol}:{p.quantity}@{p.average_price}" for p in actions.to_import],
            mismatch_summary,
            [f"{p.symbol}:{p.quantity}@{p.entry_price}" for p in actions.supabase_orphans],
            extra=event_extra(
                "closeout_position_drift",
                phase=phase,
                supabase_positions=supabase_summary,
                kabu_positions=kabu_summary,
                matched_symbols=list(actions.matched),
                import_symbols=[p.symbol for p in actions.to_import],
                mismatch_symbols=[m.symbol for m in actions.quantity_mismatches],
                supabase_orphan_symbols=[p.symbol for p in actions.supabase_orphans],
            ),
        )
        return actions

    async def _log_closeout_remaining_positions(self) -> bool | None:
        """closeout 後に残った live 建玉を強いログで可視化する。"""
        try:
            supabase_positions = await self.supabase.list_live_positions()
        except SupabaseError:
            logger.exception(
                "closeout: post-check failed reading Supabase positions",
                extra=event_extra("closeout_position_check_failed", phase="postcheck_supabase"),
            )
            return None
        try:
            kabu_rows = await self._call_kabu_with_auth_retry(
                lambda: self.kabu.list_positions(product=1),
                operation="list_positions closeout postcheck",
            )
            kabu_positions = parse_kabu_positions(kabu_rows)
        except KabuApiError:
            logger.exception(
                "closeout: postcheck failed reading kabu positions",
                extra=event_extra("closeout_position_check_failed", phase="postcheck_kabu"),
            )
            return None

        ok = not supabase_positions and not kabu_positions
        supabase_symbols = [p.symbol for p in supabase_positions]
        kabu_symbols = [p.symbol for p in kabu_positions]
        log = logger.info if ok else logger.critical
        log(
            "closeout invariant: ok=%s supabase_remaining=%d kabu_remaining=%d",
            ok,
            len(supabase_positions),
            len(kabu_positions),
            extra=event_extra(
                "closeout_invariant",
                ok=ok,
                supabase_remaining=len(supabase_positions),
                kabu_remaining=len(kabu_positions),
                supabase_symbols=supabase_symbols,
                kabu_symbols=kabu_symbols,
            ),
        )
        if ok:
            logger.info(
                "closeout: postcheck clear (no live positions remain)",
                extra=event_extra("closeout_postcheck_clear"),
            )
            return True
        await self._check_closeout_position_drift(
            supabase_positions=supabase_positions,
            phase="postcheck",
        )
        return False

    async def _disable_trading_after_closeout_failure(
        self,
        *,
        reason: str,
        positions_seen: int,
    ) -> None:
        try:
            await self.supabase.set_trading_allowed(False)
        except SupabaseError:
            logger.exception(
                "closeout: failed to disable trading after invariant violation",
                extra=event_extra(
                    "closeout_disable_trading_failed",
                    reason=reason,
                    positions_seen=positions_seen,
                ),
            )
            return
        logger.critical(
            "closeout: disabled trading after invariant violation reason=%s positions=%d",
            reason,
            positions_seen,
            extra=event_extra(
                "closeout_trading_disabled",
                reason=reason,
                positions_seen=positions_seen,
            ),
        )

    async def _process_closeout_order(
        self, *, order: OrderRequest, existing: LivePosition | None
    ) -> tuple[str, Decimal]:
        """closeout 1 件分。実発注 → 約定確認 → Supabase 書込。

        通常の ``_process_order`` とほぼ同じだが、``existing.holding_type`` を
        引き継いで apply_fill に渡す点が違う。
        """
        payload = build_sendorder_payload(
            order,
            password=self.settings.kabu_order_password,
            exchange=self.settings.kabu_default_exchange,
            account_type=self.settings.kabu_account_type,
        )
        try:
            send_resp = await self._call_kabu_with_auth_retry(
                lambda: self.kabu.send_order(payload),
                operation=f"sendorder symbol={order.symbol}",
            )
        except KabuApiError as exc:
            broker_code, broker_message = _broker_error_fields(exc)
            logger.exception(
                "closeout sendorder failed: symbol=%s",
                order.symbol,
                extra=event_extra(
                    "broker_order_failed",
                    phase="closeout_sendorder",
                    symbol=order.symbol,
                    order_id=str(order.order_id),
                    broker_status_code=exc.status_code,
                    broker_code=broker_code,
                    broker_message=broker_message,
                ),
            )
            return "no_fill", Decimal("0")
        if int(send_resp.get("Result", -1)) != 0:
            logger.warning(
                "closeout sendorder rejected: symbol=%s body=%s",
                order.symbol,
                send_resp,
                extra=event_extra(
                    "broker_order_rejected",
                    phase="closeout",
                    symbol=order.symbol,
                    order_id=str(order.order_id),
                    broker_result=send_resp.get("Result"),
                    broker_code=send_resp.get("Code"),
                    broker_message=send_resp.get("Message"),
                ),
            )
            return "no_fill", Decimal("0")
        kabu_order_id = str(send_resp.get("OrderId") or "")
        if not kabu_order_id:
            return "no_fill", Decimal("0")

        fill = await self._poll_until_filled(
            kabu_order_id,
            timeout_seconds=self.settings.closeout_order_fill_timeout_seconds,
        )
        if fill is None:
            await self._best_effort_cancel(kabu_order_id)
            return "no_fill", Decimal("0")
        if fill.filled_quantity == 0 or fill.fill_price is None:
            return "no_fill", Decimal("0")

        holding_type = existing.holding_type if existing else TradingStyle.DAY
        update = apply_fill(
            order=order,
            fill=fill,
            existing=existing,
            holding_type=holding_type,
            executed_at=order.created_at,
        )
        if update.error is not None:
            logger.warning(
                "closeout apply_fill error: symbol=%s error=%s",
                order.symbol,
                update.error,
                extra=event_extra(
                    "order_apply_fill_failed",
                    phase="closeout",
                    symbol=order.symbol,
                    reason=update.error,
                    order_id=str(order.order_id),
                    broker_order_id=kabu_order_id,
                ),
            )
            return "no_fill", Decimal("0")
        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is None:
            return "no_fill", Decimal("0")

        await self.supabase.insert_trade_live(record)
        await self._write_position_change(existing=existing, update=update, symbol=order.symbol)
        logger.info(
            "closeout order filled: symbol=%s qty=%d price=%s pnl=%s",
            record.symbol,
            record.quantity,
            record.price,
            update.realized_pnl,
            extra=event_extra(
                "closeout_order_filled",
                symbol=record.symbol,
                side=record.side.value,
                quantity=record.quantity,
                price=str(record.price),
                realized_pnl=str(update.realized_pnl) if update.realized_pnl is not None else None,
                order_id=str(order.order_id),
                broker_order_id=kabu_order_id,
            ),
        )
        return "filled", update.realized_pnl or Decimal("0")

    async def _call_kabu_with_auth_retry(
        self,
        call: Callable[[], Awaitable[Any]],
        *,
        operation: str,
    ) -> Any:
        try:
            return await call()
        except KabuApiError as exc:
            if exc.status_code not in (401, 403):
                raise
            logger.warning(
                "kabu auth lost during %s (status=%s); invalidating token and retrying once",
                operation,
                exc.status_code,
            )
            self.kabu.invalidate_token()
            return await call()


def _summarize_order_state(state: Any) -> dict[str, Any]:
    return {
        "symbol": state.symbol,
        "side": state.side.value,
        "state": state.state,
        "order_state": state.order_state,
        "order_qty": state.order_qty,
        "cum_qty": state.cum_qty,
        "price": str(state.price),
        "details": [
            {
                "rec_type": d.rec_type,
                "qty": d.quantity,
                "price": str(d.price),
                "execution_time": d.execution_time.isoformat(),
            }
            for d in state.details
        ],
    }


def _broker_error_fields(exc: KabuApiError) -> tuple[Any, Any]:
    if isinstance(exc.body, dict):
        return exc.body.get("Code"), exc.body.get("Message")
    return None, None


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


__all__ = [
    "BatchStats",
    "CloseoutStats",
    "PubSubError",
    "StreamRunner",
]
