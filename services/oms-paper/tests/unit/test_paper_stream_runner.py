from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from oms_paper._testing import (
    DEFAULT_TS,
    make_order_book,
    make_order_request,
    make_paper_position,
)
from oms_paper.clients.pubsub import PubSubSubscriber
from oms_paper.clients.supabase import SupabaseClient
from oms_paper.config import OmsPaperSettings
from oms_paper.streaming.runner import StreamRunner
from trade_contracts.enums import RoutingIntent, Side, TradeMode, TradingStyle

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


PAPER_ORDERS_SUB = "oms-paper-paper-orders"
RAW_SUB = "oms-paper-raw-market-data"


def _settings(**overrides: Any) -> OmsPaperSettings:
    base: dict[str, Any] = dict(
        supabase_url="https://example.supabase.co",
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_paper_orders=PAPER_ORDERS_SUB,
        pubsub_subscription_raw_market_data=RAW_SUB,
        pubsub_pull_max_messages=10,
    )
    base.update(overrides)
    return OmsPaperSettings(**base)


def _pull_response(payloads: list[tuple[str, bytes]]) -> dict[str, Any]:
    return {
        "receivedMessages": [
            {
                "ackId": ack_id,
                "message": {
                    "messageId": f"m-{ack_id}",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
            for ack_id, data in payloads
        ]
    }


class _PubSubRouter:
    """Routes pull / ack per subscription."""

    def __init__(
        self,
        *,
        order_batches: list[dict[str, Any]] | None = None,
        book_batches: list[dict[str, Any]] | None = None,
    ) -> None:
        self.order_batches = list(order_batches or [])
        self.book_batches = list(book_batches or [])
        self.acked: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":pull"):
            if PAPER_ORDERS_SUB in path:
                body = self.order_batches.pop(0) if self.order_batches else {}
                return httpx.Response(200, json=body)
            if RAW_SUB in path:
                body = self.book_batches.pop(0) if self.book_batches else {}
                return httpx.Response(200, json=body)
            return httpx.Response(404, text=f"unknown sub: {path}")
        if path.endswith(":acknowledge"):
            self.acked.append(request)
            return httpx.Response(200, json={})
        return httpx.Response(404, text=f"unmocked: {path}")


@dataclass
class _SupabaseRouter:
    paper_position_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    list_position_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    system_status_rows: list[dict[str, Any]] = field(default_factory=list)
    # True → atomic RPC が duplicate を返す。
    rpc_duplicate_responses: list[bool] = field(default_factory=list)
    rpc_status: int = 200
    trail_update_status: int = 200
    replace_position_before_fill: dict[str, Any] | None = None
    replace_position_before_stop_update: dict[str, Any] | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    position_state: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    committed_orders: dict[str, str] = field(default_factory=dict, init=False)
    committed_signals: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Seed the RPC's authoritative state from the first configured read."""

        if self.list_position_rows:
            for row in self.list_position_rows[0]:
                self.position_state[str(row["symbol"])] = dict(row)
        if self.paper_position_rows:
            for row in self.paper_position_rows[0]:
                self.position_state[str(row["symbol"])] = dict(row)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method
        if method == "GET" and path == "/rest/v1/system_status":
            row = self.system_status_rows.pop(0) if self.system_status_rows else None
            return httpx.Response(200, json=[row] if row is not None else [])
        if method == "GET" and path == "/rest/v1/positions":
            symbol = request.url.params.get("symbol")
            if symbol is None:  # list_paper_positions (no symbol filter)
                if self.list_position_rows:
                    rows = self.list_position_rows.pop(0)
                    self.position_state = {str(row["symbol"]): dict(row) for row in rows}
                else:
                    rows = [dict(row) for row in self.position_state.values()]
                return httpx.Response(200, json=rows)
            symbol_value = symbol.removeprefix("eq.")
            if self.paper_position_rows:
                rows = self.paper_position_rows.pop(0)
                if rows:
                    self.position_state[symbol_value] = dict(rows[0])
                else:
                    self.position_state.pop(symbol_value, None)
            else:
                current = self.position_state.get(symbol_value)
                rows = [dict(current)] if current is not None else []
            return httpx.Response(200, json=rows)
        if method == "POST" and path == "/rest/v1/rpc/oms_paper_update_stop_loss":
            if self.trail_update_status >= 300:
                return httpx.Response(self.trail_update_status, text="injected trail failure")
            if self.replace_position_before_stop_update is not None:
                replacement = dict(self.replace_position_before_stop_update)
                self.position_state[str(replacement["symbol"])] = replacement
                self.replace_position_before_stop_update = None
            return httpx.Response(200, json=[self._update_stop_loss_rpc(request)])
        if method == "POST" and path == "/rest/v1/rpc/oms_paper_apply_fill":
            if self.rpc_status >= 300:
                return httpx.Response(self.rpc_status, text="injected RPC failure")
            if self.replace_position_before_fill is not None:
                replacement = dict(self.replace_position_before_fill)
                self.position_state[str(replacement["symbol"])] = replacement
                self.replace_position_before_fill = None
            return httpx.Response(200, json=[self._apply_fill_rpc(request)])
        return httpx.Response(404, text=f"unmocked: {method} {path}")

    def _apply_fill_rpc(self, request: httpx.Request) -> dict[str, Any]:
        """Small stateful model of ``oms_paper_apply_fill`` for runner tests."""

        body = json.loads(request.content.decode())
        order_id = str(body["p_order_id"])
        trade_id = str(body["p_trade_id"])
        signal_id = body["p_unified_signal_id"]
        symbol = str(body["p_symbol"])
        current = self.position_state.get(symbol)

        committed_trade_id = self.committed_orders.get(order_id)
        if committed_trade_id is not None:
            return self._rpc_row(
                outcome="duplicate",
                reason="order_id",
                committed_trade_id=committed_trade_id,
                position_action="unchanged",
                resulting_position=current,
            )
        if signal_id is not None and str(signal_id) in self.committed_signals:
            return self._rpc_row(
                outcome="duplicate",
                reason="unified_signal_id",
                committed_trade_id=self.committed_signals[str(signal_id)],
                position_action="unchanged",
                resulting_position=current,
            )

        injected_duplicate = (
            self.rpc_duplicate_responses.pop(0) if self.rpc_duplicate_responses else False
        )
        if injected_duplicate:
            return self._rpc_row(
                outcome="duplicate",
                reason="unified_signal_id" if signal_id is not None else "order_id",
                committed_trade_id=trade_id,
                position_action="unchanged",
                resulting_position=current,
            )

        quantity = int(body["p_filled_quantity"])
        fill_price = Decimal(str(body["p_fill_price"]))
        side = str(body["p_side"])
        if side == "BUY":
            if current is None:
                resulting_position = _position_row(
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=str(fill_price),
                    holding_type=body["p_new_holding_type"],
                    target_price=body["p_new_target_price"],
                    stop_loss_price=body["p_new_stop_loss_price"],
                    max_hold_days=body["p_new_max_hold_days"],
                    scheduled_exit_date=body["p_new_scheduled_exit_date"],
                    trailing_stop_pct=body["p_new_trailing_stop_pct"],
                    opened_at=body["p_executed_at"],
                )
                action = "inserted"
            else:
                old_quantity = int(current["quantity"])
                next_quantity = old_quantity + quantity
                next_entry = (
                    (Decimal(str(current["entry_price"])) * old_quantity) + (fill_price * quantity)
                ) / next_quantity
                resulting_position = dict(current)
                resulting_position["quantity"] = next_quantity
                resulting_position["entry_price"] = str(
                    next_entry.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                )
                action = "updated"
            self.position_state[symbol] = resulting_position
        elif current is None:
            return self._rpc_row(
                outcome="rejected",
                reason="no_position_for_sell",
                committed_trade_id=None,
                position_action="unchanged",
                resulting_position=None,
            )
        elif body["p_expected_position_opened_at"] is not None and datetime.fromisoformat(
            str(current["opened_at"])
        ) != datetime.fromisoformat(str(body["p_expected_position_opened_at"])):
            return self._rpc_row(
                outcome="rejected",
                reason="position_generation_mismatch",
                committed_trade_id=None,
                position_action="unchanged",
                resulting_position=current,
            )
        elif quantity > int(current["quantity"]):
            return self._rpc_row(
                outcome="rejected",
                reason="oversell",
                committed_trade_id=None,
                position_action="unchanged",
                resulting_position=current,
            )
        elif quantity == int(current["quantity"]):
            self.position_state.pop(symbol)
            resulting_position = None
            action = "deleted"
        else:
            resulting_position = dict(current)
            resulting_position["quantity"] = int(current["quantity"]) - quantity
            self.position_state[symbol] = resulting_position
            action = "updated"

        self.committed_orders[order_id] = trade_id
        if signal_id is not None:
            self.committed_signals[str(signal_id)] = trade_id
        return self._rpc_row(
            outcome="applied",
            reason=None,
            committed_trade_id=trade_id,
            position_action=action,
            resulting_position=resulting_position,
        )

    def _update_stop_loss_rpc(self, request: httpx.Request) -> dict[str, Any]:
        body = json.loads(request.content.decode())
        symbol = str(body["p_symbol"])
        current = self.position_state.get(symbol)
        if current is None:
            return {
                "outcome": "rejected",
                "reason": "no_position_for_update",
                "resulting_position": None,
            }
        expected_opened_at = datetime.fromisoformat(body["p_expected_position_opened_at"])
        if datetime.fromisoformat(str(current["opened_at"])) != expected_opened_at:
            return {
                "outcome": "rejected",
                "reason": "position_generation_mismatch",
                "resulting_position": current,
            }
        requested_stop = Decimal(str(body["p_stop_loss_price"]))
        current_stop = current.get("stop_loss_price")
        if current_stop is not None and requested_stop <= Decimal(str(current_stop)):
            return {
                "outcome": "rejected",
                "reason": "stop_not_raised",
                "resulting_position": current,
            }
        resulting_position = dict(current)
        resulting_position["stop_loss_price"] = body["p_stop_loss_price"]
        self.position_state[symbol] = resulting_position
        return {
            "outcome": "applied",
            "reason": None,
            "resulting_position": resulting_position,
        }

    @staticmethod
    def _rpc_row(
        *,
        outcome: str,
        reason: str | None,
        committed_trade_id: str | None,
        position_action: str,
        resulting_position: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "reason": reason,
            "committed_trade_id": committed_trade_id,
            "position_action": position_action,
            "resulting_position": resulting_position,
        }


def _system_status_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "is_trading_allowed": True,
        "trade_mode": "paper",
        "trading_style": "day",
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
        "daily_loss_limit": "10000",
        "weekly_loss_limit": "30000",
        "monthly_loss_limit": "100000",
        "updated_at": "2026-04-25T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _position_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": "7203",
        "side": "LONG",
        "quantity": 100,
        "entry_price": "1000",
        "holding_type": "day",
        "target_price": None,
        "stop_loss_price": None,
        "max_hold_days": None,
        "scheduled_exit_date": None,
        "trailing_stop_pct": None,
        "opened_at": "2026-04-25T09:00:00+00:00",
    }
    row.update(overrides)
    return row


