"""StreamRunner の end-to-end 統合テスト。

実際の Pub/Sub エミュレータと Supabase PostgREST に対して:
- strategy-signals-a / strategy-signals-b に StrategySignal を publish
- StreamRunner を 1 回転させる (pull A + pull B → aggregate → trade-signals publish
  + aggregator_logs upsert)
- trade-signals サブスクリプションから pull して UnifiedTradeSignal が
  届くことを確認
- Supabase `aggregator_logs` に行が書き込まれていることを確認

テスト後は挿入した aggregator_logs 行を削除する。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from aggregator.clients.pubsub import PubSubPublisher, PubSubSubscriber
from aggregator.clients.supabase import SupabaseWriter
from aggregator.config import AggregatorSettings
from aggregator.consensus import ConsensusConfig
from aggregator.streaming.runner import StreamRunner
from trade_contracts.enums import Action, SignalSource
from trade_contracts.signal import StrategySignal, UnifiedTradeSignal

pytestmark = pytest.mark.integration


SIGNALS_A_TOPIC = "strategy-signals-a"
SIGNALS_B_TOPIC = "strategy-signals-b"
TRADE_SIGNALS_TOPIC = "trade-signals"
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


async def _delete_aggregator_log(
    *, supabase_url: str, supabase_secret_key: str, signal_id: str
) -> None:
    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        await client.delete(
            "/rest/v1/aggregator_logs",
            params={"signal_id": f"eq.{signal_id}"},
            headers=headers,
        )


async def _insert_strategy_log(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    signal: StrategySignal,
) -> None:
    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    row = {
        "signal_id": str(signal.signal_id),
        "source": signal.source.value,
        "symbol": signal.symbol,
        "action": signal.action.value,
        "confidence": float(signal.confidence),
        "reasoning": signal.reasoning,
        "created_at": signal.created_at.isoformat(),
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        resp = await client.post("/rest/v1/strategy_logs", headers=headers, json=[row])
        if resp.status_code >= 300:
            raise RuntimeError(
                f"failed to seed strategy_logs row: {resp.status_code} {resp.text[:200]}"
            )


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


@pytest.fixture
async def provisioned_subs(
    pubsub_admin: httpx.AsyncClient,
    pubsub_project_id: str,
    unique_resources: dict[str, str],
) -> AsyncIterator[dict[str, str]]:
    signals_a = unique_resources["signals_a_sub"]
    signals_b = unique_resources["signals_b_sub"]
    trade_sub = unique_resources["trade_signals_sub"]
    await _ensure_subscription(pubsub_admin, pubsub_project_id, signals_a, SIGNALS_A_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, signals_b, SIGNALS_B_TOPIC)
    await _ensure_subscription(pubsub_admin, pubsub_project_id, trade_sub, TRADE_SIGNALS_TOPIC)
    try:
        yield unique_resources
    finally:
        await _delete_subscription(pubsub_admin, pubsub_project_id, signals_a)
        await _delete_subscription(pubsub_admin, pubsub_project_id, signals_b)
        await _delete_subscription(pubsub_admin, pubsub_project_id, trade_sub)


def _build_settings(
    *,
    supabase_url: str,
    supabase_secret_key: str,
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    signals_a_sub: str,
    signals_b_sub: str,
) -> AggregatorSettings:
    return AggregatorSettings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        pubsub_subscription_signals_a=signals_a_sub,
        pubsub_subscription_signals_b=signals_b_sub,
        pubsub_topic_trade_signals=TRADE_SIGNALS_TOPIC,
        pubsub_pull_max_messages=10,
        pairing_bucket_ms=1000,
        pairing_window_ms=0,  # fast path only; both A and B land in the same bucket
    )


def _strategy_signal(
    *,
    source: SignalSource,
    symbol: str,
    action: Action = Action.BUY,
    confidence: float = 0.8,
    created_at: datetime | None = None,
) -> StrategySignal:
    return StrategySignal(
        signal_id=uuid4(),
        source=source,
        symbol=symbol,
        action=action,
        confidence=confidence,
        reasoning=None,
        created_at=created_at or datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
    )


async def test_paired_ab_flows_to_trade_signals_and_supabase(
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
        signals_a_sub=provisioned_subs["signals_a_sub"],
        signals_b_sub=provisioned_subs["signals_b_sub"],
    )
    ts = datetime.now(UTC)
    sig_a = _strategy_signal(source=SignalSource.RULE, symbol=test_symbol, created_at=ts)
    sig_b = _strategy_signal(source=SignalSource.AI, symbol=test_symbol, created_at=ts)

    await _insert_strategy_log(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        signal=sig_a,
    )
    await _insert_strategy_log(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        signal=sig_b,
    )

    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            SIGNALS_A_TOPIC,
            data=sig_a.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol, "source": "RULE"},
        )
        await publisher.publish(
            SIGNALS_B_TOPIC,
            data=sig_b.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol, "source": "AI"},
        )

    inserted_signal_ids: list[str] = []
    async with (
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as sub_a,
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as sub_b,
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        runner = StreamRunner(
            subscriber_a=sub_a,
            subscriber_b=sub_b,
            publisher=publisher,
            writer=writer,
            settings=settings,
            consensus_config=ConsensusConfig(),
            idle_backoff_seconds=0.0,
        )
        stats_list = await runner.run(iterations=1)

    relevant = [s for s in stats_list if s.buckets_emitted > 0]
    assert relevant, f"no bucket emitted: {stats_list}"
    aggregate_stats = relevant[0]
    assert aggregate_stats.unified_emitted == 1
    assert aggregate_stats.acked_a == 1
    assert aggregate_stats.acked_b == 1

    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
    ) as subscriber:
        received = await subscriber.pull(
            provisioned_subs["trade_signals_sub"], max_messages=5, return_immediately=True
        )
        assert received, "no UnifiedTradeSignal published to trade-signals"
        unified = UnifiedTradeSignal.model_validate_json(received[0].data)
        inserted_signal_ids.append(str(unified.signal_id))
        assert unified.symbol == test_symbol
        assert unified.action is Action.BUY
        assert unified.signal_source is SignalSource.CONSENSUS
        await subscriber.acknowledge(
            provisioned_subs["trade_signals_sub"], [m.ack_id for m in received]
        )

    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        try:
            resp = await client.get(
                "/rest/v1/aggregator_logs",
                params={"symbol": f"eq.{test_symbol}", "select": "*"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert len(rows) == 1, rows
            assert rows[0]["signal_source"] == "CONSENSUS"
            assert rows[0]["action"] == "BUY"
            assert rows[0]["strategy_signal_id_a"] == str(sig_a.signal_id)
            assert rows[0]["strategy_signal_id_b"] == str(sig_b.signal_id)
        finally:
            for sid in inserted_signal_ids:
                await _delete_aggregator_log(
                    supabase_url=supabase_url,
                    supabase_secret_key=supabase_secret_key,
                    signal_id=sid,
                )
            for sig in (sig_a, sig_b):
                await _delete_strategy_log(
                    supabase_url=supabase_url,
                    supabase_secret_key=supabase_secret_key,
                    signal_id=str(sig.signal_id),
                )


async def test_single_source_emits_after_window_timeout(
    provisioned_subs: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
    test_symbol: str,
) -> None:
    """A だけ到着した状態でウィンドウが 0ms で満了すれば、単独源のまま
    UnifiedTradeSignal (signal_source=RULE) が発行されることを確認する。"""
    settings = _build_settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        pubsub_project_id=pubsub_project_id,
        pubsub_emulator_host=pubsub_emulator_host,
        signals_a_sub=provisioned_subs["signals_a_sub"],
        signals_b_sub=provisioned_subs["signals_b_sub"],
    )
    sig_a = _strategy_signal(source=SignalSource.RULE, symbol=test_symbol)

    await _insert_strategy_log(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        signal=sig_a,
    )

    async with PubSubPublisher(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
    ) as publisher:
        await publisher.publish(
            SIGNALS_A_TOPIC,
            data=sig_a.model_dump_json().encode("utf-8"),
            attributes={"symbol": test_symbol, "source": "RULE"},
        )

    inserted_signal_ids: list[str] = []
    async with (
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as sub_a,
        PubSubSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
        ) as sub_b,
        PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as publisher,
        SupabaseWriter(url=supabase_url, secret_key=supabase_secret_key) as writer,
    ):
        runner = StreamRunner(
            subscriber_a=sub_a,
            subscriber_b=sub_b,
            publisher=publisher,
            writer=writer,
            settings=settings,
            consensus_config=ConsensusConfig(),
            idle_backoff_seconds=0.0,
        )
        await runner.run(iterations=1)

    async with PubSubSubscriber(
        project_id=pubsub_project_id,
        emulator_host=pubsub_emulator_host,
        timeout_seconds=TEST_PUBSUB_TIMEOUT_SECONDS,
    ) as subscriber:
        received = await subscriber.pull(
            provisioned_subs["trade_signals_sub"], max_messages=5, return_immediately=True
        )
        assert received, "no UnifiedTradeSignal published for single-source bucket"
        unified = UnifiedTradeSignal.model_validate_json(received[0].data)
        inserted_signal_ids.append(str(unified.signal_id))
        assert unified.symbol == test_symbol
        assert unified.signal_source is SignalSource.RULE
        assert unified.strategy_signal_id_a == sig_a.signal_id
        assert unified.strategy_signal_id_b is None
        await subscriber.acknowledge(
            provisioned_subs["trade_signals_sub"], [m.ack_id for m in received]
        )

    headers = {
        "apikey": supabase_secret_key,
        "Authorization": f"Bearer {supabase_secret_key}",
    }
    async with httpx.AsyncClient(base_url=supabase_url.rstrip("/"), timeout=10.0) as client:
        try:
            resp = await client.get(
                "/rest/v1/aggregator_logs",
                params={"symbol": f"eq.{test_symbol}", "select": "*"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            assert len(rows) == 1, rows
            assert rows[0]["signal_source"] == "RULE"
            assert rows[0]["strategy_signal_id_b"] is None
        finally:
            for sid in inserted_signal_ids:
                await _delete_aggregator_log(
                    supabase_url=supabase_url,
                    supabase_secret_key=supabase_secret_key,
                    signal_id=sid,
                )
            await _delete_strategy_log(
                supabase_url=supabase_url,
                supabase_secret_key=supabase_secret_key,
                signal_id=str(sig_a.signal_id),
            )
