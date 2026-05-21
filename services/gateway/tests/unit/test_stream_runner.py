from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from gateway.clients.pubsub import PubSubPublisher, PubSubSubscriber
from gateway.clients.supabase import SupabaseClient
from gateway.config import GatewaySettings, RiskConfig
from gateway.router import TopicRouting
from gateway.streaming.runner import StreamRunner
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.signal import UnifiedTradeSignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]

SUB = "gateway-trade-signals"
LIVE_TOPIC = "live-orders"
PAPER_TOPIC = "paper-orders"
SUPABASE_URL = "https://example.supabase.co"


def _settings(**overrides: Any) -> GatewaySettings:
    base: dict[str, Any] = dict(
        supabase_url=SUPABASE_URL,
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_trade_signals=SUB,
        pubsub_topic_live_orders=LIVE_TOPIC,
        pubsub_topic_paper_orders=PAPER_TOPIC,
        pubsub_pull_max_messages=10,
        capital=Decimal("1000000"),
    )
    base.update(overrides)
    return GatewaySettings(**base)


def _unified_payload(
    *,
    symbol: str = "7203",
    action: Action = Action.BUY,
    confidence: float = 0.8,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    holding_type: TradingStyle = TradingStyle.DAY,
    stop_loss_price: str | None = None,
    signal_id: UUID | None = None,
) -> bytes:
    body: dict[str, Any] = {
        "signal_id": str(signal_id or uuid4()),
        "symbol": symbol,
        "action": action.value,
        "confidence": confidence,
        "signal_source": signal_source.value,
        "strategy_signal_id_a": None,
        "strategy_signal_id_b": None,
        "holding_type": holding_type.value,
        "stop_loss_price": stop_loss_price,
        "target_price": None,
        "trailing_stop_pct": None,
        "max_hold_days": None,
        "created_at": "2026-04-20T09:00:00+00:00",
    }
    return json.dumps(body).encode("utf-8")


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
        "updated_at": "2026-04-23T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class _PubSubRouter:
    """Routes pull / ack / publish on the gateway Pub/Sub transport."""

    def __init__(self, *, pull_batches: list[dict[str, Any]]) -> None:
        self.pull_batches = list(pull_batches)
        self.published: list[httpx.Request] = []
        self.acked: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":pull"):
            body = self.pull_batches.pop(0) if self.pull_batches else {}
            return httpx.Response(200, json=body)
        if path.endswith(":acknowledge"):
            self.acked.append(request)
            return httpx.Response(200, json={})
        if path.endswith(":publish"):
            self.published.append(request)
            return httpx.Response(200, json={"messageIds": [f"pub-{len(self.published)}"]})
        return httpx.Response(404)


@dataclass
class _SupabaseRouter:
    """Stubs PostgREST responses in order for each endpoint."""

    system_status_rows: list[dict[str, Any]] = field(default_factory=list)
    positions_quantity_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    positions_price_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    daily_ohlcv_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    disable_status: int = 204
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/rest/v1/system_status":
            row = self.system_status_rows.pop(0) if self.system_status_rows else None
            return httpx.Response(200, json=[row] if row is not None else [])
        if request.method == "GET" and path == "/rest/v1/positions":
            select = request.url.params.get("select") or ""
            if "current_price" in select:
                rows = self.positions_price_rows.pop(0) if self.positions_price_rows else []
            else:
                rows = self.positions_quantity_rows.pop(0) if self.positions_quantity_rows else []
            return httpx.Response(200, json=rows)
        if request.method == "GET" and path == "/rest/v1/daily_ohlcv":
            rows = self.daily_ohlcv_rows.pop(0) if self.daily_ohlcv_rows else []
            return httpx.Response(200, json=rows)
        if request.method == "PATCH" and path == "/rest/v1/system_status":
            return httpx.Response(self.disable_status)
        return httpx.Response(404, text=f"unmocked: {request.method} {path}")