APPLY_FILL_RPC_PATH = "/rest/v1/rpc/oms_paper_apply_fill"
STOP_UPDATE_RPC_PATH = "/rest/v1/rpc/oms_paper_update_stop_loss"


def _apply_fill_requests(supabase: _SupabaseRouter) -> list[httpx.Request]:
    return [
        request
        for request in supabase.requests
        if request.method == "POST" and request.url.path == APPLY_FILL_RPC_PATH
    ]


def _apply_fill_body(request: httpx.Request) -> dict[str, Any]:
    body = json.loads(request.content.decode())
    assert isinstance(body, dict)
    return body


def _stop_update_requests(supabase: _SupabaseRouter) -> list[httpx.Request]:
    return [
        request
        for request in supabase.requests
        if request.method == "POST" and request.url.path == STOP_UPDATE_RPC_PATH
    ]


def _direct_fill_writes(supabase: _SupabaseRouter) -> list[httpx.Request]:
    return [
        request
        for request in supabase.requests
        if request.method in {"POST", "PATCH", "DELETE"}
        and request.url.path in {"/rest/v1/trades_paper", "/rest/v1/positions"}
    ]


async def _with_runner(
    *,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    settings: OmsPaperSettings | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
    sleep: Callable[[float], Awaitable[None]] | None = None,
    book_cache: dict[str, Any] | None = None,
) -> Any:
    s = settings or _settings()

    async def _noop_sleep(_: float) -> None:
        return None

    def _wall_clock() -> datetime:
        return DEFAULT_TS

    async with (
        PubSubSubscriber(
            project_id=s.pubsub_project_id,
            emulator_host=s.pubsub_emulator_host,
            transport=httpx.MockTransport(pubsub),
        ) as subscriber,
        SupabaseClient(
            url=s.supabase_url,
            secret_key=s.supabase_secret_key,
            transport=httpx.MockTransport(supabase),
        ) as supa,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supa,
            settings=s,
            book_cache=book_cache or {},
            idle_backoff_seconds=1.0,
            sleep=sleep or _noop_sleep,
            wall_clock=_wall_clock,
        )
        return await run_body(runner)


# --- run_once: orders ----------------------------------------------------


