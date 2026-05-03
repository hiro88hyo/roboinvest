"""OMS Live Phase 3 e2e: 検証環境 (kabuステーション 18081) で実発注を走らせる skeleton。

平日 9:00-15:00 JST に env を揃えて走らせるためのスキャフォールド。
全テストは下記の skip ガードでデフォルト OFF (CI / 通常 ``pytest`` 実行では走らない)。

ENV (全部揃って初めて走る):
  OMS_LIVE_PHASE3_E2E=1                 — フラグ。明示 opt-in
  KABU_API_BASE_URL                     — 例: ``http://localhost:18081/kabusapi``
  KABU_API_PASSWORD                     — kabu API パスワード
  KABU_ORDER_PASSWORD                   — kabu 注文パスワード (発注時必須)
  OMS_LIVE_PHASE3_SYMBOL                — 例: ``7203`` (default)
  OMS_LIVE_PHASE3_QUANTITY              — 株数 (default: ``100``)
  OMS_LIVE_PHASE3_EXCHANGE              — 市場コード (default: ``1`` 東証)
  PUBSUB_EMULATOR_HOST                  — 例: ``localhost:8085``
  PUBSUB_PROJECT_ID                     — 例: ``trade-ai-dev``
  SUPABASE_URL                          — 例: ``http://127.0.0.1:54321``
  SUPABASE_SECRET_KEY                   — sb_secret_*

時間ガード:
  Asia/Tokyo タイムゾーンで weekday < 5 (月-金) かつ 9 <= hour < 15。
  祝日カレンダーは未実装 (将来 jpholiday 等を導入する場合は本判定を差し替え)。

実行手順 (Thu 9:00-15:00 JST に流す想定):
  1. SSH トンネル: ``ssh -N -L 18081:127.0.0.1:18081 user@<win-ip>``
  2. ``docker compose -f infra/docker-compose.dev.yml up -d`` (Pub/Sub エミュレータ)
  3. ``cd infra && supabase start`` (Supabase ローカル)
  4. Pub/Sub topic ``live-orders`` 作成 (init-topics.sh)
  5. env を揃えて以下を実行:
     ``uv run pytest services/oms-live/tests/integration/test_phase3_kabu_e2e.py -v -s``

注意:
  - 本物の検証環境 18081 に sendorder が走るため、対象銘柄・数量に注意
  - 検証環境は約定が市場時間内のみ。時間外は sendorder の挙動 (受付 vs reject) を
    観測する用途では本テストは skip され、``scripts/probe-kabu-oms.py --send`` を使う
  - 想定 DB 遷移: BUY 100 → fill → positions(live) 1 行追加 → SELL 100 → fill →
    positions(live) 削除 + trades_live 2 行 + system_status.daily_pnl 加算
  - スクリプト終了時の cleanup: 万が一 fill が片方だけ走った場合に備え、test 後の
    finally で対象銘柄の positions(live) / trades_live を強制削除する

Phase 3 直前に拡張する候補 (本 PR 範囲外):
  - 同一 order_id 二回投入で skipped_duplicate=1 を確認するケース
  - 14:50 closeout の発火確認 (system_status.trading_style=day で run_closeout)
  - 安全装備 (OMS_LIVE_MAX_QTY_PER_ORDER 等の env knob) を検証するケース
  - test_e2e.py との helper 共通化 (現状は重複コピーで scope を抑える)
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from oms_live.clients.pubsub import PubSubSubscriber
from oms_live.clients.supabase import SupabaseClient
from oms_live.config import OmsLiveSettings
from oms_live.kabu_client import KabuLiveClient
from oms_live.streaming.runner import StreamRunner
from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode
from trade_contracts.order import OrderRequest

LIVE_ORDERS_TOPIC = "live-orders"

_REQUIRED_KABU_ENV = ("KABU_API_BASE_URL", "KABU_API_PASSWORD", "KABU_ORDER_PASSWORD")


def _is_market_hours_jst() -> bool:
    """平日 9:00-15:00 JST かを判定。祝日カレンダーは未実装。"""
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    if now.weekday() >= 5:  # 土日
        return False
    return 9 <= now.hour < 15


def _kabu_env_ready() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_KABU_ENV)


_PHASE3_ENABLED = os.environ.get("OMS_LIVE_PHASE3_E2E") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _PHASE3_ENABLED, reason="OMS_LIVE_PHASE3_E2E != 1"),
    pytest.mark.skipif(
        not _is_market_hours_jst(),
        reason="not in JST market hours (Mon-Fri 9:00-15:00)",
    ),
    pytest.mark.skipif(
        not _kabu_env_ready(),
        reason=f"missing KABU env: {_REQUIRED_KABU_ENV}",
    ),
]


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
def kabu_base_url() -> str:
    return os.environ["KABU_API_BASE_URL"]


@pytest.fixture(scope="session")
def kabu_api_password() -> str:
    return os.environ["KABU_API_PASSWORD"]


@pytest.fixture(scope="session")
def kabu_order_password() -> str:
    return os.environ["KABU_ORDER_PASSWORD"]


@pytest.fixture(scope="session")
def phase3_symbol() -> str:
    return os.environ.get("OMS_LIVE_PHASE3_SYMBOL", "7203")


@pytest.fixture(scope="session")
def phase3_quantity() -> int:
    return int(os.environ.get("OMS_LIVE_PHASE3_QUANTITY", "100"))


@pytest.fixture(scope="session")
def phase3_exchange() -> int:
    return int(os.environ.get("OMS_LIVE_PHASE3_EXCHANGE", "1"))


@pytest.fixture
def run_id() -> str:
    return uuid4().hex[:8]


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
        "live_orders_sub": f"it-omsl-phase3-live-orders-{run_id}",
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
            params={"symbol": f"eq.{symbol}", "trade_type": "eq.live"},
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
    signal_id: UUID,
) -> OrderRequest:
    return OrderRequest(
        unified_signal_id=signal_id,
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
    kabu_base_url: str,
    kabu_api_password: str,
    kabu_order_password: str,
    kabu_default_exchange: int,
) -> OmsLiveSettings:
    return OmsLiveSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_live_orders=live_orders_sub,
        pubsub_pull_max_messages=10,
        kabu_api_base_url=kabu_base_url,
        kabu_api_password=kabu_api_password,
        kabu_order_password=kabu_order_password,
        kabu_default_exchange=kabu_default_exchange,
        kabu_account_type=4,
        order_fill_poll_interval_seconds=1.0,
        order_fill_timeout_seconds=30.0,
    )


async def _drain_until_filled(
    *,
    settings: OmsLiveSettings,
    max_iterations: int = 30,
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
        ) as kabu,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            supabase=supabase,
            kabu=kabu,
            settings=settings,
            idle_backoff_seconds=0.5,
        )
        for _ in range(max_iterations):
            stats = await runner.run_once()
            if stats.filled > 0:
                return


# --- tests ------------------------------------------------------------------


async def test_phase3_buy_then_sell_round_trip(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    kabu_base_url: str,
    kabu_api_password: str,
    kabu_order_password: str,
    phase3_symbol: str,
    phase3_quantity: int,
    phase3_exchange: int,
) -> None:
    """検証環境 18081 に対し BUY → SELL を一気通貫で流す skeleton。

    成功条件:
      - trades_live に BUY と SELL の 2 行が入る
      - positions(live) は最終的に空
      - daily_pnl が SELL 後に変動 (BUY 時点では変わらない)

    失敗時 cleanup:
      - finally で対象 symbol の trades_live / positions(live) を強制削除
      - aggregator_logs も削除
    """
    buy_signal_id = uuid4()
    sell_signal_id = uuid4()

    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        live_orders_sub=provisioned_subs["live_orders_sub"],
        kabu_base_url=kabu_base_url,
        kabu_api_password=kabu_api_password,
        kabu_order_password=kabu_order_password,
        kabu_default_exchange=phase3_exchange,
    )

    try:
        await _seed_aggregator_log(
            url=supabase_url,
            key=supabase_secret_key,
            signal_id=buy_signal_id,
            symbol=phase3_symbol,
        )
        await _seed_aggregator_log(
            url=supabase_url,
            key=supabase_secret_key,
            signal_id=sell_signal_id,
            symbol=phase3_symbol,
            action="SELL",
        )

        # --- BUY ---
        buy = _make_order(
            symbol=phase3_symbol,
            side=Side.BUY,
            quantity=phase3_quantity,
            signal_id=buy_signal_id,
        )
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=LIVE_ORDERS_TOPIC,
            data=buy.model_dump_json().encode("utf-8"),
        )
        await _drain_until_filled(settings=settings)

        pos = await _read_live_position(
            url=supabase_url, key=supabase_secret_key, symbol=phase3_symbol
        )
        assert pos is not None, "BUY 後に positions(live) が作られていない"
        assert pos["quantity"] == phase3_quantity
        assert Decimal(str(pos["entry_price"])) > Decimal("0")

        # --- SELL ---
        sell = _make_order(
            symbol=phase3_symbol,
            side=Side.SELL,
            quantity=phase3_quantity,
            signal_id=sell_signal_id,
        )
        await _publish(
            project=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            topic=LIVE_ORDERS_TOPIC,
            data=sell.model_dump_json().encode("utf-8"),
        )
        await _drain_until_filled(settings=settings)

        pos_after = await _read_live_position(
            url=supabase_url, key=supabase_secret_key, symbol=phase3_symbol
        )
        assert pos_after is None, "SELL 後に positions(live) が残っている"

        trades = await _read_trades(url=supabase_url, key=supabase_secret_key, symbol=phase3_symbol)
        sides = sorted(t["side"] for t in trades)
        assert sides == ["BUY", "SELL"], f"trades_live に BUY/SELL 1 行ずつ無い: {sides}"

        # 各約定行に order_id が乗っていること (Phase A/B/C で追加した冪等性キー)
        for t in trades:
            assert t.get("order_id") is not None, f"order_id が NULL: {t}"
    finally:
        await _delete_trades(url=supabase_url, key=supabase_secret_key, symbol=phase3_symbol)
        await _delete_position(url=supabase_url, key=supabase_secret_key, symbol=phase3_symbol)
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=buy_signal_id
        )
        await _delete_aggregator_log(
            url=supabase_url, key=supabase_secret_key, signal_id=sell_signal_id
        )
