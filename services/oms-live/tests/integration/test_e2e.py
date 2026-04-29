"""OMS Live StreamRunner end-to-end 統合テスト。

実 Pub/Sub エミュレータ + Supabase ローカルに対し:
- live-orders トピックと subscription を作成
- live-orders に注文を publish
- KabuLiveClient は ``httpx.MockTransport`` でスタブ (市場時間外でも実走可能にするため)
- StreamRunner.run_once() を回す
- positions(trade_type=live) と trades_live、必要なら system_status.daily_pnl の
  行が期待通りに作成・更新されていることを確認

oms-paper の e2e と同じく ``conftest.py`` を持たない (mypy duplicate-module 回避)。
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from oms_live.clients.pubsub import PubSubSubscriber
from oms_live.clients.supabase import SupabaseClient
from oms_live.config import OmsLiveSettings
from oms_live.kabu_client import KabuLiveClient
from oms_live.streaming.runner import StreamRunner
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode
from trade_contracts.order import OrderRequest

pytestmark = pytest.mark.integration


LIVE_ORDERS_TOPIC = "live-orders"


# --- env / health fixtures --------------------------------------------------


@pytest.fixture(scope="session")
def pubsub_emulator_host() -> str:
    host = os.environ.get("PUBSUB_EMULATOR_HOST")
    if not host:
        pytest.skip("PUBSUB_EMULATOR_HOST not set")
    try:
        resp = httpx.get(f"http://{host}/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"pubsub emulator unreachable at {host}: {exc}")
    if resp.status_code >= 500:
        pytest.skip(f"pubsub emulator unhealthy: status={resp.status_code}")
    return host


@pytest.fixture(scope="session")
def pubsub_project_id() -> str:
    project = os.environ.get("PUBSUB_PROJECT_ID")
    if not project:
        pytest.skip("PUBSUB_PROJECT_ID not set")
    return project


@pytest.fixture(scope="session")
def supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        pytest.skip("SUPABASE_URL not set")
    try:
        resp = httpx.get(f"{url.rstrip('/')}/rest/v1/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"supabase unreachable at {url}: {exc}")
    if resp.status_code >= 500:
        pytest.skip(f"supabase unhealthy: status={resp.status_code}")
    return url


@pytest.fixture(scope="session")
def supabase_secret_key() -> str:
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not key:
        pytest.skip("SUPABASE_SECRET_KEY not set")
    return key


@pytest.fixture
def run_id() -> str:
    return uuid4().hex[:8]


@pytest.fixture
def test_symbol(run_id: str) -> str:
    return f"IT{run_id[:6].upper()}"


@pytest.fixture
async def pubsub_admin(pubsub_emulator_host: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=f"http://{pubsub_emulator_host}",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


# --- Pub/Sub helpers --------------------------------------------------------


async def _ensure_topic(admin: httpx.AsyncClient, project: str, topic: str) -> None:
    resp = await admin.put(f"/v1/projects/{project}/topics/{topic}")
    if resp.status_code not in (200, 409):
        raise RuntimeError(f"failed to create topic {topic}: {resp.status_code} {resp.text[:200]}")


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


@pytest.fixture
def unique_resources(run_id: str) -> Iterator[dict[str, str]]:
    yield {
        "live_orders_sub": f"it-omsl-live-orders-{run_id}",
    }


@pytest.fixture
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    unique_resources: dict[str, str],
) -> AsyncIterator[dict[str, str]]:
    sub = unique_resources["live_orders_sub"]
    await _ensure_topic(pubsub_admin, pubsub_project_id, LIVE_ORDERS_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, sub, LIVE_ORDERS_TOPIC)
    try:
        yield unique_resources
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, sub)


async def _publish(
    *,
    project: str,
    emulator_host: str,
    topic: str,
    data: bytes,
) -> None:
    async with httpx.AsyncClient(
        base_url=f"http://{emulator_host}",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        resp = await client.post(
            f"/v1/projects/{project}/topics/{topic}:publish",
            json={"messages": [{"data": base64.b64encode(data).decode("ascii")}]},
        )
        if resp.status_code >= 300:
            raise RuntimeError(
                f"publish failed: topic={topic} status={resp.status_code} body={resp.text[:200]}"
            )


# --- Supabase helpers -------------------------------------------------------


def _supabase_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _seed_aggregator_log(
    *,
    url: str,
    key: str,
    signal_id: UUID,
    symbol: str,
    action: str = "BUY",
) -> None:
    """trades_live.unified_signal_id の FK 制約を満たすため、上流 aggregator_logs 行を seed。"""
    row = {
        "signal_id": str(signal_id),
        "symbol": symbol,
        "action": action,
        "confidence": 0.8,
        "signal_source": "CONSENSUS",
    }
    headers = {**_supabase_headers(key), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/aggregator_logs", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(
                f"failed to seed aggregator_logs: {resp.status_code} {resp.text[:200]}"
            )


async def _delete_aggregator_log(*, url: str, key: str, signal_id: UUID) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/aggregator_logs",
            params={"signal_id": f"eq.{signal_id}"},
            headers=_supabase_headers(key),
        )


async def _delete_position(*, url: str, key: str, symbol: str) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/positions",
            params={"symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )


async def _delete_trades(*, url: str, key: str, symbol: str) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/trades_live",
            params={"symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )


async def _read_live_position(*, url: str, key: str, symbol: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.get(
            "/rest/v1/positions",
            params={
                "select": "*",
                "symbol": f"eq.{symbol}",
                "trade_type": "eq.live",
                "limit": "1",
            },
            headers=_supabase_headers(key),
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None


async def _read_trades(*, url: str, key: str, symbol: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.get(
            "/rest/v1/trades_live",
            params={
                "select": "*",
                "symbol": f"eq.{symbol}",
                "order": "executed_at.asc",
            },
            headers=_supabase_headers(key),
        )
        resp.raise_for_status()
        rows = resp.json()
        return list(rows)


# --- builders ---------------------------------------------------------------


def _make_order(
    *,
    symbol: str,
    side: Side,
    quantity: int,
    signal_id: UUID | None = None,
) -> OrderRequest:
    return OrderRequest(
        unified_signal_id=signal_id or uuid4(),
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        trade_mode=TradeMode.LIVE,
        signal_source=SignalSource.CONSENSUS,
        created_at=datetime.now(UTC),
    )


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    live_orders_sub: str,
) -> OmsLiveSettings:
    return OmsLiveSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_live_orders=live_orders_sub,
        pubsub_pull_max_messages=10,
        kabu_api_password="api-pw",
        kabu_order_password="order-pw",
        kabu_default_exchange=1,
        kabu_account_type=4,
        order_fill_poll_interval_seconds=0.0,
        order_fill_timeout_seconds=5.0,
    )


# --- kabu mock --------------------------------------------------------------


class _KabuMock:
    """単純な kabu API スタブ。/sendorder で発行した OrderId を /orders で返す。"""

    def __init__(self, *, fill_price: int, side: str, quantity: int) -> None:
        self._fill_price = fill_price
        self._side = side
        self._quantity = quantity
        self._counter = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/token"):
            return httpx.Response(200, json={"Token": "tok-1"})
        if path.endswith("/sendorder"):
            self._counter += 1
            return httpx.Response(200, json={"Result": 0, "OrderId": f"OID-{self._counter}"})
        if request.method == "GET" and path.endswith("/orders"):
            order_id = request.url.params.get("id", "")
            return httpx.Response(
                200,
                json=[
                    {
                        "ID": order_id,
                        "Symbol": "X",
                        "Side": self._side,
                        "OrderQty": self._quantity,
                        "CumQty": self._quantity,
                        "State": 3,
                        "OrderState": 3,
                        "Price": 0,
                        "Details": [
                            {
                                "ExecutionID": f"E-{order_id}",
                                "ExecutionTime": "2026-04-29T09:00:01+09:00",
                                "Price": self._fill_price,
                                "Qty": self._quantity,
                            }
                        ],
                    }
                ],
            )
        return httpx.Response(404, text=f"unmocked {request.method} {path}")


async def _drain_until_filled(
    *,
    settings: OmsLiveSettings,
    kabu_handler: _KabuMock,
    max_iterations: int = 10,
) -> None:
    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
        ) as subscriber,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
        KabuLiveClient(
            base_url=settings.kabu_api_base_url,
            api_password=settings.kabu_api_password,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(kabu_handler)),
        ) as kabu,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supabase,
            kabu=kabu,
            settings=settings,
            idle_backoff_seconds=0.0,
        )
        for _ in range(max_iterations):
            stats = await runner.run_once()
            if stats.filled > 0:
                return


# --- tests -----------------------------------------------------------------


async def test_buy_order_creates_live_position_and_trade_row(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    signal_id = uuid4()
    order = _make_order(symbol=test_symbol, side=Side.BUY, quantity=100, signal_id=signal_id)

    await _seed_aggregator_log(
        url=supabase_url, key=supabase_secret_key, signal_id=signal_id, symbol=test_symbol
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=LIVE_ORDERS_TOPIC,
        data=order.model_dump_json().encode("utf-8"),
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        live_orders_sub=provisioned_subs["live_orders_sub"],
    )

    try:
        await _drain_until_filled(
            settings=settings,
            kabu_handler=_KabuMock(fill_price=1000, side="2", quantity=100),
        )

        pos = await _read_live_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos is not None
        assert pos["quantity"] == 100
        assert Decimal(str(pos["entry_price"])) == Decimal("1000")
        assert pos["trade_type"] == "live"

        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        assert len(trades) == 1
        assert trades[0]["side"] == "BUY"
        assert trades[0]["quantity"] == 100
        assert Decimal(str(trades[0]["price"])) == Decimal("1000")
        assert trades[0]["unified_signal_id"] == str(order.unified_signal_id)
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_aggregator_log(url=supabase_url, key=supabase_secret_key, signal_id=signal_id)


async def test_sell_full_close_records_realized_pnl_and_deletes_position(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    buy_signal_id = uuid4()
    sell_signal_id = uuid4()

    await _seed_aggregator_log(
        url=supabase_url, key=supabase_secret_key, signal_id=buy_signal_id, symbol=test_symbol
    )
    await _seed_aggregator_log(
        url=supabase_url,
        key=supabase_secret_key,
        signal_id=sell_signal_id,
        symbol=test_symbol,
        action="SELL",
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        live_orders_sub=provisioned_subs["live_orders_sub"],
    )

    try:
        # 1) BUY を入れて position を作る
        buy = _make_order(symbol=test_symbol, side=Side.BUY, quantity=100, signal_id=buy_signal_id)
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=LIVE_ORDERS_TOPIC,
            data=buy.model_dump_json().encode("utf-8"),
        )
        await _drain_until_filled(
            settings=settings,
            kabu_handler=_KabuMock(fill_price=1000, side="2", quantity=100),
        )

        # 2) SELL で全決済する
        sell = _make_order(
            symbol=test_symbol, side=Side.SELL, quantity=100, signal_id=sell_signal_id
        )
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=LIVE_ORDERS_TOPIC,
            data=sell.model_dump_json().encode("utf-8"),
        )
        await _drain_until_filled(
            settings=settings,
            kabu_handler=_KabuMock(fill_price=1100, side="1", quantity=100),
        )

        pos = await _read_live_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos is None  # 全決済で row 削除

        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        sides = sorted(t["side"] for t in trades)
        assert sides == ["BUY", "SELL"]
        sell_row = next(t for t in trades if t["side"] == "SELL")
        assert Decimal(str(sell_row["price"])) == Decimal("1100")
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=buy_signal_id
        )
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=sell_signal_id
        )