async def test_book_pulled_first_then_order_fills() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
        trailing_stop_pct=Decimal("0.02"),
        max_hold_days=5,
        created_at=DEFAULT_TS,
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[]],  # 既存無し
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_pulled == 1
    assert stats.books_applied == 1
    assert stats.orders_pulled == 1
    assert stats.filled == 1
    assert stats.no_fills == 0
    assert stats.write_errors == 0

    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_symbol"] == "7203"
    assert body["p_fill_price"] == "1000"
    assert body["p_filled_quantity"] == 100
    assert body["p_new_stop_loss_price"] == "950"
    assert body["p_new_target_price"] == "1100"
    assert body["p_new_trailing_stop_pct"] == "0.02"
    assert body["p_new_max_hold_days"] == 5
    assert body["p_new_scheduled_exit_date"] == "2026-05-07"
    assert _direct_fill_writes(supabase) == []


async def test_swing_buy_persists_fill_anchored_stop_and_order_metadata() -> None:
    book = make_order_book(
        symbol="7203",
        asks=(("1000", 50), ("1010", 100)),
    )
    scheduled_exit = date(2026, 5, 15)
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        holding_type=TradingStyle.SWING,
        stop_loss_pct=Decimal("0.10"),
        max_hold_days=10,
        scheduled_exit_date=scheduled_exit,
        created_at=DEFAULT_TS,
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(paper_position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(default_holding_type=TradingStyle.DAY),
        run_body=_body,
    )

    assert stats.filled == 1
    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_fill_price"] == "1005"
    assert body["p_new_holding_type"] == "swing"
    assert body["p_new_stop_loss_price"] == "904.50"
    assert body["p_new_max_hold_days"] == 10
    assert body["p_new_scheduled_exit_date"] == scheduled_exit.isoformat()
    assert _direct_fill_writes(supabase) == []


async def test_day_stop_loss_breach_triggers_exit() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
        paper_position_rows=[
            [
                _position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_exits == 1
    assert stats.day_stop_trails == 0
    assert stats.day_stop_no_fills == 0
    assert stats.day_stop_write_errors == 0
    assert stats.swing_exits == 0

    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_symbol"] == "7203"
    assert body["p_side"] == "SELL"
    assert body["p_filled_quantity"] == 100
    assert body["p_fill_price"] == "950"
    assert body["p_unified_signal_id"] is None
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []


async def test_day_stop_partial_exit_keeps_authoritative_remaining_position() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 40),))
    position = _position_row(quantity=100, stop_loss_price="950", target_price="1100")
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[position]],
        paper_position_rows=[[position]],
    )

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner.day_position_cache["7203"]

    stats, cached = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_exits == 0
    assert stats.day_stop_partial_exits == 1
    assert stats.day_stop_no_fills == 0
    assert cached.quantity == 60
    assert supabase.position_state["7203"]["quantity"] == 60
    assert _apply_fill_body(_apply_fill_requests(supabase)[0])["p_filled_quantity"] == 40


async def test_day_stop_monitor_rejects_stale_received_book() -> None:
    book = make_order_book(
        symbol="7203",
        bids=(("950", 500),),
        timestamp=DEFAULT_TS,
        received_at=DEFAULT_TS - timedelta(seconds=11),
    )
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_exits == 0
    assert stats.day_stop_no_fills == 1
    writes = [
        request for request in supabase.requests if request.method in {"POST", "PATCH", "DELETE"}
    ]
    assert writes == []


async def test_day_stop_stale_cached_position_does_not_emit_phantom_sell() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        runner.day_position_cache["7203"] = make_paper_position(
            symbol="7203",
            quantity=100,
            entry_price=Decimal("1000"),
            stop_loss_price=Decimal("950"),
            target_price=Decimal("1100"),
        )
        runner.swing_cache_loaded_at = runner.monotonic()
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_exits == 0
    assert stats.day_stop_no_fills == 1
    fresh_position_reads = [
        r
        for r in supabase.requests
        if r.method == "GET"
        and r.url.path == "/rest/v1/positions"
        and r.url.params.get("symbol") == "eq.7203"
    ]
    assert len(fresh_position_reads) == 1
    write_reqs = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert write_reqs == []


async def test_day_stop_does_not_sell_replacement_position_generation() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    original = _position_row(
        stop_loss_price="950",
        target_price="1100",
        opened_at="2026-04-25T09:00:00+00:00",
    )
    replacement = _position_row(
        stop_loss_price="500",
        target_price=None,
        opened_at="2026-04-25T10:00:00+00:00",
    )
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[original]],
        paper_position_rows=[[replacement]],
    )

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner.day_position_cache["7203"]

    stats, cached = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_exits == 0
    assert stats.day_stop_no_fills == 1
    assert cached.opened_at == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    assert cached.stop_loss_price == Decimal("500")
    assert _apply_fill_requests(supabase) == []
    assert supabase.position_state["7203"]["opened_at"] == replacement["opened_at"]


async def test_day_trailing_stop_patches_stop_loss_only() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [_position_row(quantity=100, stop_loss_price="980", trailing_stop_pct="0.02")]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_trails == 1
    assert stats.day_stop_exits == 0
    [rpc_request] = _stop_update_requests(supabase)
    body = json.loads(rpc_request.content.decode())
    assert body["p_symbol"] == "7203"
    assert body["p_stop_loss_price"] == "1078"
    assert body["p_expected_position_opened_at"] == "2026-04-25T09:00:00+00:00"
    assert _direct_fill_writes(supabase) == []


async def test_day_trail_rejects_replacement_position_generation() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    original = _position_row(
        stop_loss_price="980",
        trailing_stop_pct="0.02",
        opened_at="2026-04-25T09:00:00+00:00",
    )
    replacement = _position_row(
        stop_loss_price="500",
        trailing_stop_pct=None,
        opened_at="2026-04-25T10:00:00+00:00",
    )
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[original]],
        replace_position_before_stop_update=replacement,
    )

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner.day_position_cache["7203"]

    stats, cached = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.day_stop_trails == 0
    assert stats.day_stop_no_fills == 1
    assert cached.opened_at == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    assert cached.stop_loss_price == Decimal("500")
    assert supabase.position_state["7203"]["stop_loss_price"] == "500"


async def test_order_with_no_book_in_cache_is_no_fill_and_acked() -> None:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.no_fills == 1
    assert stats.filled == 0
    # 冪等性チェック (GET trades_paper) は走るが、書き込みは発生しない
    write_reqs = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert write_reqs == []
    ack_paths = [r.url.path for r in pubsub.acked]
    assert any(PAPER_ORDERS_SUB in p for p in ack_paths)


async def test_order_no_fill_log_has_structured_reason(caplog: Any) -> None:
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="oms_paper.streaming.runner")

    await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    ]
    assert len(records) == 1
    assert records[0].reason == "no_book"
    assert records[0].symbol == "7203"
    assert records[0].side == "BUY"


