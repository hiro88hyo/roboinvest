from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from gateway.clients.pubsub import PubSubError, PubSubPublisher, PubSubSubscriber
from gateway.clients.supabase import SupabaseClient
from gateway.config import GatewaySettings, RiskConfig
from gateway.order_archive import OrderArchiveWriter
from gateway.router import TopicRouting
from gateway.streaming.runner import StreamRunner
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.order import OrderRequest
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
    price: str | None = None,
    action: Action = Action.BUY,
    confidence: float = 0.8,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    holding_type: TradingStyle = TradingStyle.DAY,
    stop_loss_price: str | None = None,
    signal_id: UUID | None = None,
    strategy_signal_id_a: UUID | None = None,
    strategy_signal_id_b: UUID | None = None,
    created_at: str = "2026-04-20T09:00:00+00:00",
) -> bytes:
    body: dict[str, Any] = {
        "signal_id": str(signal_id or uuid4()),
        "symbol": symbol,
        "price": price,
        "action": action.value,
        "confidence": confidence,
        "signal_source": signal_source.value,
        "strategy_signal_id_a": str(strategy_signal_id_a) if strategy_signal_id_a else None,
        "strategy_signal_id_b": str(strategy_signal_id_b) if strategy_signal_id_b else None,
        "holding_type": holding_type.value,
        "stop_loss_price": stop_loss_price,
        "target_price": None,
        "trailing_stop_pct": None,
        "max_hold_days": None,
        "created_at": created_at,
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


def _market_regime_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "valid_date": "2026-04-20",
        "regime": "RISK_OFF",
        "confidence": "0.86",
        "buy_enabled": False,
        "position_size_multiplier": "0.25",
        "source": "universe_scanner",
        "rationale": ["weak breadth"],
        "metrics": {"down_ratio": 0.82},
        "created_at": "2026-04-20T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class _PubSubRouter:
    """Routes pull / ack / publish on the gateway Pub/Sub transport."""

    def __init__(self, *, pull_batches: list[dict[str, Any]], publish_status: int = 200) -> None:
        self.pull_batches = list(pull_batches)
        self.publish_status = publish_status
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
            if self.publish_status >= 300:
                return httpx.Response(self.publish_status, text="publish failed")
            return httpx.Response(200, json={"messageIds": [f"pub-{len(self.published)}"]})
        return httpx.Response(404)


class _FakeKabuWallet:
    def __init__(self, outcomes: list[Decimal | Exception]) -> None:
        self.outcomes = list(outcomes)

    async def read_stock_account_wallet(self) -> Decimal:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class _SupabaseRouter:
    """Stubs PostgREST responses in order for each endpoint."""

    system_status_rows: list[dict[str, Any]] = field(default_factory=list)
    positions_quantity_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    positions_price_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    daily_ohlcv_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    trades_live_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    trades_paper_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    market_regime_rows: list[list[dict[str, Any]]] = field(default_factory=list)
    risk_reservation_rows: list[dict[str, Any]] = field(default_factory=list)
    released_risk_order_ids: list[str] = field(default_factory=list)
    disable_status: int = 204
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/rest/v1/rpc/gateway_check_kill_switch":
            row = self.system_status_rows.pop(0) if self.system_status_rows else None
            if row is None:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[_kill_switch_decision_row(row)])
        if request.method == "POST" and path == "/rest/v1/rpc/gateway_check_and_reserve_risk":
            if self.risk_reservation_rows:
                return httpx.Response(200, json=[self.risk_reservation_rows.pop(0)])
            payload = json.loads(request.content.decode())
            risk_amount = Decimal(str(payload["p_risk_amount"]))
            return httpx.Response(
                200,
                json=[
                    {
                        "passed": True,
                        "reason": None,
                        "reserved": True,
                        "active_risk_before": "0",
                        "active_risk_after": str(risk_amount),
                        "daily_pnl": "0",
                        "daily_loss_limit": "10000",
                        "weekly_pnl": "0",
                        "weekly_loss_limit": "30000",
                        "monthly_pnl": "0",
                        "monthly_loss_limit": "100000",
                    }
                ],
            )
        if request.method == "POST" and path == "/rest/v1/rpc/gateway_release_risk_reservation":
            payload = json.loads(request.content.decode())
            self.released_risk_order_ids.append(str(payload["p_order_id"]))
            return httpx.Response(
                200,
                json=[{"released": True, "order_id": payload["p_order_id"], "status": "released"}],
            )
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
        if request.method == "GET" and path == "/rest/v1/market_regime":
            rows = self.market_regime_rows.pop(0) if self.market_regime_rows else []
            return httpx.Response(200, json=rows)
        if request.method == "GET" and path == "/rest/v1/trades_live":
            rows = self.trades_live_rows.pop(0) if self.trades_live_rows else []
            return httpx.Response(200, json=rows)
        if request.method == "GET" and path == "/rest/v1/trades_paper":
            rows = self.trades_paper_rows.pop(0) if self.trades_paper_rows else []
            return httpx.Response(200, json=rows)
        if request.method == "PATCH" and path == "/rest/v1/system_status":
            return httpx.Response(self.disable_status)
        return httpx.Response(404, text=f"unmocked: {request.method} {path}")


