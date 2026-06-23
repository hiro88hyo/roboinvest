"""Shared pytest fixtures for gateway tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from trade_contracts.enums import Action, SignalSource, TradeMode, TradingStyle
from trade_contracts.risk import KillSwitchState
from trade_contracts.signal import UnifiedTradeSignal

UnifiedSignalFactory = Callable[..., UnifiedTradeSignal]
KillSwitchStateFactory = Callable[..., KillSwitchState]


def _make_unified_signal(
    *,
    symbol: str = "7203",
    price: Decimal | None = None,
    action: Action = Action.BUY,
    confidence: float = 0.7,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    holding_type: TradingStyle = TradingStyle.DAY,
    stop_loss_price: Decimal | None = None,
    target_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    max_hold_days: int | None = None,
    spread_bps: Decimal | None = None,
    spread_ticks: Decimal | None = None,
    ask_depth_5: int | None = None,
    created_at: datetime | None = None,
) -> UnifiedTradeSignal:
    return UnifiedTradeSignal(
        signal_id=uuid4(),
        symbol=symbol,
        price=price,
        action=action,
        confidence=confidence,
        signal_source=signal_source,
        holding_type=holding_type,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        trailing_stop_pct=trailing_stop_pct,
        max_hold_days=max_hold_days,
        spread_bps=spread_bps,
        spread_ticks=spread_ticks,
        ask_depth_5=ask_depth_5,
        created_at=created_at or datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
    )


def _make_kill_switch_state(
    *,
    is_trading_allowed: bool = True,
    trade_mode: TradeMode = TradeMode.LIVE,
    trading_style: TradingStyle = TradingStyle.DAY,
    daily_pnl: Decimal = Decimal("0"),
    weekly_pnl: Decimal = Decimal("0"),
    monthly_pnl: Decimal = Decimal("0"),
    daily_loss_limit: Decimal = Decimal("10000"),
    weekly_loss_limit: Decimal = Decimal("30000"),
    monthly_loss_limit: Decimal = Decimal("100000"),
    updated_at: datetime | None = None,
) -> KillSwitchState:
    return KillSwitchState(
        is_trading_allowed=is_trading_allowed,
        trade_mode=trade_mode,
        trading_style=trading_style,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        monthly_pnl=monthly_pnl,
        daily_loss_limit=daily_loss_limit,
        weekly_loss_limit=weekly_loss_limit,
        monthly_loss_limit=monthly_loss_limit,
        updated_at=updated_at or datetime(2026, 4, 23, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def unified_signal_factory() -> UnifiedSignalFactory:
    return _make_unified_signal


@pytest.fixture
def kill_switch_state_factory() -> KillSwitchStateFactory:
    return _make_kill_switch_state


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
        "trade_signals_sub": f"it-gw-trade-signals-{run_id}",
        "live_orders_sub": f"it-gw-live-orders-{run_id}",
        "paper_orders_sub": f"it-gw-paper-orders-{run_id}",
    }
