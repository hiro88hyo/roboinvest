"""Gateway StreamRunner end-to-end 統合テスト。

実際の Pub/Sub エミュレータと Supabase PostgREST に対して:
- `system_status` シングルトン行をテスト値で上書き (test 後に復元)
- `positions` に擬似ポジション / 価格を挿入 (test 後に削除)
- `trade-signals` に UnifiedTradeSignal を publish
- StreamRunner を 1 回転させ、ルーティング先トピック (live / paper) を
  サブスクライブして OrderRequest が届くことを確認
- キルスイッチ発動ケースでは `system_status.is_trading_allowed = false`
  が UPDATE されることを確認
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from gateway.clients.pubsub import PubSubPublisher, PubSubSubscriber
from gateway.clients.supabase import SupabaseClient
from gateway.config import GatewaySettings, RiskConfig
from gateway.router import TopicRouting
from gateway.streaming.runner import StreamRunner
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.order import OrderRequest
from trade_contracts.signal import UnifiedTradeSignal

pytestmark = pytest.mark.integration


TRADE_SIGNALS_TOPIC = "trade-signals"
LIVE_ORDERS_TOPIC = "live-orders"
PAPER_ORDERS_TOPIC = "paper-orders"
TEST_PUBSUB_TIMEOUT_SECONDS = 2.0


async def _ensure_subscription(
    admin: httpx.AsyncClient, project: str, sub: str, topic: str
) -> None:
    resp = await admin.put(
        f"/v1/projects/{project}/subscriptions/{sub}",
        json={"topic": f"projects/{project}/topics/{topic}", "ackDeadlineSeconds": 30},
    )
    if resp.status_code not in (200, 409):
        raise RuntimeError(
            f"failed to create subscription {sub}: {resp.status_code} {resp.text[:200]}"
        )


async def _delete_subscription(admin: httpx.AsyncClient, project: str, sub: str) -> None:
    await admin.delete(f"/v1/projects/{project}/subscriptions/{sub}")


def _supabase_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _read_system_status_row(*, url: str, key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.get(
            "/rest/v1/system_status",
            params={"select": "*", "id": "eq.1", "limit": "1"},
            headers=_supabase_headers(key),
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise RuntimeError("system_status row (id=1) missing — seed it before running")
        row: dict[str, Any] = rows[0]
        return row


async def _write_system_status_row(*, url: str, key: str, row: dict[str, Any]) -> None:
    headers = {**_supabase_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/system_status", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(
                f"failed to upsert system_status: {resp.status_code} {resp.text[:200]}"
            )


async def _insert_position(
    *,
    url: str,
    key: str,
    symbol: str,
    trade_type: str,
    quantity: int,
    entry_price: str,
    current_price: str,
    holding_type: str = "day",
) -> None:
    row = {
        "symbol": symbol,
        "trade_type": trade_type,
        "side": "LONG",
        "quantity": quantity,
        "entry_price": entry_price,
        "current_price": current_price,
        "unrealized_pnl": "0",
        "holding_type": holding_type,
    }
    headers = {**_supabase_headers(key), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/positions", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(
                f"failed to insert positions row: {resp.status_code} {resp.text[:200]}"
            )


async def _delete_position(*, url: str, key: str, symbol: str) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )


async def _insert_daily_ohlcv(
    *,
    url: str,
    key: str,
    symbol: str,
    date: str,
    close: str,
) -> None:
    row = {
        "symbol": symbol,
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
        "turnover": "1000000000",
    }
    headers = {**_supabase_headers(key), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/daily_ohlcv", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(
                f"failed to insert daily_ohlcv row: {resp.status_code} {resp.text[:200]}"
            )


async def _delete_daily_ohlcv(*, url: str, key: str, symbol: str) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/daily_ohlcv",
            params={"symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )


@pytest.fixture
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    unique_resources: dict[str, str],
) -> AsyncIterator[dict[str, str]]:
    trade_sub = unique_resources["trade_signals_sub"]
    live_sub = unique_resources["live_orders_sub"]
    paper_sub = unique_resources["paper_orders_sub"]
    await _ensure_subscription(pubsub_admin, pubsub_project_id, trade_sub, TRADE_SIGNALS_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, live_sub, LIVE_ORDERS_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, paper_sub, PAPER_ORDERS_TOPIC)
    try:
        yield unique_resources
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, trade_sub)
        await _delete_subscription(pubsub_admin, pubsub_project_id, live_sub)
        await _delete_subscription(pubsub_admin, pubsub_project_id, paper_sub)


@pytest.fixture
async def system_status_sandbox(
    supabase_url: str,
    supabase_secret_key: str,
) -> AsyncIterator[None]:
    """テスト中に system_status を書き換える場合の復元サンドボックス。"""
    original = await _read_system_status_row(url=supabase_url, key=supabase_secret_key)
    try:
        yield
    finally:
        await _write_system_status_row(url=supabase_url, key=supabase_secret_key, row=original)


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    trade_signals_sub: str,
) -> GatewaySettings:
    return GatewaySettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_trade_signals=trade_signals_sub,
        pubsub_topic_live_orders=LIVE_ORDERS_TOPIC,
        pubsub_topic_paper_orders=PAPER_ORDERS_TOPIC,
        pubsub_pull_max_messages=10,
        capital=Decimal("1000000"),
        live_day_new_buy_start_time="00:00",
        live_day_new_buy_cutoff_time="23:59",
        day_closeout_time="23:59",
    )


def _unified_signal(
    *,
    symbol: str,
    action: Action = Action.BUY,
    stop_loss_price: Decimal | None = Decimal("2400"),
    holding_type: TradingStyle = TradingStyle.DAY,
    signal_id: UUID | None = None,
) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        signal_id=signal_id or uuid4(),
        symbol=symbol,
        action=action,
        confidence=0.8,
        signal_source=SignalSource.CONSENSUS,
        holding_type=holding_type,
        stop_loss_price=stop_loss_price,
        created_at=datetime.now(UTC),
    )


async def _run_one_iteration(
    *,
    settings: GatewaySettings,
) -> None:
    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as subscriber,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as publisher,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            supabase=supabase,
            settings=settings,
            risk_config=RiskConfig.from_settings(settings),
            routing=TopicRouting(
                live_topic=settings.pubsub_topic_live_orders,
                paper_topic=settings.pubsub_topic_paper_orders,
            ),
            idle_backoff_seconds=0.0,
        )
        await runner.run(iterations=1)


async def test_paper_mode_buy_signal_routes_to_paper_orders(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
    system_status_sandbox: None,
) -> None:
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        trade_signals_sub=provisioned_subs["trade_signals_sub"],
    )

    await _write_system_status_row(
        url=supabase_url,
        key=supabase_secret_key,
        row={
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
        },
    )

    # Seed a live-trade-type position for the same symbol so positions.current_price
    # supplies entry_price (price lookup is trade_type-agnostic). No paper-type position
    # exists, so the runner will treat this as a fresh BUY.
    await _insert_position(
        url=supabase_url,
        key=supabase_secret_key,
        symbol=test_symbol,
        trade_type="live",
        quantity=100,
        entry_price="2500",
        current_price="2500",
    )

    signal = _unified_signal(symbol=test_symbol)
    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            TRADE_SIGNALS_TOPIC,
            data=signal.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol},
        )

    try:
        await _run_one_iteration(settings=settings)

        async with PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as subscriber:
            received = await subscriber.pull(
                provisioned_subs["paper_orders_sub"], max_messages=5, return_immediately=True
            )
            assert received, "no OrderRequest published to paper-orders"
            order = OrderRequest.model_validate_json(received[0].data)
            assert order.symbol == test_symbol
            assert order.unified_signal_id == signal.signal_id
            assert order.trade_mode is TradeMode.PAPER
            assert order.side.value == "BUY"
            assert order.quantity >= 100
            await subscriber.acknowledge(
                provisioned_subs["paper_orders_sub"], [m.ack_id for m in received]
            )

            # live-orders must not receive the BUY
            live_msgs = await subscriber.pull(
                provisioned_subs["live_orders_sub"], max_messages=5, return_immediately=True
            )
            assert live_msgs == []
    finally:
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)


async def test_daily_loss_limit_flips_is_trading_allowed(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
    system_status_sandbox: None,
) -> None:
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        trade_signals_sub=provisioned_subs["trade_signals_sub"],
    )

    await _write_system_status_row(
        url=supabase_url,
        key=supabase_secret_key,
        row={
            "id": 1,
            "is_trading_allowed": True,
            "trade_mode": "live",
            "trading_style": "day",
            "daily_pnl": "-10000",
            "weekly_pnl": "0",
            "monthly_pnl": "0",
            "daily_loss_limit": "10000",
            "weekly_loss_limit": "30000",
            "monthly_loss_limit": "100000",
        },
    )

    signal = _unified_signal(symbol=test_symbol)
    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            TRADE_SIGNALS_TOPIC,
            data=signal.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol},
        )

    await _run_one_iteration(settings=settings)

    row = await _read_system_status_row(url=supabase_url, key=supabase_secret_key)
    assert row["is_trading_allowed"] is False

    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
    ) as subscriber:
        live_msgs = await subscriber.pull(
            provisioned_subs["live_orders_sub"], max_messages=5, return_immediately=True
        )
        paper_msgs = await subscriber.pull(
            provisioned_subs["paper_orders_sub"], max_messages=5, return_immediately=True
        )
    assert live_msgs == []
    assert paper_msgs == []


async def test_paper_buy_falls_back_to_daily_ohlcv_when_no_position(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
    system_status_sandbox: None,
) -> None:
    """paper モードで positions に行がなくても daily_ohlcv の close を
    エントリープライスに使って BUY が approve されることを確認する。"""
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        trade_signals_sub=provisioned_subs["trade_signals_sub"],
    )

    await _write_system_status_row(
        url=supabase_url,
        key=supabase_secret_key,
        row={
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
        },
    )

    today = datetime.now(UTC).date().isoformat()
    await _insert_daily_ohlcv(
        url=supabase_url,
        key=supabase_secret_key,
        symbol=test_symbol,
        date=today,
        close="2500",
    )

    signal = _unified_signal(symbol=test_symbol, stop_loss_price=Decimal("2400"))
    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            TRADE_SIGNALS_TOPIC,
            data=signal.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol},
        )

    try:
        await _run_one_iteration(settings=settings)

        async with PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as subscriber:
            received = await subscriber.pull(
                provisioned_subs["paper_orders_sub"], max_messages=5, return_immediately=True
            )
            assert received, "no OrderRequest published to paper-orders"
            order = OrderRequest.model_validate_json(received[0].data)
            assert order.symbol == test_symbol
            assert order.unified_signal_id == signal.signal_id
            assert order.trade_mode is TradeMode.PAPER
            assert order.side.value == "BUY"
            # entry=2500 (daily_ohlcv) stop=2400 → risk/share=100, qty=200
            assert order.quantity == 200
            await subscriber.acknowledge(
                provisioned_subs["paper_orders_sub"], [m.ack_id for m in received]
            )

            live_msgs = await subscriber.pull(
                provisioned_subs["live_orders_sub"], max_messages=5, return_immediately=True
            )
            assert live_msgs == []
    finally:
        await _delete_daily_ohlcv(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