def _kill_switch_decision_row(row: dict[str, Any]) -> dict[str, Any]:
    reason: str | None = None
    disabled = False
    if not row["is_trading_allowed"]:
        reason = "kill_switch_off"
    elif row["trade_mode"] == "live":
        daily_pnl = Decimal(str(row["daily_pnl"]))
        weekly_pnl = Decimal(str(row["weekly_pnl"]))
        monthly_pnl = Decimal(str(row["monthly_pnl"]))
        daily_limit = Decimal(str(row["daily_loss_limit"]))
        weekly_limit = Decimal(str(row["weekly_loss_limit"]))
        monthly_limit = Decimal(str(row["monthly_loss_limit"]))
        if daily_pnl <= -daily_limit:
            reason = "daily_loss_limit"
        elif weekly_pnl <= -weekly_limit:
            reason = "weekly_loss_limit"
        elif monthly_pnl <= -monthly_limit:
            reason = "monthly_loss_limit"

    result = dict(row)
    if reason in {"daily_loss_limit", "weekly_loss_limit", "monthly_loss_limit"}:
        result["is_trading_allowed"] = False
        disabled = True
    result.update({"passed": reason is None, "reason": reason, "disabled": disabled})
    return result


async def _with_runner(
    *,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    settings: GatewaySettings | None = None,
    kabu: Any | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
    sleep: Callable[[float], Awaitable[None]] | None = None,
    wall_clock: Callable[[], datetime] | None = None,
    order_archive: OrderArchiveWriter | None = None,
) -> Any:
    settings = settings or _settings()

    async def _noop_sleep(_: float) -> None:
        return None

    def _default_wall_clock() -> datetime:
        return datetime(2026, 4, 20, 1, 0, 0, tzinfo=UTC)

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
            kabu=kabu,
            order_archive=order_archive,
            idle_backoff_seconds=1.0,
            sleep=sleep or _noop_sleep,
            wall_clock=wall_clock or _default_wall_clock,
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

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

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
    reserve_requests = [
        req
        for req in supabase.requests
        if req.url.path == "/rest/v1/rpc/gateway_check_and_reserve_risk"
    ]
    assert len(reserve_requests) == 1
    reserve_payload = json.loads(reserve_requests[0].content.decode())
    assert reserve_payload["p_trade_mode"] == "live"
    assert reserve_payload["p_symbol"] == "7203"
    assert reserve_payload["p_side"] == "BUY"
    assert reserve_payload["p_risk_amount"] == "20000"
    assert reserve_payload["p_notional_amount"] == "500000"

    assert json.loads(pubsub.acked[0].content.decode()) == {"ackIds": ["a1"]}


