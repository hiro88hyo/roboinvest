"""StreamRunner の end-to-end 統合テスト。

実際の Pub/Sub エミュレータと Supabase PostgREST に対して:
- raw-market-data に tick を publish
- StreamRunner を 1 回転させる (pull → 処理 → publish)
- processed-features サブスクリプションから pull して
  `ProcessedFeatures` が届くことを確認

Supabase 側は `fetch_positions` が空配列を返す想定 (テスト銘柄は存在しない)。
ポジション更新の書き込みパスそのものは統合では検証しない。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from feature_engine.clients.pubsub import PubSubPublisher, PubSubSubscriber
from feature_engine.clients.supabase import SupabaseReader, SupabaseWriter
from feature_engine.config import FeatureEngineSettings
from feature_engine.storage.warm import WarmWriter
from feature_engine.streaming.feature_state import StreamingFeatureState
from feature_engine.streaming.runner import StreamRunner
from feature_engine.streaming.session import TickSession
from trade_contracts.features import ProcessedFeatures
from trade_contracts.market import TickData

pytestmark = pytest.mark.integration


RAW_TOPIC = "raw-market-data"
FEATURES_TOPIC = "processed-features"


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
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    unique_resources: dict[str, str],
) -> AsyncIterator[dict[str, str]]:
    raw_sub = unique_resources["raw_sub"]
    features_sub = unique_resources["features_sub"]
    await _ensure_subscription(pubsub_admin, pubsub_project_id, raw_sub, RAW_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, features_sub, FEATURES_TOPIC)
    try:
        yield unique_resources
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, raw_sub)
        await _delete_subscription(pubsub_admin, pubsub_project_id, features_sub)


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    raw_sub: str,
    storage_dir: Path,
) -> FeatureEngineSettings:
    return FeatureEngineSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_raw=raw_sub,
        pubsub_topic_features=FEATURES_TOPIC,
        pubsub_pull_max_messages=10,
        storage_tick_resolution="raw",
        storage_warm_dir=storage_dir,
    )


async def test_tick_flows_from_raw_to_features(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
    tmp_path: Path,
) -> None:
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        raw_sub=provisioned_subs["raw_sub"],
        storage_dir=tmp_path / "warm",
    )
    tick = TickData(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        price=Decimal("2500"),
        volume=100,
    )

    async with (
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
    ):
        await publisher.publish(
            RAW_TOPIC,
            data=tick.model_dump_json().encode("utf-8"),
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
        SupabaseReader(url=supabase_url, secret_key=supabase_secret_key) as reader,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        warm_writer = WarmWriter(base_dir=tmp_path / "warm", resolution="raw")
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            reader=reader,
            writer=writer,
            feature_state=StreamingFeatureState.from_settings(settings),
            tick_session=TickSession(),
            settings=settings,
            warm_writer=warm_writer,
            idle_backoff_seconds=0.0,
        )
        stats = await runner.run(iterations=1)

    assert len(stats) == 1
    assert stats[0].ticks_processed == 1, stats[0]
    assert stats[0].acked == 1

    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as subscriber:
        received = await subscriber.pull(
            provisioned_subs["features_sub"], max_messages=5, return_immediately=True
        )
        assert received, "no ProcessedFeatures was published to features subscription"
        features = ProcessedFeatures.model_validate_json(received[0].data)
        assert features.symbol == test_symbol
        assert features.price == Decimal("2500")
        await subscriber.acknowledge(provisioned_subs["features_sub"], [m.ack_id for m in received])


async def test_duplicate_tick_is_deduplicated(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
    tmp_path: Path,
) -> None:
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        raw_sub=provisioned_subs["raw_sub"],
        storage_dir=tmp_path / "warm",
    )
    tick = TickData(
        symbol=test_symbol,
        timestamp=datetime.now(UTC),
        price=Decimal("3000"),
        volume=50,
    )
    payload = tick.model_dump_json().encode("utf-8")

    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        # 同一 (symbol, timestamp) を 2 回 publish
        await publisher.publish(RAW_TOPIC, data=payload, attributes={"symbol": test_symbol})
        await publisher.publish(RAW_TOPIC, data=payload, attributes={"symbol": test_symbol})

    tick_session = TickSession()
    feature_state = StreamingFeatureState.from_settings(settings)

    async with (
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as subscriber,
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
        SupabaseReader(url=supabase_url, secret_key=supabase_secret_key) as reader,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            reader=reader,
            writer=writer,
            feature_state=feature_state,
            tick_session=tick_session,
            settings=settings,
            idle_backoff_seconds=0.0,
        )
        stats = await runner.run_once()

    # emulator は `max_messages=10` での pull で両メッセージを 1 バッチで返す想定
    assert stats.received == 2, f"expected 2 messages pulled in one batch, got {stats.received}"
    assert stats.ticks_processed == 1, f"expected 1 accepted tick, got {stats.ticks_processed}"
    assert stats.ticks_dropped == 1, f"expected 1 dropped dup, got {stats.ticks_dropped}"

    # features 側には 1 件だけ配信されているはず (処理回数 = publish 回数ではない)
    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as subscriber:
        received = await subscriber.pull(
            provisioned_subs["features_sub"], max_messages=5, return_immediately=True
        )
        assert len(received) == 1
        await subscriber.acknowledge(provisioned_subs["features_sub"], [m.ack_id for m in received])