async def test_order_with_stale_book_is_no_fill_and_acked() -> None:
    old_book = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS - timedelta(seconds=11),
    )
    # An order timestamp copied from the stale book must not make the book look fresh.
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        created_at=old_book.timestamp,
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", old_book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_applied == 1
    assert stats.no_fills == 1
    assert stats.filled == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []
    assert any(PAPER_ORDERS_SUB in r.url.path for r in pubsub.acked)


async def test_order_with_future_skewed_book_is_no_fill_and_acked(caplog: Any) -> None:
    future_book = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS - timedelta(hours=1),
        received_at=DEFAULT_TS + timedelta(seconds=6),
    )
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", future_book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.WARNING, logger="oms_paper.streaming.runner")
    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(order_book_max_future_skew_seconds=5),
        run_body=_body,
    )

    assert stats.no_fills == 1
    assert stats.filled == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []
    assert any(PAPER_ORDERS_SUB in r.url.path for r in pubsub.acked)
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    )
    assert record.reason == "future_book"
    assert record.book_age_seconds == -6.0
    assert record.freshness_timestamp_source == "received_at"


async def test_fresh_book_uses_wall_clock_instead_of_order_timestamp() -> None:
    fresh_book = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        # CurrentPriceTime may be old even when this exact board payload was just
        # received. Freshness therefore prefers the separate receipt timestamp.
        timestamp=DEFAULT_TS - timedelta(hours=2),
        received_at=DEFAULT_TS - timedelta(seconds=5),
    )
    # A delayed order timestamp is not the freshness clock; the currently cached
    # book is only five seconds old at processing time and remains eligible.
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        created_at=DEFAULT_TS - timedelta(hours=1),
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", fresh_book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(paper_position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.filled == 1
    assert stats.no_fills == 0


async def test_strict_received_at_mode_rejects_legacy_book_and_acks(caplog: Any) -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.WARNING, logger="oms_paper.streaming.runner")
    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(order_book_require_received_at=True),
        run_body=_body,
    )

    assert stats.no_fills == 1
    assert stats.filled == 0
    assert any(PAPER_ORDERS_SUB in r.url.path for r in pubsub.acked)
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    )
    assert record.reason == "missing_book_received_at"
    assert record.freshness_timestamp_source == "received_at"


async def test_paper_only_order_requires_received_at_even_when_global_strict_is_off(
    caplog: Any,
) -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        routing_intent=RoutingIntent.PAPER_ONLY,
        strategy_key="event-cluster-v1",
        candidate_id="cluster-1",
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.WARNING, logger="oms_paper.streaming.runner")
    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(order_book_require_received_at=False),
        run_body=_body,
    )

    assert stats.no_fills == 1
    assert stats.filled == 0
    assert any(PAPER_ORDERS_SUB in request.url.path for request in pubsub.acked)
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    )
    assert record.reason == "missing_book_received_at"


async def test_paper_only_order_rejects_disabled_freshness_thresholds(caplog: Any) -> None:
    book = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        received_at=DEFAULT_TS,
    )
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        routing_intent=RoutingIntent.PAPER_ONLY,
        strategy_key="event-cluster-v1",
        candidate_id="cluster-1",
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.WARNING, logger="oms_paper.streaming.runner")
    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(order_book_max_age_seconds=None),
        run_body=_body,
    )

    assert stats.no_fills == 1
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    )
    assert record.reason == "invalid_book_freshness_config"


async def test_live_order_on_paper_subscription_is_rejected_and_safe_acked(
    caplog: Any,
) -> None:
    order = make_order_request(
        symbol="7203",
        side=Side.BUY,
        quantity=100,
        trade_mode=TradeMode.LIVE,
    )
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.ERROR, logger="oms_paper.streaming.runner")
    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert stats.no_fills == 0
    assert stats.filled == 0
    # Mode rejection happens before idempotency and position reads.
    assert supabase.requests == []
    assert any(PAPER_ORDERS_SUB in r.url.path for r in pubsub.acked)
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_rejected"
    )
    assert record.reason == "wrong_trade_mode"
    assert record.trade_mode == "live"


async def test_raw_books_are_drained_before_order_processing() -> None:
    stale_book = make_order_book(
        symbol="7203",
        asks=(("900", 200),),
        timestamp=DEFAULT_TS - timedelta(seconds=60),
    )
    fresh_book = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS,
    )
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=DEFAULT_TS)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[
            _pull_response([("bk-stale", stale_book.model_dump_json().encode("utf-8"))]),
            _pull_response([("bk-fresh", fresh_book.model_dump_json().encode("utf-8"))]),
        ],
    )
    supabase = _SupabaseRouter(paper_position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(raw_book_drain_max_batches=2),
        run_body=_body,
    )

    assert stats.books_pulled == 2
    assert stats.books_applied == 2
    assert stats.filled == 1
    assert stats.no_fills == 0
    [rpc_request] = _apply_fill_requests(supabase)
    assert _apply_fill_body(rpc_request)["p_fill_price"] == "1000"


async def test_older_book_does_not_overwrite_newer_cache() -> None:
    newer = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS,
    )
    older = make_order_book(
        symbol="7203",
        asks=(("900", 200),),
        timestamp=DEFAULT_TS - timedelta(seconds=60),
    )
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=DEFAULT_TS)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[
            _pull_response(
                [
                    ("bk-new", newer.model_dump_json().encode("utf-8")),
                    ("bk-old", older.model_dump_json().encode("utf-8")),
                ]
            )
        ],
    )
    supabase = _SupabaseRouter(paper_position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_applied == 1
    assert stats.filled == 1
    [rpc_request] = _apply_fill_requests(supabase)
    assert _apply_fill_body(rpc_request)["p_fill_price"] == "1000"


async def test_older_receive_does_not_overwrite_newer_when_market_times_disagree() -> None:
    newer_receive = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS - timedelta(hours=1),
        received_at=DEFAULT_TS,
    )
    older_receive = make_order_book(
        symbol="7203",
        asks=(("900", 200),),
        timestamp=DEFAULT_TS,
        received_at=DEFAULT_TS - timedelta(seconds=1),
    )
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[
            _pull_response(
                [
                    ("bk-new-receive", newer_receive.model_dump_json().encode("utf-8")),
                    ("bk-old-receive", older_receive.model_dump_json().encode("utf-8")),
                ]
            )
        ],
    )
    supabase = _SupabaseRouter(paper_position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_applied == 1
    assert stats.filled == 1
    [rpc_request] = _apply_fill_requests(supabase)
    assert _apply_fill_body(rpc_request)["p_fill_price"] == "1000"


async def test_latest_book_timestamp_keeps_global_max_across_symbols() -> None:
    fresh = make_order_book(
        symbol="7203",
        asks=(("1000", 200),),
        timestamp=DEFAULT_TS,
    )
    old_other_symbol = make_order_book(
        symbol="9984",
        asks=(("800", 200),),
        timestamp=DEFAULT_TS - timedelta(seconds=600),
    )
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response(
                [
                    ("bk-fresh", fresh.model_dump_json().encode("utf-8")),
                    ("bk-old-other", old_other_symbol.model_dump_json().encode("utf-8")),
                ]
            )
        ],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner._latest_book_timestamp

    stats, latest = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_applied == 2
    assert latest == DEFAULT_TS