async def test_live_buy_rejected_when_risk_reservation_fails(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [("a1", _unified_payload(action=Action.BUY, price="2500", stop_loss_price="2400"))]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        risk_reservation_rows=[
            {
                "passed": False,
                "reason": "daily_loss_reservation_limit",
                "reserved": False,
                "active_risk_before": "9000",
                "active_risk_after": "9000",
                "daily_pnl": "0",
                "daily_loss_limit": "10000",
                "weekly_pnl": "0",
                "weekly_loss_limit": "30000",
                "monthly_pnl": "0",
                "monthly_loss_limit": "100000",
            }
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
    )

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []
    assert "daily_loss_reservation_limit" in caplog.text


async def test_live_buy_releases_risk_reservation_when_publish_fails() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [("a1", _unified_payload(action=Action.BUY, price="2500", stop_loss_price="2400"))]
            )
        ],
        publish_status=503,
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        with pytest.raises(PubSubError):
            await runner.run_once()

    await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert len(pubsub.published) == 3
    assert len(supabase.released_risk_order_ids) == 1
    release_requests = [
        req
        for req in supabase.requests
        if req.url.path == "/rest/v1/rpc/gateway_release_risk_reservation"
    ]
    assert len(release_requests) == 1
    assert json.loads(release_requests[0].content.decode())["p_reason"] == "publish_failed"


async def test_order_publish_summary_logs_counts(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
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
    )

    async def _body(runner: StreamRunner) -> Any:
        runner.publish_summary_log_interval_seconds = 0.0
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
    )

    assert stats.approved == 1
    summaries = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "order_publish_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0].total == 1
    assert summaries[0].trade_mode_counts == {"live": 1}
    assert summaries[0].side_counts == {"BUY": 1}
    assert summaries[0].destination_topic_counts == {LIVE_TOPIC: 1}


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


