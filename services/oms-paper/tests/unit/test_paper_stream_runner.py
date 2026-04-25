from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from trade_contracts.enums import Side, TradingStyle

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
    insert_trade_status: int = 204
    insert_position_status: int = 204
    update_position_status: int = 204
    delete_position_status: int = 204
    requests: list[httpx.Request] = field(default_factory=list)

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
                rows = self.list_position_rows.pop(0) if self.list_position_rows else []
                return httpx.Response(200, json=rows)
            rows = self.paper_position_rows.pop(0) if self.paper_position_rows else []
            return httpx.Response(200, json=rows)
        if method == "POST" and path == "/rest/v1/positions":
            return httpx.Response(self.insert_position_status, content=b"")
        if method == "PATCH" and path == "/rest/v1/positions":
            return httpx.Response(self.update_position_status, content=b"")
        if method == "DELETE" and path == "/rest/v1/positions":
            return httpx.Response(self.delete_position_status, content=b"")
        if method == "POST" and path == "/rest/v1/trades_paper":
            return httpx.Response(self.insert_trade_status, content=b"")
        return httpx.Response(404, text=f"unmocked: {method} {path}")


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
        "trailing_stop_pct": None,
        "opened_at": "2026-04-25T09:00:00+00:00",
    }
    row.update(overrides)
    return row


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
    order = make_order_request(symbol="7203", side=Side.BUY, quantity=100, created_at=DEFAULT_TS)
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

    posts = [r for r in supabase.requests if r.method == "POST"]
    paths = [r.url.path for r in posts]
    assert "/rest/v1/trades_paper" in paths
    assert "/rest/v1/positions" in paths

    insert_trade = next(r for r in posts if r.url.path == "/rest/v1/trades_paper")
    body = json.loads(insert_trade.content.decode())
    assert body[0]["symbol"] == "7203"
    assert body[0]["price"] == "1000"
    assert body[0]["quantity"] == 100

    insert_pos = next(r for r in posts if r.url.path == "/rest/v1/positions")
    pos_body = json.loads(insert_pos.content.decode())[0]
    assert pos_body["trade_type"] == "paper"
    assert pos_body["entry_price"] == "1000"
    assert pos_body["current_price"] == "1000"


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
    assert supabase.requests == []  # no Supabase calls
    ack_paths = [r.url.path for r in pubsub.acked]
    assert any(PAPER_ORDERS_SUB in p for p in ack_paths)


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
    patch = next(r for r in supabase.requests if r.method == "PATCH")
    body = json.loads(patch.content.decode())
    assert body["quantity"] == 200
    assert body["entry_price"] == "1050"  # (1000*100 + 1100*100)/200 = 1050
    assert "current_price" not in body
    posts = [r for r in supabase.requests if r.method == "POST"]
    assert all(r.url.path != "/rest/v1/positions" for r in posts)


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
    deletes = [r for r in supabase.requests if r.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].url.params.get("symbol") == "eq.7203"


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
    patch = next(r for r in supabase.requests if r.method == "PATCH")
    body = json.loads(patch.content.decode())
    assert body["quantity"] == 60
    assert body["entry_price"] == "900"  # SELL doesn't change average


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
    assert all(r.method == "GET" for r in supabase.requests)


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
    assert supabase.requests == []  # no read because we short-circuit on no fill


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
        insert_trade_status=500,  # transient error → SupabaseError
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
    deletes = [r for r in supabase.requests if r.method == "DELETE"]
    assert len(deletes) == 1
    posts = [r for r in supabase.requests if r.method == "POST"]
    assert any(r.url.path == "/rest/v1/trades_paper" for r in posts)


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


# --- helpers --------------------------------------------------------------


def test_paper_position_factory_for_test_helper_used_elsewhere() -> None:
    pos = make_paper_position(symbol="X", quantity=50, holding_type=TradingStyle.SWING)
    assert pos.symbol == "X"
    assert pos.quantity == 50
    assert pos.holding_type is TradingStyle.SWING


def test_default_ts_offset_helper() -> None:
    assert (DEFAULT_TS + timedelta(seconds=1)).tzinfo is UTC