async def test_buy_into_existing_position_patches_quantity_and_entry() -> None:
    book = make_order_book(symbol="7203", asks=(("1100", 200),))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[_position_row(quantity=100, entry_price="1000")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.filled == 1
    [rpc_request] = _apply_fill_requests(supabase)
    assert _apply_fill_body(rpc_request)["p_fill_price"] == "1100"
    position = supabase.position_state["7203"]
    assert position["quantity"] == 200
    assert Decimal(position["entry_price"]) == Decimal("1050")
    assert _direct_fill_writes(supabase) == []


async def test_full_sell_deletes_position() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    order = make_order_request(symbol="7203", side=Side.SELL, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[_position_row(quantity=100, entry_price="900")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.filled == 1
    assert len(_apply_fill_requests(supabase)) == 1
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []


async def test_partial_sell_patches_remaining_quantity() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    order = make_order_request(symbol="7203", side=Side.SELL, quantity=40)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[_position_row(quantity=100, entry_price="900")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.filled == 1
    assert len(_apply_fill_requests(supabase)) == 1
    position = supabase.position_state["7203"]
    assert position["quantity"] == 60
    assert Decimal(position["entry_price"]) == Decimal("900")
    assert _direct_fill_writes(supabase) == []


async def test_oversell_is_no_fill_and_acked_without_writes() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    order = make_order_request(symbol="7203", side=Side.SELL, quantity=200)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[_position_row(quantity=100, entry_price="900")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.no_fills == 1
    assert len(_apply_fill_requests(supabase)) == 1
    assert supabase.position_state["7203"]["quantity"] == 100
    assert _direct_fill_writes(supabase) == []


async def test_sell_without_position_logs_specific_no_fill_reason(caplog: Any) -> None:
    caplog.set_level(logging.WARNING)
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    order = make_order_request(symbol="7203", side=Side.SELL, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.no_fills == 1
    assert len(_apply_fill_requests(supabase)) == 1
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []
    no_fill_reasons = [
        getattr(record, "reason", None)
        for record in caplog.records
        if getattr(record, "event", None) == "paper_order_no_fill"
    ]
    assert no_fill_reasons == ["no_position_for_sell"]


async def test_book_with_no_liquidity_is_no_fill() -> None:
    # asks empty
    book = make_order_book(symbol="7203", asks=())
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.no_fills == 1
    # 注文経路は早期 return で Supabase 書込ゼロ。
    # swing cache の list_paper_positions は走るが、本テストの趣旨と独立。
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []


async def test_tick_data_on_raw_market_data_is_ignored_but_acked() -> None:
    tick = {
        "symbol": "7203",
        "timestamp": DEFAULT_TS.isoformat(),
        "price": "1000",
        "volume": 100,
    }
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", json.dumps(tick).encode("utf-8"))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.books_pulled == 1
    assert stats.books_applied == 0
    raw_acks = [r for r in pubsub.acked if RAW_SUB in r.url.path]
    assert len(raw_acks) == 1


async def test_malformed_order_is_acked_without_writes() -> None:
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", b"not-json")])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.parse_errors == 1
    assert stats.filled == 0
    assert stats.no_fills == 0
    assert supabase.requests == []
    assert any(PAPER_ORDERS_SUB in r.url.path for r in pubsub.acked)


async def test_supabase_write_error_skips_ack() -> None:
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        paper_position_rows=[[]],
        rpc_status=500,  # transient error → SupabaseError
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.write_errors == 1
    assert stats.filled == 0
    # raw-market-data only acked
    order_acks = [r for r in pubsub.acked if PAPER_ORDERS_SUB in r.url.path]
    assert order_acks == []  # 注文側はリトライ用に未 ack
    raw_acks = [r for r in pubsub.acked if RAW_SUB in r.url.path]
    assert len(raw_acks) == 1


async def test_idle_pulls_trigger_backoff_sleep() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=1)

    await _with_runner(pubsub=pubsub, supabase=supabase, sleep=_sleep, run_body=_body)
    assert sleeps == [1.0]


# --- run_closeout --------------------------------------------------------


async def test_closeout_skipped_when_trading_style_swing() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="swing")],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.triggered is False
    assert stats.skipped_reason == "trading_style_swing"


async def test_closeout_with_no_positions_is_no_op_but_triggered() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.triggered is True
    assert stats.positions_seen == 0


async def test_closeout_fills_each_position_with_book_in_cache() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[_position_row(symbol="7203", quantity=100, entry_price="900")]],
    )
    book = make_order_book(symbol="7203", bids=(("1100", 500),))

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
        book_cache={"7203": book},
    )
    assert stats.triggered is True
    assert stats.closed == 1
    assert stats.no_fills == 0
    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_symbol"] == "7203"
    assert body["p_side"] == "SELL"
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []


async def test_closeout_partial_exit_then_newer_book_closes_remaining_position() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(trading_style="day"),
            _system_status_row(trading_style="day"),
        ],
        list_position_rows=[[_position_row(symbol="7203", quantity=100)]],
    )
    first_book = make_order_book(symbol="7203", bids=(("1100", 40),))
    second_book = make_order_book(
        symbol="7203",
        bids=(("1099", 60),),
        timestamp=DEFAULT_TS + timedelta(seconds=1),
        received_at=DEFAULT_TS + timedelta(seconds=1),
    )

    async def _body(runner: StreamRunner) -> Any:
        first = await runner.run_closeout()
        runner.book_cache["7203"] = second_book
        second = await runner.run_closeout()
        return first, second

    first, second = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
        book_cache={"7203": first_book},
    )

    assert first.closed == 0
    assert first.partial_exits == 1
    assert first.no_fills == 0
    assert second.closed == 1
    assert second.partial_exits == 0
    assert second.no_fills == 0
    assert "7203" not in supabase.position_state
    assert len(_apply_fill_requests(supabase)) == 2


async def test_closeout_does_not_sell_replacement_position_generation() -> None:
    original = _position_row(opened_at="2026-04-25T09:00:00+00:00")
    replacement = _position_row(opened_at="2026-04-25T10:00:00+00:00")
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[original]],
        replace_position_before_fill=replacement,
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
        book_cache={"7203": make_order_book(symbol="7203", bids=(("1100", 500),))},
    )

    assert stats.closed == 0
    assert stats.partial_exits == 0
    assert stats.no_fills == 1
    assert supabase.position_state["7203"]["opened_at"] == replacement["opened_at"]
    assert supabase.committed_orders == {}