async def test_buy_quantity_is_capped_by_thin_daily_liquidity() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            symbol="4346",
                            action=Action.BUY,
                            price="830",
                            stop_loss_price="813.4",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
        daily_ohlcv_rows=[[{"close": "803", "volume": 12600, "turnover": "10117800"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["symbol"] == "4346"
    assert order["quantity"] == 100
    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert len(daily_calls) == 1
    assert daily_calls[0].url.params.get("select") == "close,volume,turnover"


async def test_buy_quantity_is_capped_when_daily_liquidity_is_missing() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            symbol="4346",
                            action=Action.BUY,
                            price="830",
                            stop_loss_price="813.4",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
        daily_ohlcv_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["symbol"] == "4346"
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

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []


async def test_live_buy_uses_kabu_wallet_as_capital() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[
            [{"current_price": "2500"}],
            [],
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        kabu=_FakeKabuWallet([Decimal("500000")]),
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 100


async def test_live_buy_uses_cached_wallet_after_kabu_failure(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
        positions_price_rows=[
            [{"current_price": "2500"}],
            [],
        ],
    )
    caplog.set_level(logging.WARNING, logger="gateway.streaming.runner")

    async def _body(runner: StreamRunner) -> Any:
        runner._cached_live_capital = Decimal("500000")
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        kabu=_FakeKabuWallet([RuntimeError("wallet down")]),
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 100
    assert "using cached live capital" in caplog.text


async def test_paper_mode_publishes_to_paper_orders(tmp_path: Path) -> None:
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

    archive = OrderArchiveWriter(tmp_path / "orders")
    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        order_archive=archive,
        run_body=_body,
    )

    assert stats.approved == 1
    assert pubsub.published[0].url.path.endswith(f"/topics/{PAPER_TOPIC}:publish")
    archive_path = tmp_path / "orders" / "trade_mode=paper" / "date=2026-04-20" / "orders.jsonl"
    rows = archive_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    archived = OrderRequest.model_validate_json(rows[0])
    assert archived.trade_mode is TradeMode.PAPER
    assert archived.symbol == "7203"


async def test_market_regime_risk_off_logs_would_reject_without_blocking(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [("a1", _unified_payload(action=Action.BUY, price="2500", stop_loss_price="2400"))]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        market_regime_rows=[[_market_regime_row(regime="RISK_OFF", buy_enabled=False)]],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"quantity": 100, "current_price": "2500", "entry_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

    assert stats.approved == 1
    assert stats.rejected == 0
    assert len(pubsub.published) == 1
    would_reject = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "market_regime_would_reject"
    ]
    assert len(would_reject) == 1
    record = would_reject[0]
    assert record.reason == "market_regime_risk_off"
    assert record.market_regime == "RISK_OFF"
    assert record.market_regime_buy_enabled is False
    assert record.guard_enabled is False


async def test_market_regime_guard_rejects_buy_when_enabled(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [("a1", _unified_payload(action=Action.BUY, price="2500", stop_loss_price="2400"))]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        market_regime_rows=[[_market_regime_row(regime="CRASH", buy_enabled=False)]],
        positions_quantity_rows=[],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(market_regime_gateway_guard_enabled=True),
        run_body=_body,
    )

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []
    rejected = [
        record for record in caplog.records if getattr(record, "event", None) == "signal_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].reason == "market_regime_risk_off"
    position_reads = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_reads == []


async def test_market_regime_guard_does_not_block_sell() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.SELL))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        market_regime_rows=[[_market_regime_row(regime="CRASH", buy_enabled=False)]],
        positions_quantity_rows=[[{"quantity": 300, "side": "LONG"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(market_regime_gateway_guard_enabled=True),
        run_body=_body,
    )

    assert stats.approved == 1
    assert pubsub.published[0].url.path.endswith(f"/topics/{LIVE_TOPIC}:publish")
    regime_reads = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/market_regime"
    ]
    assert regime_reads == []


async def test_soft_loss_throttle_logs_rule_buy_would_reject_without_blocking(
    caplog: Any,
) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            signal_source=SignalSource.RULE,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(
                trade_mode="live",
                daily_pnl="-25000",
                daily_loss_limit="100000",
            )
        ],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"quantity": 100, "current_price": "2500", "entry_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

    assert stats.approved == 1
    assert stats.rejected == 0
    assert len(pubsub.published) == 1
    would_reject = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "soft_loss_throttle_would_reject"
    ]
    assert len(would_reject) == 1
    record = would_reject[0]
    assert record.reason == "soft_loss_rule_only_buy"
    assert record.daily_pnl == -25000.0
    assert record.soft_loss_limit_jpy == 20000.0
    assert record.guard_enabled is False


async def test_soft_loss_throttle_guard_rejects_rule_buy_when_enabled(
    caplog: Any,
) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            signal_source=SignalSource.RULE,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(
                trade_mode="live",
                daily_pnl="-25000",
                daily_loss_limit="100000",
            )
        ],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(soft_loss_throttle_guard_enabled=True),
        run_body=_body,
    )

    assert stats.approved == 0
    assert stats.rejected == 1
    assert pubsub.published == []
    rejected = [
        record for record in caplog.records if getattr(record, "event", None) == "signal_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].reason == "soft_loss_rule_only_buy"
    position_reads = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_reads == []


async def test_soft_loss_throttle_does_not_block_consensus_buy() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            signal_source=SignalSource.CONSENSUS,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(
                trade_mode="live",
                daily_pnl="-25000",
                daily_loss_limit="100000",
            )
        ],
        positions_quantity_rows=[[]],
        positions_price_rows=[[{"quantity": 100, "current_price": "2500", "entry_price": "2500"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(soft_loss_throttle_guard_enabled=True),
        run_body=_body,
    )

    assert stats.approved == 1
    assert len(pubsub.published) == 1


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


async def test_reject_summary_logs_reason_counts(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.SELL))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        runner.reject_summary_log_interval_seconds = 0.0
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.rejected == 1
    summaries = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "signal_reject_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0].total == 1
    assert summaries[0].reason_counts == {"no_position_for_sell": 1}


async def test_signal_rejected_log_has_daily_analysis_fields(caplog: Any) -> None:
    signal_id = uuid4()
    strategy_signal_id_a = uuid4()
    strategy_signal_id_b = uuid4()
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.SELL,
                            signal_id=signal_id,
                            signal_source=SignalSource.CONSENSUS,
                            strategy_signal_id_a=strategy_signal_id_a,
                            strategy_signal_id_b=strategy_signal_id_b,
                            confidence=0.73,
                            created_at="2026-04-20T00:30:00+00:00",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
    )

    def _now() -> datetime:
        return datetime(2026, 4, 20, 0, 30, 3, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, wall_clock=_now, run_body=_body)

    assert stats.rejected == 1
    rejected = [
        record for record in caplog.records if getattr(record, "event", None) == "signal_rejected"
    ]
    assert len(rejected) == 1
    record = rejected[0]
    assert record.signal_id == str(signal_id)
    assert record.source == "CONSENSUS"
    assert record.reason == "no_position_for_sell"
    assert record.action == "SELL"
    assert record.symbol == "7203"
    assert record.confidence == 0.73
    assert record.signal_created_at == "2026-04-20T00:30:00+00:00"
    assert record.age_seconds == 3.0
    assert record.strategy_signal_id_a == str(strategy_signal_id_a)
    assert record.strategy_signal_id_b == str(strategy_signal_id_b)


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