async def _with_runner(
    *,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    settings: GatewaySettings | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Any:
    settings = settings or _settings()

    async def _noop_sleep(_: float) -> None:
        return None

    def _wall_clock() -> datetime:
        return datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=httpx.MockTransport(pubsub),
        ) as subscriber,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=httpx.MockTransport(pubsub),
        ) as publisher,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            transport=httpx.MockTransport(supabase),
        ) as supa,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            supabase=supa,
            settings=settings,
            risk_config=RiskConfig.from_settings(settings),
            routing=TopicRouting(
                live_topic=settings.pubsub_topic_live_orders,
                paper_topic=settings.pubsub_topic_paper_orders,
            ),
            idle_backoff_seconds=1.0,
            sleep=sleep or _noop_sleep,
            wall_clock=_wall_clock,
        )
        return await run_body(runner)


async def test_buy_with_no_existing_position_publishes_to_live_orders() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],  # no existing LONG
        positions_price_rows=[[{"current_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.pulled == 1
    assert stats.approved == 1
    assert stats.rejected == 0
    assert stats.kill_switch_triggered == 0
    assert stats.acked == 1

    assert len(pubsub.published) == 1
    pub_req = pubsub.published[0]
    assert pub_req.url.path.endswith(f"/topics/{LIVE_TOPIC}:publish")
    body = json.loads(pub_req.content.decode())
    msg = body["messages"][0]
    attrs = msg["attributes"]
    assert attrs == {
        "symbol": "7203",
        "side": "BUY",
        "trade_mode": "live",
        "signal_source": "CONSENSUS",
    }
    order = json.loads(base64.b64decode(msg["data"]).decode("utf-8"))
    assert order["symbol"] == "7203"
    assert order["side"] == "BUY"
    # risk_amount = 1_000_000 * 0.02 = 20_000; risk/share = 2500 - 2400 = 100
    # raw qty = 200 → floor to 200 shares (lot 100)
    assert order["quantity"] == 200
    assert order["trade_mode"] == "live"
    assert order["order_type"] == "MARKET"

    assert json.loads(pubsub.acked[0].content.decode()) == {"ackIds": ["a1"]}


async def test_live_buy_quantity_is_capped_by_oms_limit() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"current_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(oms_live_max_qty_per_order=100),
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 100


async def test_live_buy_is_rejected_when_existing_live_exposure_exhausts_budget() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[
            [{"current_price": "2500"}],
            [{"quantity": 300, "current_price": "2500"}],
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []


async def test_paper_mode_publishes_to_paper_orders() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"current_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    assert pubsub.published[0].url.path.endswith(f"/topics/{PAPER_TOPIC}:publish")


async def test_sell_uses_existing_long_quantity() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.SELL))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[{"quantity": 300, "side": "LONG"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["side"] == "SELL"
    assert order["quantity"] == 300


async def test_sell_without_position_is_rejected() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.SELL))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []
    assert stats.acked == 1


async def test_buy_with_existing_long_is_rejected_without_price_lookup() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.BUY))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[{"quantity": 100, "side": "LONG"}]],
        positions_price_rows=[],  # would raise if consulted
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []


async def test_buy_without_latest_price_is_rejected() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.BUY))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],  # no existing LONG
        positions_price_rows=[[]],  # no price row
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []
    # live must NOT consult daily_ohlcv (fail-closed). Only positions GETs allowed.
    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert daily_calls == []


async def test_paper_buy_falls_back_to_daily_ohlcv_when_no_position() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],  # no existing LONG
        positions_price_rows=[[]],  # no current_price (no open position)
        daily_ohlcv_rows=[[{"close": "2500"}]],  # fallback
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    assert stats.rejected == 0
    assert pubsub.published[0].url.path.endswith(f"/topics/{PAPER_TOPIC}:publish")
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    # entry=2500 (daily_ohlcv) stop=2400 → risk/share=100, qty=200
    assert order["quantity"] == 200
    assert order["trade_mode"] == "paper"

    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert len(daily_calls) == 1
    assert daily_calls[0].url.params.get("symbol") == "eq.7203"


async def test_paper_buy_rejects_when_neither_position_nor_daily_ohlcv() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.BUY))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[]],
        daily_ohlcv_rows=[[]],  # no fallback either
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []


async def test_live_buy_without_position_does_not_fall_back_to_daily_ohlcv() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    # daily_ohlcv has a row, but live mode must not consult it. positions_price empty
    # → reject with missing_entry_price.
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[]],
        daily_ohlcv_rows=[[{"close": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []
    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert daily_calls == []


async def test_paper_buy_prefers_position_price_over_daily_ohlcv() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    # position has current_price → daily_ohlcv must not be consulted.
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"current_price": "2500"}]],
        daily_ohlcv_rows=[[{"close": "9999"}]],  # would skew lot calc if used
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 200  # 2500 - 2400 = 100/share, 20_000/100 = 200
    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert daily_calls == []


async def test_kill_switch_off_rejects_without_update() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.BUY))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(is_trading_allowed=False, trade_mode="live")],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert stats.kill_switch_triggered == 0
    patch_calls = [r for r in supabase.requests if r.method == "PATCH"]
    assert patch_calls == []


async def test_daily_loss_limit_flips_kill_switch() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.BUY))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(
                trade_mode="live",
                daily_pnl="-10000",
                daily_loss_limit="10000",
            )
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert stats.kill_switch_triggered == 1
    patch_calls = [r for r in supabase.requests if r.method == "PATCH"]
    assert len(patch_calls) == 1
    body = json.loads(patch_calls[0].content.decode())
    assert body["is_trading_allowed"] is False
    assert body["updated_at"].startswith("2026-04-20T09:00:00")
    assert pubsub.published == []


async def test_hold_action_is_rejected_without_publish() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.HOLD))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []
    assert stats.acked == 1


async def test_malformed_json_is_acked_immediately() -> None:
    pubsub = _PubSubRouter(pull_batches=[_pull_response([("a1", b"not-json")])])
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.parse_errors == 1
    assert stats.acked == 1
    assert pubsub.published == []
    assert supabase.requests == []


async def test_schema_invalid_is_acked_immediately() -> None:
    bad = json.dumps({"symbol": "7203"}).encode()
    pubsub = _PubSubRouter(pull_batches=[_pull_response([("a1", bad)])])
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.parse_errors == 1
    assert stats.acked == 1
    assert pubsub.published == []


async def test_idle_pull_triggers_backoff_sleep() -> None:
    pubsub = _PubSubRouter(pull_batches=[{}])
    supabase = _SupabaseRouter()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=1)

    await _with_runner(pubsub=pubsub, supabase=supabase, sleep=_sleep, run_body=_body)
    assert sleeps == [1.0]


async def test_below_min_lot_is_rejected() -> None:
    # capital=1_000_000 * 0.02 = 20_000 risk. With entry=2500 stop=2499 (risk/share=1),
    # raw qty = 20000 but the more useful edge: stop at 99.99 spread → qty that floors < 100
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(action=Action.BUY, stop_loss_price="1"),
                    )
                ]
            )
        ]
    )
    # entry = 1_000_000_000 forces 20_000 / 999_999_999 → 0 shares
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"current_price": "1000000000"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    assert pubsub.published == []


def test_parse_signal_round_trip() -> None:
    from gateway.clients.pubsub import PulledMessage
    from gateway.streaming.runner import _parse_signal

    good = _unified_payload()
    msg = PulledMessage(ack_id="a1", message_id="m1", data=good, attributes={})
    parsed = _parse_signal(msg)
    assert isinstance(parsed, UnifiedTradeSignal)
    assert parsed.symbol == "7203"

    bad = PulledMessage(ack_id="a1", message_id="m1", data=b"not-json", attributes={})
    assert _parse_signal(bad) is None

    array_payload = PulledMessage(ack_id="a1", message_id="m1", data=b"[1,2]", attributes={})
    assert _parse_signal(array_payload) is None

    missing = PulledMessage(
        ack_id="a1",
        message_id="m1",
        data=json.dumps({"symbol": "7203"}).encode(),
        attributes={},
    )
    assert _parse_signal(missing) is None


# Keep pytest happy even when no tests import TradeMode directly.
_ = TradeMode