async def test_closeout_preserves_swing_position_in_mixed_portfolio() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[
            [
                _position_row(
                    symbol="DAY",
                    quantity=100,
                    entry_price="900",
                    holding_type="day",
                ),
                _position_row(
                    symbol="SWING",
                    quantity=200,
                    entry_price="1000",
                    holding_type="swing",
                ),
            ]
        ],
    )
    books = {
        "DAY": make_order_book(symbol="DAY", bids=(("1100", 500),)),
        "SWING": make_order_book(symbol="SWING", bids=(("1200", 500),)),
    }

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
        book_cache=books,
    )

    assert stats.triggered is True
    assert stats.positions_seen == 1
    assert stats.closed == 1
    assert stats.no_fills == 0
    [rpc_request] = _apply_fill_requests(supabase)
    assert _apply_fill_body(rpc_request)["p_symbol"] == "DAY"
    assert "DAY" not in supabase.position_state
    assert supabase.position_state["SWING"]["quantity"] == 200
    assert _direct_fill_writes(supabase) == []


async def test_closeout_no_book_is_no_fill_and_no_writes_for_that_symbol() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[_position_row(symbol="7203", quantity=100)]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.no_fills == 1
    assert stats.closed == 0
    posts = [r for r in supabase.requests if r.method == "POST"]
    assert posts == []


async def test_closeout_handles_supabase_read_failure() -> None:
    # system_status not seeded → SupabaseError raised → stats reflects skip
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.triggered is False
    assert stats.skipped_reason == "read_system_status_failed"


# --- swing auto-close (Phase 4) ------------------------------------------


def _swing_position_row(**overrides: Any) -> dict[str, Any]:
    """swing 用 default を _position_row に上書き。"""
    base = _position_row(holding_type="swing", **overrides)
    return base


async def test_swing_no_positions_in_cache_is_skip() -> None:
    """swing position が DB に無いと、板更新が来ても何も起きない。"""
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(list_position_rows=[[]])  # swing cache fetch → empty

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_exits == 0
    assert stats.swing_trails == 0
    assert stats.swing_no_fills == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []


async def test_swing_stop_loss_breach_triggers_exit() -> None:
    """bids[0] が stop_loss 以下 → atomic RPC で SELL 約定 + position delete。"""
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _swing_position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
        paper_position_rows=[
            [
                _swing_position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_exits == 1
    assert stats.swing_trails == 0
    assert stats.swing_no_fills == 0
    assert stats.swing_write_errors == 0

    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_symbol"] == "7203"
    assert body["p_side"] == "SELL"
    assert body["p_filled_quantity"] == 100
    assert body["p_fill_price"] == "950"
    assert body["p_unified_signal_id"] is None  # swing exit は aggregator_logs 行なし
    assert body["p_signal_source"] == "CONSENSUS"
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []


async def test_swing_partial_exit_remains_in_monitor_cache() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 40),))
    position = _swing_position_row(quantity=100, stop_loss_price="950", target_price="1100")
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[position]],
        paper_position_rows=[[position]],
    )

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner.swing_position_cache["7203"]

    stats, cached = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.swing_exits == 0
    assert stats.swing_partial_exits == 1
    assert stats.swing_no_fills == 0
    assert cached.quantity == 60
    assert supabase.position_state["7203"]["quantity"] == 60


async def test_swing_exit_does_not_sell_replacement_position_generation() -> None:
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    original = _swing_position_row(
        stop_loss_price="950",
        target_price="1100",
        opened_at="2026-04-25T09:00:00+00:00",
    )
    replacement = _swing_position_row(
        stop_loss_price="500",
        target_price=None,
        opened_at="2026-04-25T10:00:00+00:00",
    )
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[original]],
        paper_position_rows=[[replacement]],
    )

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner.swing_position_cache["7203"]

    stats, cached = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.swing_exits == 0
    assert stats.swing_no_fills == 1
    assert cached.opened_at == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    assert cached.stop_loss_price == Decimal("500")
    assert _apply_fill_requests(supabase) == []
    assert supabase.position_state["7203"]["opened_at"] == replacement["opened_at"]


