"""Shared pytest fixtures for aggregator tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from trade_contracts.enums import Action, SignalSource, TradingStyle
from trade_contracts.signal import StrategySignal

SignalFactory = Callable[..., StrategySignal]


def _make_signal(
    *,
    source: SignalSource = SignalSource.RULE,
    symbol: str = "7203",
    action: Action = Action.BUY,
    confidence: float = 0.7,
    reasoning: str | None = None,
    created_at: datetime | None = None,
    price: Decimal | None = None,
    holding_type: TradingStyle | None = None,
    stop_loss_price: Decimal | None = None,
    target_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    max_hold_days: int | None = None,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
    spread_bps: Decimal | None = None,
    tick_size: Decimal | None = None,
    spread_ticks: Decimal | None = None,
    bid_depth_5: int | None = None,
    ask_depth_5: int | None = None,
    book_imbalance_5: Decimal | None = None,
    minutes_from_open: int | None = None,
    minutes_to_close: int | None = None,
    session_phase: str | None = None,
) -> StrategySignal:
    return StrategySignal(
        signal_id=uuid4(),
        source=source,
        symbol=symbol,
        price=price,
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        holding_type=holding_type,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        trailing_stop_pct=trailing_stop_pct,
        max_hold_days=max_hold_days,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=spread_bps,
        tick_size=tick_size,
        spread_ticks=spread_ticks,
        bid_depth_5=bid_depth_5,
        ask_depth_5=ask_depth_5,
        book_imbalance_5=book_imbalance_5,
        minutes_from_open=minutes_from_open,
        minutes_to_close=minutes_to_close,
        session_phase=session_phase,
        created_at=created_at or datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def signal_factory() -> SignalFactory:
    return _make_signal


# --- integration fixtures ---------------------------------------------------
# Pub/Sub エミュレータ + Supabase ローカルが立ち上がっていない環境では `pytest.skip`。


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
    """テストごとに一意な suffix。Pub/Sub subscription の衝突回避用。"""
    return uuid4().hex[:8]


@pytest.fixture
async def pubsub_admin(pubsub_emulator_host: str) -> AsyncIterator[httpx.AsyncClient]:
    """Pub/Sub エミュレータの管理 REST を叩く httpx クライアント (subscription 管理用)。"""
    async with httpx.AsyncClient(
        base_url=f"http://{pubsub_emulator_host}",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest.fixture
def test_symbol(run_id: str) -> str:
    """既存データと衝突しない一時テスト銘柄。"""
    return f"IT{run_id[:6].upper()}"


@pytest.fixture
def unique_resources(run_id: str) -> Iterator[dict[str, str]]:
    """テストで使う一意な subscription 名を払い出す。"""
    yield {
        "signals_a_sub": f"it-agg-signals-a-{run_id}",
        "signals_b_sub": f"it-agg-signals-b-{run_id}",
        "trade_signals_sub": f"it-agg-trade-signals-{run_id}",
    }
