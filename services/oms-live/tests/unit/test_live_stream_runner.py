"""StreamRunner (oms-live) の単体テスト。

httpx MockTransport で Pub/Sub / Supabase / kabu の 3 系統をスタブし、
runner の主要パス (filled / rejected / timeout / Supabase 失敗 / closeout) を検証する。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from oms_live._testing import (
    DEFAULT_TS,
    make_order_request,
)
from oms_live.clients.pubsub import PubSubSubscriber
from oms_live.clients.supabase import SupabaseClient, SupabaseError
from oms_live.config import OmsLiveSettings
from oms_live.kabu_client import KabuLiveClient
from oms_live.streaming.runner import StreamRunner
from trade_contracts.enums import Side

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


LIVE_ORDERS_SUB = "oms-live-live-orders"


def _settings(**overrides: Any) -> OmsLiveSettings:
    base: dict[str, Any] = dict(
        kabu_api_base_url="http://localhost:18081/kabusapi",
        kabu_api_password="api-pw",
        kabu_order_password="order-pw",
        kabu_default_exchange=1,
        kabu_account_type=4,
        supabase_url="https://example.supabase.co",
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_live_orders=LIVE_ORDERS_SUB,
        pubsub_pull_max_messages=10,
        order_fill_poll_interval_seconds=0.0,
        order_fill_timeout_seconds=5.0,
    )
    base.update(overrides)
    return OmsLiveSettings(**base)


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


# --- routers ----------------------------------------------------------------


class _PubSubRouter:
    def __init__(self, *, batches: list[dict[str, Any]] | None = None) -> None:
        self.batches = list(batches or [])
        self.acked: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":pull"):
            body = self.batches.pop(0) if self.batches else {}
            return httpx.Response(200, json=body)
        if path.endswith(":acknowledge"):
            self.acked.append(request)
            return httpx.Response(200, json={})
        return httpx.Response(404, text=f"unmocked: {path}")


@dataclass
class _SupabaseRouter:
    live_position_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    list_position_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    system_status_rows: list[dict[str, Any]] = field(default_factory=list)
    # GET /rest/v1/trades_live (existence check) のレスポンス列。
    # 1 回の order_id check につき 1 件 pop。空ならデフォルト [] (重複なし) を返す。
    trades_live_check_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    insert_trade_status: int = 204
    insert_position_status: int = 204
    update_position_status: int = 204
    delete_position_status: int = 204
    update_system_status: int = 204
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method
        if method == "GET" and path == "/rest/v1/system_status":
            row = self.system_status_rows.pop(0) if self.system_status_rows else None
            return httpx.Response(200, json=[row] if row is not None else [])
        if method == "PATCH" and path == "/rest/v1/system_status":
            return httpx.Response(self.update_system_status, content=b"")
        if method == "GET" and path == "/rest/v1/positions":
            symbol = request.url.params.get("symbol")
            if symbol is None:
                rows = self.list_position_rows.pop(0) if self.list_position_rows else []
                return httpx.Response(200, json=rows)
            rows = self.live_position_rows.pop(0) if self.live_position_rows else []
            return httpx.Response(200, json=rows)
        if method == "POST" and path == "/rest/v1/positions":
            return httpx.Response(self.insert_position_status, content=b"")
        if method == "PATCH" and path == "/rest/v1/positions":
            return httpx.Response(self.update_position_status, content=b"")
        if method == "DELETE" and path == "/rest/v1/positions":
            return httpx.Response(self.delete_position_status, content=b"")
        if method == "GET" and path == "/rest/v1/trades_live":
            rows = self.trades_live_check_rows.pop(0) if self.trades_live_check_rows else []
            return httpx.Response(200, json=rows)
        if method == "POST" and path == "/rest/v1/trades_live":
            return httpx.Response(self.insert_trade_status, content=b"")
        return httpx.Response(404, text=f"unmocked: {method} {path}")


@dataclass
class _KabuRouter:
    """kabu API のスタブ。

    ``order_states`` は ``OrderId`` -> 順番に返す ``/orders`` レスポンス列。
    1 OrderId につき同 list の先頭から順に消費する (poll の挙動を制御するため)。
    """

    sendorder_responses: list[dict[str, Any]] = field(default_factory=list)
    order_states: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    cancel_response: dict[str, Any] = field(default_factory=lambda: {"Result": 0, "OrderId": ""})
    sendorder_status: int = 200
    cancel_status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method
        if path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        if path.endswith("/sendorder"):
            if not self.sendorder_responses:
                return httpx.Response(500, json={"Code": 9999, "Message": "no stub"})
            body = self.sendorder_responses.pop(0)
            return httpx.Response(self.sendorder_status, json=body)
        if path.endswith("/cancelorder"):
            return httpx.Response(self.cancel_status, json=self.cancel_response)
        if method == "GET" and path.endswith("/orders"):
            order_id = request.url.params.get("id", "")
            queue = self.order_states.get(order_id, [])
            if not queue:
                return httpx.Response(200, json=[])
            payload = queue.pop(0) if len(queue) > 1 else queue[0]
            # 最後の 1 件は repeat 想定 (poll が継続したら同じ状態を返す)
            return httpx.Response(200, json=[payload])
        return httpx.Response(404, text=f"unmocked: {method} {path}")


# --- helpers ----------------------------------------------------------------


def _system_status_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "is_trading_allowed": True,
        "trade_mode": "live",
        "trading_style": "day",
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
        "daily_loss_limit": "10000",
        "weekly_loss_limit": "30000",
        "monthly_loss_limit": "100000",
        "updated_at": "2026-04-29T00:00:00+00:00",
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
        "opened_at": "2026-04-29T09:00:00+00:00",
    }
    row.update(overrides)
    return row


def _kabu_order_payload(
    *,
    order_id: str = "OID-1",
    symbol: str = "7203",
    side: str = "2",
    state: int = 3,
    cum_qty: int = 100,
    order_qty: int = 100,
    detail_price: int = 1000,
) -> dict[str, Any]:
    return {
        "ID": order_id,
        "Symbol": symbol,
        "Side": side,
        "State": state,
        "OrderState": state,
        "CumQty": cum_qty,
        "OrderQty": order_qty,
        "Price": 0,
        "RecvTime": "2026-04-29T09:00:00+09:00",
        "Details": [
            {
                "ExecutionID": f"E-{order_id}",
                "ExecutionTime": "2026-04-29T09:00:01+09:00",
                "Price": detail_price,
                "Qty": cum_qty,
            }
        ]
        if cum_qty > 0
        else [],
    }


async def _with_runner(
    *,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    kabu: _KabuRouter,
    settings: OmsLiveSettings | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
    sleep: Callable[[float], Awaitable[None]] | None = None,
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
        KabuLiveClient(
            base_url=s.kabu_api_base_url,
            api_password=s.kabu_api_password,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(kabu)),
        ) as kabu_client,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supa,
            kabu=kabu_client,
            settings=s,
            idle_backoff_seconds=0.0,
            sleep=sleep or _noop_sleep,
            wall_clock=_wall_clock,
        )
        return await run_body(runner)


# --- run_once: BUY 新規 -----------------------------------------------------


async def test_run_once_buy_new_position_writes_trade_and_position_and_acks() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter(live_position_rows=[[]])  # 既存ポジション無し
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-1"}],
        order_states={
            "OID-1": [
                _kabu_order_payload(order_id="OID-1", state=3, cum_qty=100, detail_price=1000)
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.orders_pulled == 1
    assert stats.filled == 1
    assert stats.no_fills == 0
    assert stats.acked == 1

    # Supabase に trades_live INSERT と positions INSERT が起きていること
    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") in methods
    assert ("POST", "/rest/v1/positions") in methods
    # BUY なので realized_pnl 加算 (PATCH /system_status) は無し
    assert ("PATCH", "/rest/v1/system_status") not in methods
    # 冪等性 check (GET /trades_live?order_id=eq.<id>) が走っていること
    insert_req = next(
        r for r in supabase.requests if r.method == "POST" and r.url.path == "/rest/v1/trades_live"
    )
    inserted = json.loads(insert_req.content.decode())[0]
    assert inserted["order_id"] == str(order.order_id)

    # Pub/Sub ack も飛んでいること
    assert len(pubsub.acked) == 1
    body = json.loads(pubsub.acked[0].content.decode())
    assert body == {"ackIds": ["a1"]}


async def test_run_once_skips_when_order_id_already_in_trades_live() -> None:
    """前回成功した同一 order_id を再配信されたら sendorder せず ack する。"""
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    # existence check が「あり」を返す
    supabase = _SupabaseRouter(
        trades_live_check_rows=[[{"order_id": str(order.order_id)}]],
    )
    kabu = _KabuRouter()  # sendorder_responses なし — 呼ばれたら 500

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.orders_pulled == 1
    assert stats.filled == 0
    assert stats.no_fills == 0
    assert stats.skipped_duplicate == 1
    assert stats.acked == 1

    # kabu には一切リクエストを投げていないこと (token も含めて 0)
    assert kabu.requests == []
    # Supabase には existence check の GET だけ
    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert methods == [("GET", "/rest/v1/trades_live")]
    # ack も飛ぶ
    assert len(pubsub.acked) == 1


async def test_run_once_sell_full_close_calls_add_realized_pnl_and_delete() -> None:
    order = make_order_request(side=Side.SELL, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter(
        live_position_rows=[[_position_row(quantity=100, entry_price="1000")]],
        system_status_rows=[_system_status_row(daily_pnl="0", weekly_pnl="0", monthly_pnl="0")],
    )
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-2"}],
        order_states={
            "OID-2": [
                _kabu_order_payload(
                    order_id="OID-2",
                    side="1",
                    state=3,
                    cum_qty=100,
                    order_qty=100,
                    detail_price=1100,
                )
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 1
    assert stats.no_fills == 0

    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") in methods
    assert ("DELETE", "/rest/v1/positions") in methods
    # SELL → realized_pnl 加算で system_status 読み取り + PATCH
    assert ("PATCH", "/rest/v1/system_status") in methods
    # PATCH ボディに +10000.00 (1100-1000)*100 が反映されていること
    # (vwap 丸めが 0.01 円単位になったため文字列表現は "10000.00")
    patch_req = next(
        r
        for r in supabase.requests
        if r.method == "PATCH" and r.url.path == "/rest/v1/system_status"
    )
    body = json.loads(patch_req.content.decode())
    assert body == {
        "daily_pnl": "10000.00",
        "weekly_pnl": "10000.00",
        "monthly_pnl": "10000.00",
    }


async def test_run_once_sendorder_rejected_records_no_fill_and_acks() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter(sendorder_responses=[{"Result": 4, "Message": "rejected"}])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 0
    assert stats.no_fills == 1
    assert stats.acked == 1
    # Supabase の書込系 (POST/PATCH/DELETE) は呼ばない (existence check の GET は走る)
    write_methods = {"POST", "PATCH", "DELETE"}
    assert [r for r in supabase.requests if r.method in write_methods] == []
    # ack は飛ぶ
    assert len(pubsub.acked) == 1


async def test_run_once_poll_timeout_calls_cancel_and_acks_no_fill() -> None:
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()
    # always returns state=2 (processing), never reaches done → poll times out
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-X"}],
        order_states={
            "OID-X": [_kabu_order_payload(order_id="OID-X", state=2, cum_qty=0, order_qty=100)]
        },
    )

    # monotonic を進めてタイムアウトを起こす
    times = iter([0.0, 0.0, 100.0])

    async def _body(runner: StreamRunner) -> Any:
        runner.monotonic = lambda: next(times)
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 0
    assert stats.no_fills == 1
    assert stats.acked == 1
    # cancel が呼ばれていること
    cancel_calls = [r for r in kabu.requests if r.url.path.endswith("/cancelorder")]
    assert len(cancel_calls) == 1


async def test_run_once_poll_continues_through_state3_zero_cum_qty_then_filled_at_state5() -> None:
    """State=3 + CumQty=0 (中間状態) で poll が継続し、State=5 + CumQty>0 で filled。

    2026-05-07 本番実機で発生した実害ケース (kabu /orders 初回応答が State=3 + CumQty=0
    の中間状態) の回帰テスト。修正前は 1 回目の応答で誤って cancelled 扱いされ
    no_fill ack → kabu は実発注済 / Supabase 空 で乖離していた。
    """

    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter(live_position_rows=[[]])
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-INTERMEDIATE"}],
        order_states={
            "OID-INTERMEDIATE": [
                # 1 回目: 取引所に流れた中間状態 (約定 Detail 未着)
                _kabu_order_payload(order_id="OID-INTERMEDIATE", state=3, cum_qty=0, order_qty=100),
                # 2 回目以降: 終端 + 完全約定 (本番実機の通常パターン)
                _kabu_order_payload(
                    order_id="OID-INTERMEDIATE",
                    state=5,
                    cum_qty=100,
                    order_qty=100,
                    detail_price=1500,
                ),
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 1
    assert stats.no_fills == 0
    assert stats.acked == 1
    # poll が 2 回以上呼ばれていること (1 回目で抜けていない証拠)
    get_order_calls = [
        r for r in kabu.requests if r.method == "GET" and r.url.path.endswith("/orders")
    ]
    assert len(get_order_calls) >= 2
    # Supabase に trades_live INSERT が走っていること (Supabase 不整合が起きない)
    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") in methods


async def test_run_once_poll_continues_through_state3_zero_cum_qty_then_cancelled_at_state5() -> (
    None
):
    """State=3 + CumQty=0 で poll 継続 → State=5 + CumQty=0 で cancelled として終端。"""

    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-CXL"}],
        order_states={
            "OID-CXL": [
                _kabu_order_payload(order_id="OID-CXL", state=3, cum_qty=0, order_qty=100),
                _kabu_order_payload(order_id="OID-CXL", state=5, cum_qty=0, order_qty=100),
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 0
    assert stats.no_fills == 1
    assert stats.acked == 1
    # cancel は呼ばない (約定確定状態で終端しているため)
    cancel_calls = [r for r in kabu.requests if r.url.path.endswith("/cancelorder")]
    assert cancel_calls == []
    # Supabase の書込系は呼ばれない
    write_methods = {"POST", "PATCH", "DELETE"}
    assert [r for r in supabase.requests if r.method in write_methods] == []


async def test_run_once_supabase_write_failure_aborts_runner_without_ack() -> None:
    """fail-fast: sendorder 成功後に Supabase 書込が失敗すると ``SupabaseError`` を伝播させる。

    バッチ内で 1 件しか処理していないので flush 対象の ack は無く、現メッセージは
    未 ack のまま残る。プロセスが手動回復後に再起動されると、再配信が来るが
    ``live_trade_exists_for_order_id`` で skip される (運用前提)。
    """
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter(
        live_position_rows=[[]],
        insert_trade_status=500,  # 500 は SupabaseError(transient) になる
    )
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-3"}],
        order_states={
            "OID-3": [
                _kabu_order_payload(order_id="OID-3", state=3, cum_qty=100, detail_price=1000)
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    with pytest.raises(SupabaseError):
        await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    # 失敗したメッセージは ack されないので Pub/Sub の再配信に残る
    assert pubsub.acked == []


async def test_run_once_flushes_prior_acks_before_fail_fast() -> None:
    """fail-fast 時、バッチ内で既に成功した分は ack を flush してから raise する。

    1 件目: parse 失敗 (即 ack)
    2 件目: sendorder 成功 + Supabase 書込失敗 → 致命例外
    expected: 1 件目の ack だけが flush される (再起動時に二度処理しないため)。
    """
    bad_payload = b"not-json"
    good = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(
        batches=[
            _pull_response(
                [
                    ("a-bad", bad_payload),
                    ("a-good", good.model_dump_json().encode()),
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        live_position_rows=[[]],
        insert_trade_status=500,
    )
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-FF"}],
        order_states={
            "OID-FF": [
                _kabu_order_payload(order_id="OID-FF", state=3, cum_qty=100, detail_price=1000)
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    with pytest.raises(SupabaseError):
        await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    # 1 件目 (parse error → 即 ack) だけが flush されている
    assert len(pubsub.acked) == 1
    body = json.loads(pubsub.acked[0].content.decode())
    assert body == {"ackIds": ["a-bad"]}


async def test_run_once_poison_message_is_acked() -> None:
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", b"not-json")])])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.parse_errors == 1
    assert stats.acked == 1
    assert kabu.requests == []
    assert supabase.requests == []


async def test_run_once_empty_pull_returns_zero_stats() -> None:
    pubsub = _PubSubRouter(batches=[{}])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.orders_pulled == 0
    assert stats.acked == 0


# --- run (loop) -------------------------------------------------------------


async def test_run_invokes_idle_backoff_when_empty() -> None:
    pubsub = _PubSubRouter(batches=[{}, {}])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()
    sleep_calls: list[float] = []

    async def _sleep(s: float) -> None:
        sleep_calls.append(s)

    async def _body(runner: StreamRunner) -> Any:
        runner.idle_backoff_seconds = 1.5
        return await runner.run(iterations=2)

    stats_list = await _with_runner(
        pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body, sleep=_sleep
    )
    assert len(stats_list) == 2
    assert sleep_calls == [1.5, 1.5]


# --- closeout ---------------------------------------------------------------


async def test_run_closeout_skipped_when_swing() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(system_status_rows=[_system_status_row(trading_style="swing")])
    kabu = _KabuRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert result.triggered is False
    assert result.skipped_reason == "trading_style_swing"
    assert result.closed == 0
    # kabu API は呼ばない
    assert kabu.requests == []


async def test_run_closeout_no_positions_returns_triggered_with_zero_closed() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[]],
    )
    kabu = _KabuRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert result.triggered is True
    assert result.skipped_reason is None
    assert result.positions_seen == 0
    assert result.closed == 0
    assert kabu.requests == []


async def test_run_closeout_sells_each_position_and_writes_trade_and_pnl() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(trading_style="day"),  # 最初の trading_style 判定
            _system_status_row(trading_style="day"),  # add_realized_pnl の read
        ],
        list_position_rows=[[_position_row(symbol="7203", quantity=100, entry_price="1000")]],
    )
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-CO-1"}],
        order_states={
            "OID-CO-1": [
                _kabu_order_payload(
                    order_id="OID-CO-1",
                    side="1",
                    state=3,
                    cum_qty=100,
                    order_qty=100,
                    detail_price=1100,
                )
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert result.triggered is True
    assert result.positions_seen == 1
    assert result.closed == 1
    assert result.no_fills == 0

    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") in methods
    assert ("DELETE", "/rest/v1/positions") in methods
    assert ("PATCH", "/rest/v1/system_status") in methods


async def test_run_closeout_handles_sendorder_rejected_as_no_fill() -> None:
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trading_style="day")],
        list_position_rows=[[_position_row(symbol="7203", quantity=100, entry_price="1000")]],
    )
    kabu = _KabuRouter(sendorder_responses=[{"Result": 4, "Message": "no liquidity"}])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert result.triggered is True
    assert result.positions_seen == 1
    assert result.closed == 0
    assert result.no_fills == 1
    # 約定無しなので realized_pnl 加算は呼ばれない
    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") not in methods
    assert ("DELETE", "/rest/v1/positions") not in methods
    assert ("PATCH", "/rest/v1/system_status") not in methods


async def test_run_closeout_read_system_status_failure_returns_skipped() -> None:
    pubsub = _PubSubRouter()
    # system_status_rows が空 → SupabaseError -> 例外を握って skipped_reason を返す
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert result.triggered is False
    assert result.skipped_reason == "read_system_status_failed"


# --- partial fill ---------------------------------------------------------


async def test_run_once_partial_fill_writes_trade_and_patches_position() -> None:
    order = make_order_request(side=Side.BUY, quantity=300)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter(
        live_position_rows=[[_position_row(symbol="7203", quantity=100, entry_price="1000")]],
    )
    kabu = _KabuRouter(
        sendorder_responses=[{"Result": 0, "OrderId": "OID-PART"}],
        order_states={
            "OID-PART": [
                _kabu_order_payload(
                    order_id="OID-PART",
                    state=3,
                    cum_qty=200,
                    order_qty=300,
                    detail_price=1100,
                )
            ]
        },
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, kabu=kabu, run_body=_body)
    assert stats.filled == 1
    methods = [(r.method, r.url.path) for r in supabase.requests]
    assert ("POST", "/rest/v1/trades_live") in methods
    # 既存 100 + 新規 200 → 300 → PATCH (UPSERT 経路)
    assert ("PATCH", "/rest/v1/positions") in methods
    # PATCH の数量が 300、entry_price は加重平均
    patch = next(
        r for r in supabase.requests if r.method == "PATCH" and r.url.path == "/rest/v1/positions"
    )
    body = json.loads(patch.content.decode())
    assert body["quantity"] == 300
    assert Decimal(body["entry_price"]) == Decimal("1067")  # (1000*100+1100*200)/300


# --- Phase 3 safety knobs ---------------------------------------------------


async def test_run_once_safety_rejects_when_symbol_not_in_allowlist() -> None:
    """``oms_live_allowed_symbols`` に含まれない銘柄は sendorder せず safety_rejected。"""
    order = make_order_request(symbol="9999", side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()  # existence check は default [] (重複なし)
    kabu = _KabuRouter()  # sendorder_responses 無し → 呼ばれたら 500

    settings = _settings(oms_live_allowed_symbols="7203,9984")

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub, supabase=supabase, kabu=kabu, settings=settings, run_body=_body
    )
    assert stats.safety_rejected == 1
    assert stats.filled == 0
    assert stats.no_fills == 0
    assert stats.acked == 1
    # kabu には一切リクエスト無し (token 含む)
    assert kabu.requests == []
    # Supabase は existence check の GET だけ (書込なし)
    write_methods = {"POST", "PATCH", "DELETE"}
    assert [r for r in supabase.requests if r.method in write_methods] == []


async def test_run_once_safety_rejects_when_qty_exceeds_max() -> None:
    """``oms_live_max_qty_per_order`` 超過は sendorder せず safety_rejected。"""
    order = make_order_request(side=Side.BUY, quantity=500)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()

    settings = _settings(oms_live_max_qty_per_order=100)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub, supabase=supabase, kabu=kabu, settings=settings, run_body=_body
    )
    assert stats.safety_rejected == 1
    assert stats.filled == 0
    assert kabu.requests == []


async def test_run_once_dry_run_skips_sendorder_and_supabase_writes() -> None:
    """``oms_live_dry_run=True`` は sendorder も Supabase 書込もせずに ack。"""
    order = make_order_request(side=Side.BUY, quantity=100)
    pubsub = _PubSubRouter(batches=[_pull_response([("a1", order.model_dump_json().encode())])])
    supabase = _SupabaseRouter()
    kabu = _KabuRouter()

    settings = _settings(oms_live_dry_run=True)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub, supabase=supabase, kabu=kabu, settings=settings, run_body=_body
    )
    assert stats.dry_run_skipped == 1
    assert stats.filled == 0
    assert stats.no_fills == 0
    assert stats.acked == 1
    # kabu は一切呼ばない
    assert kabu.requests == []
    # Supabase は existence check の GET だけ
    write_methods = {"POST", "PATCH", "DELETE"}
    assert [r for r in supabase.requests if r.method in write_methods] == []


async def test_run_closeout_dry_run_skips_immediately() -> None:
    """``oms_live_dry_run=True`` の closeout は read_system_status も呼ばず即 no-op。"""
    pubsub = _PubSubRouter()
    supabase = _SupabaseRouter()  # system_status_rows 空 — 呼ばれたら不整合
    kabu = _KabuRouter()

    settings = _settings(oms_live_dry_run=True)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_closeout()

    result = await _with_runner(
        pubsub=pubsub, supabase=supabase, kabu=kabu, settings=settings, run_body=_body
    )
    assert result.triggered is False
    assert result.skipped_reason == "dry_run"
    assert result.closed == 0
    # Supabase / kabu には一切触らない
    assert supabase.requests == []
    assert kabu.requests == []


# --- sanity --------------------------------------------------------------


def test_default_ts_is_aware() -> None:
    assert DEFAULT_TS.tzinfo is UTC