async def test_swing_monitor_rejects_stale_received_book() -> None:
    book = make_order_book(
        symbol="7203",
        bids=(("950", 500),),
        timestamp=DEFAULT_TS,
        received_at=DEFAULT_TS - timedelta(seconds=11),
    )
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _swing_position_row(
                    quantity=100,
                    entry_price="1000",
                    stop_loss_price="950",
                    target_price="1100",
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.swing_exits == 0
    assert stats.swing_no_fills == 1
    writes = [
        request for request in supabase.requests if request.method in {"POST", "PATCH", "DELETE"}
    ]
    assert writes == []


async def test_swing_target_hit_triggers_exit() -> None:
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [_swing_position_row(quantity=100, target_price="1100", stop_loss_price="950")]
        ],
        paper_position_rows=[
            [_swing_position_row(quantity=100, target_price="1100", stop_loss_price="950")]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_exits == 1


async def test_swing_max_hold_exit_is_written_before_same_cycle_buy_entry() -> None:
    exit_book = make_order_book(symbol="7203", bids=(("1005", 500),))
    entry_book = make_order_book(symbol="6758", asks=(("2000", 500),))
    buy_order = make_order_request(
        symbol="6758",
        side=Side.BUY,
        quantity=100,
        stop_loss_price=Decimal("1900"),
        target_price=Decimal("2200"),
        max_hold_days=10,
        created_at=DEFAULT_TS,
    )
    stale_swing = _swing_position_row(
        symbol="7203",
        quantity=100,
        entry_price="1000",
        max_hold_days=10,
        scheduled_exit_date="2026-04-25",
        opened_at="2026-04-10T09:00:00+00:00",
    )
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response(
                [
                    ("bk-exit", exit_book.model_dump_json().encode("utf-8")),
                    ("bk-entry", entry_book.model_dump_json().encode("utf-8")),
                ]
            )
        ],
        order_batches=[_pull_response([("ord-buy", buy_order.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[stale_swing]],
        paper_position_rows=[
            [stale_swing],  # current row for swing exit
            [],  # no existing 6758 paper position for BUY
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.swing_exits == 1
    assert stats.filled == 1
    assert stats.no_fills == 0
    assert stats.write_errors == 0

    rpc_symbols = [
        _apply_fill_body(request)["p_symbol"] for request in _apply_fill_requests(supabase)
    ]
    assert rpc_symbols == ["7203", "6758"]
    assert _direct_fill_writes(supabase) == []


async def test_opening_swing_max_hold_exit_batch_closes_due_positions(caplog: Any) -> None:
    caplog.set_level(logging.INFO)
    due = _swing_position_row(
        symbol="7203",
        quantity=100,
        entry_price="1000",
        max_hold_days=10,
        scheduled_exit_date="2026-04-25",
        opened_at="2026-04-10T09:00:00+00:00",
    )
    not_due = _swing_position_row(
        symbol="6758",
        quantity=100,
        entry_price="2000",
        max_hold_days=20,
        scheduled_exit_date="2026-05-15",
        opened_at="2026-04-10T09:00:00+00:00",
    )
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        list_position_rows=[[not_due, due]],
        paper_position_rows=[[due]],
    )
    book_cache = {
        "7203": make_order_book(symbol="7203", bids=(("1005", 500),)),
        "6758": make_order_book(symbol="6758", bids=(("2005", 500),)),
    }

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_opening_swing_max_hold_exits()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        book_cache=book_cache,
        run_body=_body,
    )

    assert stats.positions_seen == 2
    assert stats.due_positions == 1
    assert stats.closed == 1
    assert stats.no_fills == 0
    assert stats.write_errors == 0

    [rpc_request] = _apply_fill_requests(supabase)
    body = _apply_fill_body(rpc_request)
    assert body["p_symbol"] == "7203"
    assert body["p_side"] == "SELL"
    assert body["p_fill_price"] == "1005"
    assert "7203" not in supabase.position_state
    assert _direct_fill_writes(supabase) == []
    sequence_stages = [
        getattr(record, "stage", None)
        for record in caplog.records
        if getattr(record, "event", None) == "opening_swing_exit_sequence"
    ]
    assert sequence_stages == ["sell_fill", "position_delete"]


async def test_opening_swing_exit_reports_partial_and_keeps_remaining_position() -> None:
    due = _swing_position_row(
        quantity=100,
        max_hold_days=10,
        scheduled_exit_date="2026-04-25",
        opened_at="2026-04-10T09:00:00+00:00",
    )
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        list_position_rows=[[due]],
        paper_position_rows=[[due]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_opening_swing_max_hold_exits()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        book_cache={"7203": make_order_book(symbol="7203", bids=(("1005", 40),))},
        run_body=_body,
    )

    assert stats.closed == 0
    assert stats.partial_exits == 1
    assert stats.no_fills == 0
    assert supabase.position_state["7203"]["quantity"] == 60


async def test_opening_swing_max_hold_exit_batch_no_fill_without_cached_bid() -> None:
    due = _swing_position_row(
        symbol="7203",
        quantity=100,
        entry_price="1000",
        max_hold_days=10,
        scheduled_exit_date="2026-04-25",
        opened_at="2026-04-10T09:00:00+00:00",
    )
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(list_position_rows=[[due]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_opening_swing_max_hold_exits()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.positions_seen == 1
    assert stats.due_positions == 1
    assert stats.closed == 0
    assert stats.no_fills == 1
    assert stats.write_errors == 0
    writes = [request for request in supabase.requests if request.method in {"POST", "DELETE"}]
    assert writes == []


async def test_swing_trail_only_patches_stop_loss() -> None:
    """stop も target も触れない / max_hold 未経過、trail 候補が既存 stop を更新。"""
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _swing_position_row(
                    quantity=100,
                    stop_loss_price="980",
                    trailing_stop_pct="0.02",
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_trails == 1
    assert stats.swing_exits == 0

    [rpc_request] = _stop_update_requests(supabase)
    body = json.loads(rpc_request.content.decode())
    # 1100 * 0.98 = 1078
    assert body["p_stop_loss_price"] == "1078"
    assert body["p_expected_position_opened_at"] == "2026-04-25T09:00:00+00:00"
    # trades_paper への書き込みはなし
    assert _apply_fill_requests(supabase) == []
    assert _direct_fill_writes(supabase) == []


async def test_swing_hold_no_writes() -> None:
    """価格が stop と target の間、trailing は stop を更新できない → hold。"""
    book = make_order_book(symbol="7203", bids=(("1090", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [
                _swing_position_row(
                    quantity=100,
                    stop_loss_price="1078",  # 既に切上げ済み
                    target_price="1200",
                    trailing_stop_pct="0.02",  # candidate=1068 < 1078
                )
            ]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_exits == 0
    assert stats.swing_trails == 0
    assert stats.swing_no_fills == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []


async def test_swing_skips_day_positions_in_cache() -> None:
    """holding_type=day は cache に乗らないので評価対象外。"""
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [_position_row(stop_loss_price="950", quantity=100)]  # holding_type=day
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(paper_day_stop_monitor_enabled=False),
        run_body=_body,
    )
    assert stats.swing_exits == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []


async def test_swing_consecutive_books_only_exit_once() -> None:
    """exit 後に同 symbol の板が再来しても、cache から消えているので no-op。"""
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response([("bk-1", book.model_dump_json().encode("utf-8"))]),
            _pull_response([("bk-2", book.model_dump_json().encode("utf-8"))]),
        ],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[
            [_swing_position_row(quantity=100, stop_loss_price="950")],
        ],
        paper_position_rows=[[_swing_position_row(quantity=100, stop_loss_price="950")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        # 1 回目 exit 確定 (cache から削除) → 2 回目は cache 空で no-op
        # cache TTL 30s で 2 回目は再 fetch しない
        s1 = await runner.run_once()
        s2 = await runner.run_once()
        return [s1, s2]

    [s1, s2] = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert s1.swing_exits == 1
    assert s2.swing_exits == 0
    assert len(_apply_fill_requests(supabase)) == 1
    assert _direct_fill_writes(supabase) == []


async def test_swing_no_bids_in_book_is_no_fill() -> None:
    """板の bids が空 → SELL 約定できないので swing_no_fills を計上。"""
    book = make_order_book(symbol="7203", bids=(), asks=(("1000", 100),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[_swing_position_row(quantity=100, stop_loss_price="950")]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert stats.swing_no_fills == 1
    assert stats.swing_exits == 0
    writes = [r for r in supabase.requests if r.method in {"POST", "PATCH", "DELETE"}]
    assert writes == []


async def test_swing_supabase_write_failure_keeps_position_in_cache() -> None:
    """exit 経路で atomic RPC が 5xx → write_error 計上、cache 維持。"""
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response([("bk-1", book.model_dump_json().encode("utf-8"))]),
            _pull_response([("bk-2", book.model_dump_json().encode("utf-8"))]),
        ],
    )
    supabase = _SupabaseRouter(
        list_position_rows=[[_swing_position_row(quantity=100, stop_loss_price="950")]],
        paper_position_rows=[
            [_swing_position_row(quantity=100, stop_loss_price="950")],
            [_swing_position_row(quantity=100, stop_loss_price="950")],
        ],
        rpc_status=500,
    )

    async def _body(runner: StreamRunner) -> Any:
        s1 = await runner.run_once()
        # cache 維持のため 2 回目も同条件で再評価
        # ただし 2 回目も同じ 500 でリトライ後 SupabaseError
        s2 = await runner.run_once()
        return [s1, s2]

    [s1, s2] = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert s1.swing_exits == 0
    assert s1.swing_write_errors == 1
    # 2 回目も同 symbol で再評価 (cache 維持)
    assert s2.swing_write_errors == 1


async def test_swing_cache_refresh_failure_returns_write_error() -> None:
    """list_paper_positions 自体が 5xx → swing_write_errors=1 の 1 サイクル分。"""
    book = make_order_book(symbol="7203", bids=(("950", 500),))
    pubsub = _PubSubRouter(
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )

    @dataclass
    class _Failing:
        calls: int = 0

        async def __call__(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if request.method == "GET" and request.url.path == "/rest/v1/positions":
                # tenacity は 3 回 retry → 全部 500 で SupabaseError
                return httpx.Response(500, text="boom")
            return httpx.Response(404, text=f"unmocked: {request.method} {request.url.path}")

    supabase_handler = _Failing()

    async def _noop_sleep(_: float) -> None:
        return None

    s = _settings()
    async with (
        PubSubSubscriber(
            project_id=s.pubsub_project_id,
            emulator_host=s.pubsub_emulator_host,
            transport=httpx.MockTransport(pubsub),
        ) as subscriber,
        SupabaseClient(
            url=s.supabase_url,
            secret_key=s.supabase_secret_key,
            transport=httpx.MockTransport(supabase_handler),
        ) as supa,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supa,
            settings=s,
            idle_backoff_seconds=1.0,
            sleep=_noop_sleep,
            wall_clock=lambda: DEFAULT_TS,
        )
        stats = await runner.run_once()

    assert stats.swing_write_errors == 1
    assert stats.swing_exits == 0


async def test_swing_cache_ttl_reuses_within_window() -> None:
    """TTL 内なら list_paper_positions は 1 度しか叩かない。"""
    book = make_order_book(symbol="7203", bids=(("1100", 500),))
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response([("bk-1", book.model_dump_json().encode("utf-8"))]),
            _pull_response([("bk-2", book.model_dump_json().encode("utf-8"))]),
        ],
    )
    # list_position_rows は 1 度だけ pop される想定
    supabase = _SupabaseRouter(
        list_position_rows=[
            [_swing_position_row(quantity=100, stop_loss_price="950", target_price="2000")]
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        s1 = await runner.run_once()
        s2 = await runner.run_once()
        return [s1, s2]

    await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    # list_paper_positions = symbol 無し GET 1 回のみ (2 回目は TTL 内で再フェッチなし)
    list_gets = [
        r
        for r in supabase.requests
        if r.method == "GET"
        and r.url.path == "/rest/v1/positions"
        and r.url.params.get("symbol") is None
    ]
    assert len(list_gets) == 1


# --- idempotency ---------------------------------------------------------


async def test_duplicate_buy_is_skipped_and_acked() -> None:
    """同一 signal_id の BUY が再配信されたとき skipped_duplicate になり ack される。"""
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        order_batches=[_pull_response([("ord-1", order.model_dump_json().encode("utf-8"))])],
        book_batches=[_pull_response([("bk-1", book.model_dump_json().encode("utf-8"))])],
    )
    # 冪等性チェックで「既存あり」を返す → 重複
    supabase = _SupabaseRouter(rpc_duplicate_responses=[True])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.skipped_duplicate == 1
    assert stats.filled == 0
    assert stats.no_fills == 0
    # 重複判定も同じ RPC 内で行い、direct table write は発生しない。
    assert len(_apply_fill_requests(supabase)) == 1
    assert _direct_fill_writes(supabase) == []
    # ack はされる
    ack_paths = [r.url.path for r in pubsub.acked]
    assert any(PAPER_ORDERS_SUB in p for p in ack_paths)


async def test_first_delivery_fills_second_delivery_skipped() -> None:
    """1 通目は約定、2 通目 (再配信) は skipped_duplicate。"""
    book = make_order_book(symbol="7203", asks=(("1000", 200),))
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100)
    data = order.model_dump_json().encode("utf-8")
    pubsub = _PubSubRouter(
        order_batches=[
            _pull_response([("ord-1", data)]),
            _pull_response([("ord-2", data)]),
        ],
        book_batches=[
            _pull_response([("bk-1", book.model_dump_json().encode("utf-8"))]),
            {},
        ],
    )
    # 1 通目: 重複なし → 約定。2 通目: 重複あり → skip
    supabase = _SupabaseRouter(
        paper_position_rows=[[]],  # 1 通目の read_paper_position
        rpc_duplicate_responses=[False, True],
    )

    async def _body(runner: StreamRunner) -> Any:
        s1 = await runner.run_once()
        s2 = await runner.run_once()
        return s1, s2

    s1, s2 = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)
    assert s1.filled == 1
    assert s1.skipped_duplicate == 0
    assert s2.filled == 0
    assert s2.skipped_duplicate == 1


async def test_market_data_stale_warns_and_recovers_during_jpx_session(caplog: Any) -> None:
    stale_book = make_order_book(
        symbol="7203",
        timestamp=datetime(2026, 4, 20, 0, 0, tzinfo=UTC),
    )
    fresh_book = make_order_book(
        symbol="7203",
        timestamp=datetime(2026, 4, 20, 0, 9, tzinfo=UTC),
    )
    pubsub = _PubSubRouter(
        book_batches=[
            _pull_response([("bk-1", stale_book.model_dump_json().encode("utf-8"))]),
            _pull_response([("bk-2", fresh_book.model_dump_json().encode("utf-8"))]),
        ]
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        runner.summary_log_interval_seconds = 0.0
        runner.wall_clock = lambda: datetime(2026, 4, 20, 0, 10, tzinfo=UTC)
        await runner.run_once()
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="oms_paper.streaming.runner")

    await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    stale = [
        record for record in caplog.records if getattr(record, "event", None) == "market_data_stale"
    ]
    assert len(stale) == 1
    assert stale[0].kind == "order_book"
    assert stale[0].latest_book_age_seconds == 600.0
    recovered = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_data_recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].kind == "order_book"
    assert recovered[0].latest_book_age_seconds == 60.0


# --- helpers --------------------------------------------------------------


def test_paper_position_factory_for_test_helper_used_elsewhere() -> None:
    pos = make_paper_position(symbol="X", quantity=50, holding_type=TradingStyle.SWING)
    assert pos.symbol == "X"
    assert pos.quantity == 50
    assert pos.holding_type is TradingStyle.SWING


def test_default_ts_offset_helper() -> None:
    assert (DEFAULT_TS + timedelta(seconds=1)).tzinfo is UTC