async def test_live_buy_uses_signal_price_without_position_price_lookup() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [("a1", _unified_payload(action=Action.BUY, price="2500", stop_loss_price="2400"))]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live")],
        positions_quantity_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

    assert stats.approved == 1
    assert stats.rejected == 0
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 200

    position_price_calls = [
        r
        for r in supabase.requests
        if r.method == "GET"
        and r.url.path == "/rest/v1/positions"
        and (r.url.params.get("select") or "") == "current_price"
    ]
    assert position_price_calls == []


async def test_live_duplicate_buy_is_rejected_while_order_pending(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            symbol="6840",
                            action=Action.BUY,
                            price="1043",
                            stop_loss_price="1000",
                        ),
                    ),
                    (
                        "a2",
                        _unified_payload(
                            symbol="6840",
                            action=Action.BUY,
                            price="1043",
                            stop_loss_price="1000",
                        ),
                    ),
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(trade_mode="live"),
            _system_status_row(trade_mode="live"),
        ],
        positions_quantity_rows=[[]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    assert stats.rejected == 1
    assert len(pubsub.published) == 1
    rejected = [
        record for record in caplog.records if getattr(record, "event", None) == "signal_rejected"
    ]
    assert [record.reason for record in rejected] == ["pending_live_order"]


async def test_live_duplicate_sell_is_rejected_while_order_pending(caplog: Any) -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    ("a1", _unified_payload(symbol="186A", action=Action.SELL)),
                    ("a2", _unified_payload(symbol="186A", action=Action.SELL)),
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[
            _system_status_row(trade_mode="live"),
            _system_status_row(trade_mode="live"),
        ],
        positions_quantity_rows=[[{"quantity": 200, "side": "LONG"}]],
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="gateway.streaming.runner")

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.approved == 1
    assert stats.rejected == 1
    assert len(pubsub.published) == 1
    rejected = [
        record for record in caplog.records if getattr(record, "event", None) == "signal_rejected"
    ]
    assert [record.reason for record in rejected] == ["pending_live_order"]


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

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

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

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=_settings(liquidity_sizing_enabled=False),
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["quantity"] == 200  # 2500 - 2400 = 100/share, 20_000/100 = 200
    daily_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/daily_ohlcv"
    ]
    assert daily_calls == []


async def test_live_day_signal_is_rejected_after_market_close() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
    )

    def _after_close() -> datetime:
        return datetime(2026, 4, 20, 5, 50, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_after_close,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    position_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_calls == []


async def test_live_day_buy_is_rejected_after_new_buy_cutoff() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
    )

    def _at_cutoff() -> datetime:
        return datetime(2026, 4, 20, 5, 30, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_at_cutoff,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_paper_day_buy_is_rejected_after_new_buy_cutoff() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper", trading_style="day")],
        positions_quantity_rows=[[]],
    )

    def _at_cutoff() -> datetime:
        return datetime(2026, 4, 20, 5, 30, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_at_cutoff,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_live_day_buy_is_rejected_before_new_buy_start() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
    )

    def _before_start() -> datetime:
        return datetime(2026, 4, 20, 0, 14, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_before_start,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_paper_day_buy_is_rejected_before_new_buy_start() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper", trading_style="day")],
    )

    def _before_start() -> datetime:
        return datetime(2026, 4, 20, 0, 14, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_before_start,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_live_day_buy_is_allowed_at_new_buy_start() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
        positions_quantity_rows=[[]],
    )

    def _at_start() -> datetime:
        return datetime(2026, 4, 20, 0, 15, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_at_start,
        run_body=_body,
    )

    assert stats.approved == 1
    assert stats.rejected == 0
    assert len(pubsub.published) == 1


async def test_live_day_sell_is_not_blocked_before_new_buy_start() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[_pull_response([("a1", _unified_payload(action=Action.SELL))])]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
        positions_quantity_rows=[[{"quantity": 300, "side": "LONG"}]],
    )

    def _before_start() -> datetime:
        return datetime(2026, 4, 20, 0, 4, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_before_start,
        run_body=_body,
    )

    assert stats.approved == 1
    body = json.loads(pubsub.published[0].content.decode())
    order = json.loads(base64.b64decode(body["messages"][0]["data"]).decode())
    assert order["side"] == "SELL"
    assert order["quantity"] == 300


