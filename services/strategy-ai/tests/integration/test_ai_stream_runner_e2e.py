"""StreamRunner の end-to-end 統合テスト。

実際の Pub/Sub エミュレータと Supabase PostgREST に対して:
- processed-features に ProcessedFeatures を publish
- StreamRunner を 1 回転させる (pull → AiStrategy 評価 → publish + strategy_logs upsert)
- strategy-signals-b サブスクリプションから pull して
  `StrategySignal` (source=AI) が届くことを確認
- Supabase `strategy_logs` (source=AI) に行が書き込まれていることを確認

テスト後は挿入した strategy_logs 行を削除する。

mypy duplicate-module を避けるため tests/conftest.py は持たず、
このファイル内で環境チェックとリソース準備をすべて行う。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from strategy_ai.clients.pubsub import PubSubPublisher, PubSubSubscriber
from strategy_ai.clients.supabase import SupabaseWriter
from strategy_ai.config import StrategyAiSettings
from strategy_ai.engine import StrategyAiEngine
from strategy_ai.llm.fixture import FixtureLLMClient
from strategy_ai.strategy import AiStrategy
from strategy_ai.streaming.runner import StreamRunner
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

pytestmark = pytest.mark.integration


FEATURES_TOPIC = "processed-features"
SIGNALS_TOPIC = "strategy-signals-b"


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"{name} not set")
    return val


@pytest.fixture(scope="session")
def pubsub_emulator_host() -> str:
    host = _require_env("PUBSUB_EMULATOR_HOST")
    try:
        resp = httpx.get(f"http://{host}/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"pubsub emulator unreachable at {host}: {exc}")
    if resp.status_code >= 500:
        pytest.skip(f"pubsub emulator unhealthy: status={resp.status_code}")
    return host


@pytest.fixture(scope="session")
def pubsub_project_id() -> str:
    return _require_env("PUBSUB_PROJECT_ID")


@pytest.fixture(scope="session")
def supabase_url() -> str:
    url = _require_env("SUPABASE_URL")
    try:
        resp = httpx.get(f"{url.rstrip('/')}/rest/v1/", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"supabase unreachable at {url}: {exc}")
    if resp.status_code >= 500:
        pytest.skip(f"supabase unhealthy: status={resp.status_code}")
    return url


@pytest.fixture(scope="session")
def supabase_secret_key() -> str:
    return _require_env("SUPABASE_SECRET_KEY")


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


@pytest.fixture
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    run_id: str,
) -> AsyncIterator[dict[str, str]]:
    features_sub = f"it-sai-features-{run_id}"
    signals_sub = f"it-sai-signals-{run_id}"
    await _ensure_subscription(pubsub_admin, pubsub_project_id, features_sub, FEATURES_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, signals_sub, SIGNALS_TOPIC)
    try:
        yield {"features_sub": features_sub, "signals_sub": signals_sub}
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, features_sub)
        await _delete_subscription(pubsub_admin, pubsub_project_id, signals_sub)


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


async def _delete_strategy_log(
    *, supabase_url: str, supabase_secret_key: str, signal_id: str
) -> None:
    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/strategy_logs",
            params={"signal_id": f"eq.{signal_id}"},
            headers=headers,
        )


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    features_sub: str,
) -> StrategyAiSettings:
    return StrategyAiSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_features=features_sub,
        pubsub_topic_signals=SIGNALS_TOPIC,
        pubsub_pull_max_messages=10,
    )


def _make_strategy() -> AiStrategy:
    """決定論的に BUY を 1 件返すテスト戦略。"""
    llm = FixtureLLMClient(
        responses=(json.dumps({"action": "BUY", "confidence": 0.7, "reasoning": "integration"}),)
    )
    return AiStrategy(llm=llm, min_interval_seconds=0)


async def test_features_flow_to_signal_topic_and_supabase(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        features_sub=provisioned_subs["features_sub"],
    )
    features = ProcessedFeatures(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        price=Decimal("2500"),
    )

    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            FEATURES_TOPIC,
            data=features.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol},
        )

    async with (
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as subscriber,
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        engine = StrategyAiEngine([_make_strategy()])
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
            idle_backoff_seconds=0.0,
        )
        stats_list = await runner.run(iterations=1)

    assert len(stats_list) == 1
    stats = stats_list[0]
    assert stats.features_processed == 1, stats
    assert stats.signals_emitted == 1, stats
    assert stats.acked == 1

    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as subscriber:
        received = await subscriber.pull(
            provisioned_subs["signals_sub"], max_messages=5, return_immediately=True
        )
        assert received, "no StrategySignal published to strategy-signals-b"
        signal = StrategySignal.model_validate_json(received[0].data)
        assert signal.symbol == test_symbol
        assert signal.action.value == "BUY"
        assert signal.source.value == "AI"
        assert signal.reasoning == "integration"
        await subscriber.acknowledge(provisioned_subs["signals_sub"], [m.ack_id for m in received])

    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        try:
            resp = await client.get(
                "/rest/v1/strategy_logs",
                params={"symbol": f"eq.{test_symbol}", "select": "*"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert len(rows) == 1, rows
            assert rows[0]["source"] == "AI"
            assert rows[0]["action"] == "BUY"
            assert rows[0]["reasoning"] == "integration"
        finally:
            await _delete_strategy_log(
                supabase_url=supabase_url,
                supabase_secret_key=supabase_secret_key,
                signal_id=str(signal.signal_id),
            )


async def test_redelivered_features_inserts_distinct_signal_rows(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    """Pub/Sub の再配信 (同じ ProcessedFeatures が 2 回届く) でも
    `strategy_logs` に 2 行入り (signal_id は別) かつ一意制約違反が起きないことを確認する。
    AiStrategy のレート制御は min_interval_seconds=0 で外している。
    """
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        features_sub=provisioned_subs["features_sub"],
    )
    payload = (
        ProcessedFeatures(
            symbol=test_symbol,
            timestamp=datetime.now(UTC),
            price=Decimal("2500"),
        )
        .model_dump_json()
        .encode("utf-8")
    )

    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(FEATURES_TOPIC, data=payload, attributes={"symbol": test_symbol})
        await publisher.publish(FEATURES_TOPIC, data=payload, attributes={"symbol": test_symbol})

    inserted_signal_ids: list[str] = []
    async with (
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as subscriber,
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        # 2 件分のレスポンスを与える
        llm = FixtureLLMClient(
            responses=(
                json.dumps({"action": "BUY", "confidence": 0.7, "reasoning": "1st"}),
                json.dumps({"action": "BUY", "confidence": 0.7, "reasoning": "2nd"}),
            )
        )
        engine = StrategyAiEngine([AiStrategy(llm=llm, min_interval_seconds=0)])
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
            idle_backoff_seconds=0.0,
        )
        stats = await runner.run_once()

    assert stats.received == 2, stats
    assert stats.features_processed == 2, stats
    assert stats.signals_emitted == 2, stats

    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        try:
            resp = await client.get(
                "/rest/v1/strategy_logs",
                params={"symbol": f"eq.{test_symbol}", "select": "signal_id"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert len(rows) == 2  # 異なる signal_id が 2 行
            inserted_signal_ids = [r["signal_id"] for r in rows]
        finally:
            for sid in inserted_signal_ids:
                await _delete_strategy_log(
                    supabase_url=supabase_url,
                    supabase_secret_key=supabase_secret_key,
                    signal_id=sid,
                )
