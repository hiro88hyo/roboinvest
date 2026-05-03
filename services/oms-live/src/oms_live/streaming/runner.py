"""Streaming loop for OMS Live.

``live-orders`` を pull し、kabu API へ実発注 → 約定確定までポーリング → Supabase
に書き込み → ack するループ。

at-least-once 規約 (oms-paper と同じ思想):

* 実発注 + Supabase 書込が完了した時点で ack。
* Supabase 書込失敗は **ack 列に積まず** Pub/Sub の再配信に委ねる (fail-closed)。
* 冪等性: ``OrderRequest.order_id`` を ``trades_live.order_id`` (partial unique
  index) に carry。Runner は sendorder の前に ``live_trade_exists_for_order_id``
  で重複検知し、True なら sendorder せず ack して "skipped_duplicate" を計上する。
  これで「前回 sendorder + Supabase 書込が完全に成功した後に redeliver」のケースは
  二重発注を完全に防ぐ。なお「sendorder 成功 + Supabase 書込失敗で再配信」では
  trades_live に行が無いため check は通り、sendorder が再走する (kabu 側で同一
  銘柄・同一秒の二重発注になる) — これは別途 Runner 側で fail-closed の見直しが
  必要だが、本フェーズでは扱わない。
* sendorder の Result != 0 (受付拒否) は業務上の no_fill として ack。
* 約定タイムアウト時は cancel_order を投げて ack (再配信は冪等性問題のため避ける)。
* スキーマ不正・JSON パース失敗の poison message は ack。

純関数 (build_sendorder_payload / parse_order_state / to_fill_result /
apply_fill / build_fill_record / build_closeout_orders) は Phase 1/2b のものを
そのまま呼び出す。
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
from trade_contracts.enums import TradingStyle
from trade_contracts.order import OrderRequest

from ..clients.pubsub import PubSubError, PubSubSubscriber, PulledMessage
from ..clients.supabase import SupabaseClient, SupabaseError
from ..closeout import build_closeout_orders
from ..config import OmsLiveSettings
from ..kabu_client import KabuApiError, KabuLiveClient
from ..models import FillResult, LivePosition, PositionUpdate
from ..order_builder import build_sendorder_payload
from ..order_parser import STATE_DONE, parse_order_state, to_fill_result
from ..position_updater import apply_fill, build_fill_record

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
    write_errors: int
    acked: int
    skipped_duplicate: int = 0


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
        """1 バッチ分の pull + 処理 + ack。"""
        order_msgs = await self.subscriber.pull(
            self.settings.pubsub_subscription_live_orders,
            max_messages=self.settings.pubsub_pull_max_messages,
            return_immediately=True,
        )
        parse_errors = 0
        filled = 0
        no_fills = 0
        write_errors = 0
        skipped_duplicate = 0
        acks: list[str] = []

        for msg in order_msgs:
            order = _parse_order(msg)
            if order is None:
                parse_errors += 1
                acks.append(msg.ack_id)
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
            acks.append(msg.ack_id)

        if acks:
            await self.subscriber.acknowledge(self.settings.pubsub_subscription_live_orders, acks)

        return BatchStats(
            orders_pulled=len(order_msgs),
            parse_errors=parse_errors,
            filled=filled,
            no_fills=no_fills,
            write_errors=write_errors,
            acked=len(acks),
            skipped_duplicate=skipped_duplicate,
        )

    async def _process_order(self, order: OrderRequest) -> str:
        """``"filled" | "no_fill" | "skipped_duplicate"``。

        Supabase 書込失敗時のみ ``SupabaseError`` を伝播する。
        重複検知時は sendorder せず即 ack する (idempotency hardening)。
        """

        # 0) idempotency check: redeliver された (前回完全成功済) 注文は弾く
        if await self.supabase.live_trade_exists_for_order_id(order.order_id):
            logger.info(
                "live order skipped (already filled): order_id=%s symbol=%s signal_id=%s",
                order.order_id,
                order.symbol,
                order.unified_signal_id,
            )
            return "skipped_duplicate"

        # 1) sendorder
        payload = build_sendorder_payload(
            order,
            password=self.settings.kabu_order_password,
            exchange=self.settings.kabu_default_exchange,
            account_type=self.settings.kabu_account_type,
        )
        try:
            send_resp = await self.kabu.send_order(payload)
        except KabuApiError:
            logger.exception(
                "kabu sendorder failed: symbol=%s signal_id=%s",
                order.symbol,
                order.unified_signal_id,
            )
            return "no_fill"
        if int(send_resp.get("Result", -1)) != 0:
            logger.warning(
                "kabu sendorder rejected: symbol=%s signal_id=%s body=%s",
                order.symbol,
                order.unified_signal_id,
                send_resp,
            )
            return "no_fill"
        kabu_order_id = str(send_resp.get("OrderId") or "")
        if not kabu_order_id:
            logger.warning(
                "kabu sendorder returned empty OrderId: symbol=%s body=%s",
                order.symbol,
                send_resp,
            )
            return "no_fill"

        # 2) poll until State==3 (done) or timeout
        fill = await self._poll_until_filled(kabu_order_id)
        if fill is None:
            # タイムアウト → cancel して no_fill 扱いで ack
            await self._best_effort_cancel(kabu_order_id)
            return "no_fill"
        if fill.filled_quantity == 0 or fill.fill_price is None:
            logger.info(
                "live order finished without fill: symbol=%s reason=%s signal_id=%s",
                order.symbol,
                fill.reason,
                order.unified_signal_id,
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
            executed_at=order.created_at,
        )
        if update.error is not None:
            logger.warning(
                "live apply_fill error: symbol=%s error=%s signal_id=%s",
                order.symbol,
                update.error,
                order.unified_signal_id,
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
        )
        return "filled"

    async def _poll_until_filled(self, kabu_order_id: str) -> FillResult | None:
        """``State==3`` か取消確定までポーリング。タイムアウトで ``None`` を返す。"""
        deadline = self.monotonic() + self.settings.order_fill_timeout_seconds
        interval = max(self.settings.order_fill_poll_interval_seconds, 0.0)
        while True:
            try:
                payload = await self.kabu.get_order(kabu_order_id)
            except KabuApiError:
                logger.exception("kabu get_order failed: order_id=%s", kabu_order_id)
                return None
            try:
                state = parse_order_state(payload)
            except (ValueError, KeyError):
                logger.exception("kabu order parse failed: order_id=%s", kabu_order_id)
                return None
            fill = to_fill_result(state)
            if fill.reason in {"filled", "partial", "cancelled"} or state.state == STATE_DONE:
                return fill
            if self.monotonic() >= deadline:
                logger.warning(
                    "kabu order poll timeout: order_id=%s last_state=%d cum_qty=%d",
                    kabu_order_id,
                    state.state,
                    state.cum_qty,
                )
                return None
            if interval > 0:
                await self.sleep(interval)

    async def _best_effort_cancel(self, kabu_order_id: str) -> None:
        try:
            await self.kabu.cancel_order(
                order_id=kabu_order_id,
                password=self.settings.kabu_order_password,
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

        positions = await self.supabase.list_live_positions()
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
            existing = next((p for p in positions if p.symbol == order.symbol), None)
            try:
                outcome = await self._process_closeout_order(order=order, existing=existing)
            except SupabaseError:
                logger.exception("closeout: write failure symbol=%s", order.symbol)
                write_errors += 1
                continue
            if outcome == "filled":
                closed += 1
            else:
                no_fills += 1

        return CloseoutStats(
            triggered=True,
            skipped_reason=None,
            positions_seen=len(positions),
            closed=closed,
            no_fills=no_fills,
            write_errors=write_errors,
        )

    async def _process_closeout_order(
        self, *, order: OrderRequest, existing: LivePosition | None
    ) -> str:
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
            send_resp = await self.kabu.send_order(payload)
        except KabuApiError:
            logger.exception("closeout sendorder failed: symbol=%s", order.symbol)
            return "no_fill"
        if int(send_resp.get("Result", -1)) != 0:
            logger.warning(
                "closeout sendorder rejected: symbol=%s body=%s", order.symbol, send_resp
            )
            return "no_fill"
        kabu_order_id = str(send_resp.get("OrderId") or "")
        if not kabu_order_id:
            return "no_fill"

        fill = await self._poll_until_filled(kabu_order_id)
        if fill is None:
            await self._best_effort_cancel(kabu_order_id)
            return "no_fill"
        if fill.filled_quantity == 0 or fill.fill_price is None:
            return "no_fill"

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
                "closeout apply_fill error: symbol=%s error=%s", order.symbol, update.error
            )
            return "no_fill"
        record = build_fill_record(order=order, fill=fill, executed_at=order.created_at)
        if record is None:
            return "no_fill"

        await self.supabase.insert_trade_live(record)
        await self._write_position_change(existing=existing, update=update, symbol=order.symbol)
        if update.realized_pnl is not None:
            await self.supabase.add_realized_pnl(update.realized_pnl)
        return "filled"


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