async def test_live_day_buy_is_rejected_after_same_day_sell() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="live", trading_style="day")],
        trades_live_rows=[[{"trade_id": "t1"}]],
    )

    def _before_cutoff() -> datetime:
        return datetime(2026, 4, 20, 1, 0, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_before_cutoff,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    trades_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/trades_live"
    ]
    assert len(trades_calls) == 1
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_paper_day_buy_is_rejected_after_same_day_sell() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response([("a1", _unified_payload(action=Action.BUY, stop_loss_price="2400"))])
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper", trading_style="day")],
        trades_paper_rows=[[{"trade_id": "t1"}]],
    )

    def _before_cutoff() -> datetime:
        return datetime(2026, 4, 20, 1, 0, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_before_cutoff,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    paper_trades_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/trades_paper"
    ]
    assert len(paper_trades_calls) == 1
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/positions"] == []


async def test_paper_day_reentry_block_can_be_disabled() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper", trading_style="day")],
        positions_quantity_rows=[[]],
        trades_paper_rows=[[{"trade_id": "t1"}]],
    )
    settings = _settings(day_same_symbol_reentry_block_enabled=False)

    def _before_cutoff() -> datetime:
        return datetime(2026, 4, 20, 1, 0, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=settings,
        wall_clock=_before_cutoff,
        run_body=_body,
    )

    assert stats.approved == 1
    assert stats.rejected == 0
    assert len(pubsub.published) == 1
    assert [r for r in supabase.requests if r.url.path == "/rest/v1/trades_paper"] == []


async def test_live_stale_signal_is_rejected_before_position_reads() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                            created_at="2026-04-20T00:00:00+00:00",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(system_status_rows=[_system_status_row(trade_mode="live")])
    settings = _settings(live_signal_max_age_seconds=60)

    def _wall_clock() -> datetime:
        return datetime(2026, 4, 20, 1, 10, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=settings,
        run_body=_body,
        wall_clock=_wall_clock,
    )

    assert stats.rejected == 1
    assert stats.approved == 0
    assert pubsub.published == []
    position_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_calls == []


async def test_paper_stale_signal_is_rejected_before_position_reads() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                            created_at="2026-04-20T00:00:00+00:00",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
    )
    settings = _settings(live_signal_max_age_seconds=60)

    def _wall_clock() -> datetime:
        return datetime(2026, 4, 20, 1, 10, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=settings,
        run_body=_body,
        wall_clock=_wall_clock,
    )

    assert stats.rejected == 1
    assert pubsub.published == []
    position_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_calls == []


async def test_live_day_buy_after_close_is_rejected_before_position_reads() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(system_status_rows=[_system_status_row(trade_mode="live")])

    def _after_close() -> datetime:
        return datetime(2026, 4, 20, 6, 0, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_after_close,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert stats.approved == 0
    assert pubsub.published == []
    position_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_calls == []


async def test_paper_day_buy_after_close_is_rejected_by_session_guard() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _pull_response(
                [
                    (
                        "a1",
                        _unified_payload(
                            action=Action.BUY,
                            price="2500",
                            stop_loss_price="2400",
                        ),
                    )
                ]
            )
        ]
    )
    supabase = _SupabaseRouter(
        system_status_rows=[_system_status_row(trade_mode="paper")],
        positions_quantity_rows=[[]],
    )

    def _after_close() -> datetime:
        return datetime(2026, 4, 20, 6, 0, 0, tzinfo=UTC)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        wall_clock=_after_close,
        run_body=_body,
    )

    assert stats.rejected == 1
    assert stats.approved == 0
    assert pubsub.published == []
    position_calls = [
        r for r in supabase.requests if r.method == "GET" and r.url.path == "/rest/v1/positions"
    ]
    assert position_calls == []


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
    assert patch_calls == []
    rpc_calls = [
        r for r in supabase.requests if r.url.path == "/rest/v1/rpc/gateway_check_kill_switch"
    ]
    assert len(rpc_calls) == 1
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
