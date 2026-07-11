"""OMS Paper StreamRunner end-to-end 統合テスト。

実 Pub/Sub エミュレータ + Supabase ローカルに対し:
- paper-orders と raw-market-data に topic / subscription を作成
- 板スナップショットと注文を publish
- StreamRunner.run(iterations=1) を回す
- positions(trade_type=paper) と trades_paper の行が期待通りに作成・更新されていることを確認

fixture は本ファイルにインラインで定義する。新サービス追加時に
``tests/conftest.py`` を増やすと mypy の duplicate-module で詰まるため
(``feedback_mypy_test_layout`` の制約を参照)。
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from oms_paper.clients.pubsub import PubSubSubscriber
from oms_paper.clients.supabase import SupabaseClient, SupabaseError
from oms_paper.config import OmsPaperSettings
from oms_paper.models import (
    PaperFillOutcome,
    PaperFillReason,
    PaperFillRecord,
    PaperPositionAction,
    PaperStopUpdateOutcome,
    PaperStopUpdateReason,
)
from oms_paper.streaming.runner import StreamRunner
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle
from trade_contracts.market import OrderBookSnapshot, PriceLevel
from trade_contracts.order import OrderRequest

pytestmark = pytest.mark.integration


PAPER_ORDERS_TOPIC = "paper-orders"
RAW_MARKET_DATA_TOPIC = "raw-market-data"
TEST_PUBSUB_TIMEOUT_SECONDS = 2.0


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


@pytest.fixture(scope="session")
def supabase_anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not key:
        pytest.skip("SUPABASE_ANON_KEY not set")
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
        "paper_orders_sub": f"it-omsp-paper-orders-{run_id}",
        "raw_market_data_sub": f"it-omsp-raw-market-data-{run_id}",
    }


@pytest.fixture
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    unique_resources: dict[str, str],
) -> AsyncIterator[dict[str, str]]:
    paper_sub = unique_resources["paper_orders_sub"]
    raw_sub = unique_resources["raw_market_data_sub"]
    await _ensure_topic(pubsub_admin, pubsub_project_id, PAPER_ORDERS_TOPIC)
    await _ensure_topic(pubsub_admin, pubsub_project_id, RAW_MARKET_DATA_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, paper_sub, PAPER_ORDERS_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, raw_sub, RAW_MARKET_DATA_TOPIC)
    try:
        yield unique_resources
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, paper_sub)
        await _delete_subscription(pubsub_admin, pubsub_project_id, raw_sub)


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
    """trades_paper.unified_signal_id の FK 制約を満たすため、上流 aggregator_logs 行を seed。"""
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
            "/rest/v1/trades_paper",
            params={"symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )


async def _insert_paper_position(
    *,
    url: str,
    key: str,
    symbol: str,
    quantity: int,
    entry_price: Decimal,
    holding_type: str = "swing",
    stop_loss_price: Decimal | None = None,
    target_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    max_hold_days: int | None = None,
    opened_at: datetime | None = None,
) -> None:
    """swing position を Supabase に直接 INSERT する (Phase 4 e2e seed 用)。"""
    row: dict[str, Any] = {
        "symbol": symbol,
        "trade_type": "paper",
        "side": "LONG",
        "quantity": quantity,
        "entry_price": str(entry_price),
        "current_price": str(entry_price),
        "unrealized_pnl": "0",
        "holding_type": holding_type,
        "opened_at": (opened_at or datetime.now(UTC)).isoformat(),
    }
    if stop_loss_price is not None:
        row["stop_loss_price"] = str(stop_loss_price)
    if target_price is not None:
        row["target_price"] = str(target_price)
    if trailing_stop_pct is not None:
        row["trailing_stop_pct"] = str(trailing_stop_pct)
    if max_hold_days is not None:
        row["max_hold_days"] = max_hold_days
    headers = {**_supabase_headers(key), "Prefer": "return=minimal"}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/positions", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(f"failed to seed positions: {resp.status_code} {resp.text[:200]}")


async def _read_paper_position(*, url: str, key: str, symbol: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        resp = await client.get(
            "/rest/v1/positions",
            params={
                "select": "*",
                "symbol": f"eq.{symbol}",
                "trade_type": "eq.paper",
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
            "/rest/v1/trades_paper",
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


def _make_book(symbol: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=datetime.now(UTC),
        bids=[PriceLevel(price=Decimal("999"), quantity=200)],
        asks=[PriceLevel(price=Decimal("1000"), quantity=200)],
    )


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
        trade_mode=TradeMode.PAPER,
        signal_source=SignalSource.CONSENSUS,
        created_at=datetime.now(UTC),
    )


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    paper_orders_sub: str,
    raw_market_data_sub: str,
) -> OmsPaperSettings:
    return OmsPaperSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_paper_orders=paper_orders_sub,
        pubsub_subscription_raw_market_data=raw_market_data_sub,
        pubsub_pull_max_messages=10,
    )


async def _drain_until_filled(
    *, settings: OmsPaperSettings, max_iterations: int = 10
) -> StreamRunner:
    """run_once を最大 max_iterations 回繰り返し、注文が約定するまで進める。"""
    runner: StreamRunner | None = None
    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as subscriber,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
    ):
        runner = StreamRunner(
            subscriber=subscriber, supabase=supabase, settings=settings, idle_backoff_seconds=0.0
        )
        for _ in range(max_iterations):
            stats = await runner.run_once()
            if stats.filled > 0:
                break
    assert runner is not None
    return runner


async def _drain_until_swing(
    *,
    settings: OmsPaperSettings,
    field_name: str,
    max_iterations: int = 10,
) -> StreamRunner:
    """run_once を最大 max_iterations 回繰り返し、指定 swing 系フィールドが
    1 以上になるまで進める。``field_name`` は ``swing_exits`` か ``swing_trails``。"""
    runner: StreamRunner | None = None
    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as subscriber,
        SupabaseClient(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supabase,
            settings=settings,
            idle_backoff_seconds=0.0,
        )
        for _ in range(max_iterations):
            stats = await runner.run_once()
            if getattr(stats, field_name) > 0:
                break
    assert runner is not None
    return runner


# --- tests -----------------------------------------------------------------


async def test_atomic_fill_rpc_serializes_buys_rounds_and_is_idempotent(
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    """Exercise the actual PostgREST function, not the Python runner mock."""

    executed_at = datetime.now(UTC)
    first = PaperFillRecord(
        order_id=uuid4(),
        symbol=test_symbol,
        side=Side.BUY,
        quantity=1,
        price=Decimal("1000"),
        signal_source=SignalSource.CONSENSUS,
        executed_at=executed_at,
    )
    second = PaperFillRecord(
        order_id=uuid4(),
        symbol=test_symbol,
        side=Side.BUY,
        quantity=1,
        price=Decimal("1001"),
        signal_source=SignalSource.CONSENSUS,
        executed_at=executed_at,
    )

    try:
        async with SupabaseClient(
            url=supabase_url,
            secret_key=supabase_secret_key,
        ) as client:
            first_result, second_result = await asyncio.gather(
                client.apply_paper_fill(
                    record=first,
                    new_holding_type=TradingStyle.DAY,
                ),
                client.apply_paper_fill(
                    record=second,
                    new_holding_type=TradingStyle.DAY,
                ),
            )
            assert first_result.outcome is PaperFillOutcome.APPLIED
            assert second_result.outcome is PaperFillOutcome.APPLIED

            # A Gateway redelivery rebuilds the deterministic order_id but can
            # carry a new timestamp and be re-simulated against a different
            # book. It must still be a duplicate, not a poison mismatch.
            redelivery = PaperFillRecord(
                order_id=first.order_id,
                symbol=test_symbol,
                side=Side.BUY,
                quantity=7,
                price=Decimal("1234"),
                signal_source=SignalSource.CONSENSUS,
                executed_at=executed_at + timedelta(seconds=30),
            )
            duplicate = await client.apply_paper_fill(
                record=redelivery,
                new_holding_type=TradingStyle.DAY,
            )
            assert duplicate.outcome is PaperFillOutcome.DUPLICATE
            assert duplicate.position_action is PaperPositionAction.UNCHANGED

            partial_sell = PaperFillRecord(
                order_id=uuid4(),
                symbol=test_symbol,
                side=Side.SELL,
                quantity=1,
                price=Decimal("999"),
                signal_source=SignalSource.CONSENSUS,
                executed_at=executed_at,
            )
            partial_result = await client.apply_paper_fill(
                record=partial_sell,
                new_holding_type=None,
            )
            assert partial_result.position_action is PaperPositionAction.UPDATED
            assert partial_result.resulting_position is not None
            assert partial_result.resulting_position.quantity == 1

            full_sell = PaperFillRecord(
                order_id=uuid4(),
                symbol=test_symbol,
                side=Side.SELL,
                quantity=1,
                price=Decimal("998"),
                signal_source=SignalSource.CONSENSUS,
                executed_at=executed_at,
            )
            full_result = await client.apply_paper_fill(
                record=full_sell,
                new_holding_type=None,
            )
            assert full_result.position_action is PaperPositionAction.DELETED
            assert full_result.resulting_position is None

        trades = await _read_trades(
            url=supabase_url,
            key=supabase_secret_key,
            symbol=test_symbol,
        )
        assert len(trades) == 4
        assert all(trade["order_id"] is not None for trade in trades)

        # The state before the SELLs proves 1000.5 used the same one-yen
        # ROUND_HALF_UP contract as position_updater.py.
        assert duplicate.resulting_position is not None
        assert duplicate.resulting_position.quantity == 2
        assert duplicate.resulting_position.entry_price == Decimal("1001")
        assert (
            await _read_paper_position(
                url=supabase_url,
                key=supabase_secret_key,
                symbol=test_symbol,
            )
            is None
        )
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)


async def test_atomic_fill_rpc_rolls_back_position_when_trade_fk_fails(
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    record = PaperFillRecord(
        order_id=uuid4(),
        symbol=test_symbol,
        side=Side.BUY,
        quantity=100,
        price=Decimal("1000"),
        signal_source=SignalSource.CONSENSUS,
        unified_signal_id=uuid4(),  # intentionally absent from aggregator_logs
        executed_at=datetime.now(UTC),
    )

    try:
        async with SupabaseClient(
            url=supabase_url,
            secret_key=supabase_secret_key,
        ) as client:
            with pytest.raises(SupabaseError):
                await client.apply_paper_fill(
                    record=record,
                    new_holding_type=TradingStyle.DAY,
                )

        assert (
            await _read_paper_position(
                url=supabase_url,
                key=supabase_secret_key,
                symbol=test_symbol,
            )
            is None
        )
        assert (
            await _read_trades(
                url=supabase_url,
                key=supabase_secret_key,
                symbol=test_symbol,
            )
            == []
        )
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)


async def test_paper_rpcs_reject_stale_position_generation(
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    original_opened_at = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    replacement_opened_at = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    try:
        await _insert_paper_position(
            url=supabase_url,
            key=supabase_secret_key,
            symbol=test_symbol,
            quantity=100,
            entry_price=Decimal("1000"),
            holding_type="swing",
            stop_loss_price=Decimal("900"),
            opened_at=original_opened_at,
        )
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _insert_paper_position(
            url=supabase_url,
            key=supabase_secret_key,
            symbol=test_symbol,
            quantity=100,
            entry_price=Decimal("2000"),
            holding_type="swing",
            stop_loss_price=Decimal("1800"),
            opened_at=replacement_opened_at,
        )

        stale_sell = PaperFillRecord(
            order_id=uuid4(),
            symbol=test_symbol,
            side=Side.SELL,
            quantity=100,
            price=Decimal("1900"),
            signal_source=SignalSource.CONSENSUS,
            executed_at=datetime.now(UTC),
        )
        async with SupabaseClient(
            url=supabase_url,
            secret_key=supabase_secret_key,
        ) as client:
            sell_result = await client.apply_paper_fill(
                record=stale_sell,
                new_holding_type=None,
                expected_position_opened_at=original_opened_at,
            )
            assert sell_result.outcome is PaperFillOutcome.REJECTED
            assert sell_result.reason is PaperFillReason.POSITION_GENERATION_MISMATCH

            stop_result = await client.update_paper_position_stop_loss(
                symbol=test_symbol,
                expected_opened_at=original_opened_at,
                stop_loss_price="1700",
            )
            assert stop_result.outcome is PaperStopUpdateOutcome.REJECTED
            assert stop_result.reason is PaperStopUpdateReason.POSITION_GENERATION_MISMATCH

            higher_stop, lower_stop = await asyncio.gather(
                client.update_paper_position_stop_loss(
                    symbol=test_symbol,
                    expected_opened_at=replacement_opened_at,
                    stop_loss_price="1900",
                ),
                client.update_paper_position_stop_loss(
                    symbol=test_symbol,
                    expected_opened_at=replacement_opened_at,
                    stop_loss_price="1850",
                ),
            )
            assert higher_stop.outcome is PaperStopUpdateOutcome.APPLIED
            assert lower_stop.outcome in {
                PaperStopUpdateOutcome.APPLIED,
                PaperStopUpdateOutcome.REJECTED,
            }
            if lower_stop.outcome is PaperStopUpdateOutcome.REJECTED:
                assert lower_stop.reason is PaperStopUpdateReason.STOP_NOT_RAISED

        position = await _read_paper_position(
            url=supabase_url,
            key=supabase_secret_key,
            symbol=test_symbol,
        )
        assert position is not None
        assert Decimal(str(position["entry_price"])) == Decimal("2000")
        assert Decimal(str(position["stop_loss_price"])) == Decimal("1900")
        assert (
            await _read_trades(
                url=supabase_url,
                key=supabase_secret_key,
                symbol=test_symbol,
            )
            == []
        )
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)


async def test_atomic_fill_rpc_is_not_executable_by_anon(
    supabase_url: str,
    supabase_anon_key: str,
) -> None:
    params = {
        "p_order_id": None,
        "p_trade_id": None,
        "p_symbol": None,
        "p_side": None,
        "p_filled_quantity": None,
        "p_fill_price": None,
        "p_signal_source": None,
        "p_unified_signal_id": None,
        "p_executed_at": None,
        "p_expected_position_opened_at": None,
        "p_new_holding_type": None,
        "p_new_target_price": None,
        "p_new_stop_loss_price": None,
        "p_new_max_hold_days": None,
        "p_new_scheduled_exit_date": None,
        "p_new_trailing_stop_pct": None,
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        response = await client.post(
            "/rest/v1/rpc/oms_paper_apply_fill",
            headers=_supabase_headers(supabase_anon_key),
            json=params,
        )
        stop_response = await client.post(
            "/rest/v1/rpc/oms_paper_update_stop_loss",
            headers=_supabase_headers(supabase_anon_key),
            json={
                "p_symbol": None,
                "p_expected_position_opened_at": None,
                "p_stop_loss_price": None,
            },
        )
    assert response.status_code in {401, 403, 404}
    assert stop_response.status_code in {401, 403, 404}


async def test_buy_order_with_fresh_book_creates_position_and_trade_row(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    book = _make_book(test_symbol)
    signal_id = uuid4()
    order = _make_order(symbol=test_symbol, side=Side.BUY, quantity=100, signal_id=signal_id)

    await _seed_aggregator_log(
        url=supabase_url, key=supabase_secret_key, signal_id=signal_id, symbol=test_symbol
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=RAW_MARKET_DATA_TOPIC,
        data=book.model_dump_json().encode("utf-8"),
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=PAPER_ORDERS_TOPIC,
        data=order.model_dump_json().encode("utf-8"),
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        paper_orders_sub=provisioned_subs["paper_orders_sub"],
        raw_market_data_sub=provisioned_subs["raw_market_data_sub"],
    )

    try:
        await _drain_until_filled(settings=settings)

        pos = await _read_paper_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos is not None
        assert pos["quantity"] == 100
        assert Decimal(str(pos["entry_price"])) == Decimal("1000")
        assert pos["trade_type"] == "paper"

        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        assert len(trades) == 1
        assert trades[0]["side"] == "BUY"
        assert trades[0]["quantity"] == 100
        assert Decimal(str(trades[0]["price"])) == Decimal("1000")
        assert trades[0]["order_id"] == str(order.order_id)
        assert trades[0]["unified_signal_id"] == str(order.unified_signal_id)
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_aggregator_log(url=supabase_url, key=supabase_secret_key, signal_id=signal_id)


async def test_full_sell_order_deletes_position_and_records_trade(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    # 1) BUY を入れて position を作る
    book = _make_book(test_symbol)
    buy_signal_id = uuid4()
    sell_signal_id = uuid4()
    buy = _make_order(symbol=test_symbol, side=Side.BUY, quantity=100, signal_id=buy_signal_id)

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
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=RAW_MARKET_DATA_TOPIC,
        data=book.model_dump_json().encode("utf-8"),
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=PAPER_ORDERS_TOPIC,
        data=buy.model_dump_json().encode("utf-8"),
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        paper_orders_sub=provisioned_subs["paper_orders_sub"],
        raw_market_data_sub=provisioned_subs["raw_market_data_sub"],
    )

    try:
        await _drain_until_filled(settings=settings)
        pos = await _read_paper_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos is not None
        assert pos["quantity"] == 100

        # 2) SELL で全決済
        # 板を新しく publish (cache 空状態の新ランナー用)
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=RAW_MARKET_DATA_TOPIC,
            data=book.model_dump_json().encode("utf-8"),
        )
        sell = _make_order(
            symbol=test_symbol, side=Side.SELL, quantity=100, signal_id=sell_signal_id
        )
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=PAPER_ORDERS_TOPIC,
            data=sell.model_dump_json().encode("utf-8"),
        )

        await _drain_until_filled(settings=settings)

        pos_after = await _read_paper_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos_after is None  # DELETE 済み

        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        sides = [t["side"] for t in trades]
        assert sides == ["BUY", "SELL"]
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=buy_signal_id
        )
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=sell_signal_id
        )


async def test_swing_stop_loss_breach_triggers_exit(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    """swing position を pre-seed → 損切り条件を満たす板を publish →
    atomic RPC で自動 SELL + position DELETE (unified_signal_id=NULL)。"""
    # 板の bids[0] = 950, swing position の stop_loss_price = 950 → 損切り発火
    book = OrderBookSnapshot(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        bids=[PriceLevel(price=Decimal("950"), quantity=500)],
        asks=[PriceLevel(price=Decimal("955"), quantity=500)],
    )

    await _insert_paper_position(
        url=supabase_url,
        key=supabase_secret_key,
        symbol=test_symbol,
        quantity=100,
        entry_price=Decimal("1000"),
        holding_type="swing",
        stop_loss_price=Decimal("950"),
        target_price=Decimal("1100"),
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=RAW_MARKET_DATA_TOPIC,
        data=book.model_dump_json().encode("utf-8"),
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        paper_orders_sub=provisioned_subs["paper_orders_sub"],
        raw_market_data_sub=provisioned_subs["raw_market_data_sub"],
    )

    try:
        await _drain_until_swing(settings=settings, field_name="swing_exits")

        # 1) position は DELETE 済み
        pos_after = await _read_paper_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos_after is None

        # 2) trades_paper に SELL 行が 1 つ、unified_signal_id=NULL
        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        assert len(trades) == 1
        assert trades[0]["side"] == "SELL"
        assert trades[0]["quantity"] == 100
        assert Decimal(str(trades[0]["price"])) == Decimal("950")
        assert trades[0]["unified_signal_id"] is None
        assert trades[0]["signal_source"] == "CONSENSUS"
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)


async def test_swing_trail_only_updates_stop_loss_price(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    """swing position に trailing_stop_pct=0.02、現在 stop=980 で seed → 板 bids=1100 を
    publish → stop は 1078 に切り上げ、trades_paper には何も書かれない。"""
    book = OrderBookSnapshot(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        bids=[PriceLevel(price=Decimal("1100"), quantity=500)],
        asks=[PriceLevel(price=Decimal("1105"), quantity=500)],
    )

    await _insert_paper_position(
        url=supabase_url,
        key=supabase_secret_key,
        symbol=test_symbol,
        quantity=100,
        entry_price=Decimal("1000"),
        holding_type="swing",
        stop_loss_price=Decimal("980"),
        trailing_stop_pct=Decimal("0.02"),
    )
    await _publish(
        project=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        topic=RAW_MARKET_DATA_TOPIC,
        data=book.model_dump_json().encode("utf-8"),
    )

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        paper_orders_sub=provisioned_subs["paper_orders_sub"],
        raw_market_data_sub=provisioned_subs["raw_market_data_sub"],
    )

    try:
        await _drain_until_swing(settings=settings, field_name="swing_trails")

        # 1) position はまだ存在し、stop_loss_price が 1078 に更新されている
        pos_after = await _read_paper_position(
            url=supabase_url, key=supabase_secret_key, symbol=test_symbol
        )
        assert pos_after is not None
        assert pos_after["quantity"] == 100  # 数量は変わらず
        assert Decimal(str(pos_after["stop_loss_price"])) == Decimal("1078")

        # 2) trades_paper には何も書かれていない
        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        assert trades == []
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=test_symbol)
