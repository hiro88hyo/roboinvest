"""Real Pub/Sub emulator + Supabase event-paper pipeline test."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from aggregator.clients.pubsub import PubSubPublisher as AggregatorPublisher
from aggregator.clients.pubsub import PubSubSubscriber as AggregatorSubscriber
from aggregator.clients.supabase import SupabaseWriter as AggregatorWriter
from aggregator.config import AggregatorSettings
from aggregator.consensus import ConsensusConfig, aggregate
from aggregator.streaming.runner import StreamRunner as AggregatorRunner
from gateway.clients.pubsub import PubSubPublisher as GatewayPublisher
from gateway.clients.pubsub import PubSubSubscriber as GatewaySubscriber
from gateway.clients.supabase import SupabaseClient as GatewaySupabaseClient
from gateway.config import GatewaySettings, RiskConfig
from gateway.router import TopicRouting
from gateway.streaming.runner import StreamRunner as GatewayRunner
from oms_paper.calendar import nth_tse_business_day_after
from oms_paper.clients.pubsub import PubSubSubscriber as OmsSubscriber
from oms_paper.clients.supabase import SupabaseClient as OmsSupabaseClient
from oms_paper.config import OmsPaperSettings
from oms_paper.streaming.runner import StreamRunner as OmsRunner
from strategy_rule.clients.pubsub import PubSubPublisher, PubSubSubscriber
from strategy_rule.event_paper._testing import (
    TARGET_DATE,
    make_event_artifact_payload,
    make_event_book,
    make_event_candidate,
)
from strategy_rule.event_paper.artifact import load_event_paper_artifact
from strategy_rule.event_paper.models import EventPaperPublishConfig, claim_json
from strategy_rule.event_paper.publisher import signal_from_claim
from strategy_rule.event_paper.runner import EventPaperPublisherRunner
from strategy_rule.event_paper.supabase import EventPaperSupabaseClient

pytestmark = pytest.mark.integration

RAW_TOPIC = "raw-market-data"
STRATEGY_A_TOPIC = "strategy-signals-a"
STRATEGY_B_TOPIC = "strategy-signals-b"
TRADE_TOPIC = "trade-signals"
PAPER_TOPIC = "paper-orders"
LIVE_TOPIC = "live-orders"


@pytest.fixture
def pubsub_project_id() -> str:
    value = os.environ.get("PUBSUB_PROJECT_ID")
    if not value:
        pytest.skip("PUBSUB_PROJECT_ID not set")
    return value


@pytest.fixture
def pubsub_emulator_host() -> str:
    value = os.environ.get("PUBSUB_EMULATOR_HOST")
    if not value:
        pytest.skip("PUBSUB_EMULATOR_HOST not set")
    return value


@pytest.fixture
def supabase_url() -> str:
    value = os.environ.get("SUPABASE_URL")
    if not value:
        pytest.skip("SUPABASE_URL not set")
    return value


@pytest.fixture
def supabase_secret_key() -> str:
    value = os.environ.get("SUPABASE_SECRET_KEY")
    if not value:
        pytest.skip("SUPABASE_SECRET_KEY not set")
    return value


async def _ensure_subscription(
    client: httpx.AsyncClient,
    *,
    project: str,
    name: str,
    topic: str,
    filter_: str | None = None,
) -> None:
    body: dict[str, Any] = {
        "topic": f"projects/{project}/topics/{topic}",
        "ackDeadlineSeconds": 30,
    }
    if filter_ is not None:
        body["filter"] = filter_
    response = await client.put(f"/v1/projects/{project}/subscriptions/{name}", json=body)
    if response.status_code not in {200, 409}:
        raise RuntimeError(
            f"failed to create subscription {name}: {response.status_code} {response.text[:200]}"
        )


@pytest.fixture
async def event_resources(
    pubsub_project_id: str,
    pubsub_emulator_host: str,
) -> AsyncIterator[dict[str, str]]:
    suffix = uuid4().hex[:10]
    resources = {
        "publisher_raw": f"it-event-publisher-raw-{suffix}",
        "oms_raw": f"it-event-oms-raw-{suffix}",
        "aggregator_a": f"it-event-aggregator-a-{suffix}",
        "aggregator_b": f"it-event-aggregator-b-{suffix}",
        "gateway": f"it-event-gateway-{suffix}",
        "oms_orders": f"it-event-oms-orders-{suffix}",
        "trade_observer": f"it-event-trade-observer-{suffix}",
        "paper_observer": f"it-event-paper-observer-{suffix}",
        "live_observer": f"it-event-live-observer-{suffix}",
    }
    base_url = (
        pubsub_emulator_host if "://" in pubsub_emulator_host else f"http://{pubsub_emulator_host}"
    )
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        await _ensure_subscription(
            client,
            project=pubsub_project_id,
            name=resources["publisher_raw"],
            topic=RAW_TOPIC,
            filter_='attributes.kind = "book"',
        )
        await _ensure_subscription(
            client,
            project=pubsub_project_id,
            name=resources["oms_raw"],
            topic=RAW_TOPIC,
            filter_='attributes.kind = "book"',
        )
        for name, topic in (
            (resources["aggregator_a"], STRATEGY_A_TOPIC),
            (resources["aggregator_b"], STRATEGY_B_TOPIC),
            (resources["gateway"], TRADE_TOPIC),
            (resources["trade_observer"], TRADE_TOPIC),
            (resources["oms_orders"], PAPER_TOPIC),
            (resources["paper_observer"], PAPER_TOPIC),
            (resources["live_observer"], LIVE_TOPIC),
        ):
            await _ensure_subscription(
                client,
                project=pubsub_project_id,
                name=name,
                topic=topic,
            )
    try:
        yield resources
    finally:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            for name in resources.values():
                await client.delete(f"/v1/projects/{pubsub_project_id}/subscriptions/{name}")


def _supabase_headers(key: str, *, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer is not None:
        headers["Prefer"] = prefer
    return headers


async def _read_system_status(*, url: str, key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        response = await client.get(
            "/rest/v1/system_status",
            params={"select": "*", "id": "eq.1", "limit": "1"},
            headers=_supabase_headers(key),
        )
        response.raise_for_status()
        rows = response.json()
    if not rows:
        raise RuntimeError("system_status id=1 is missing")
    row: dict[str, Any] = rows[0]
    return row


async def _write_system_status(*, url: str, key: str, row: dict[str, Any]) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        response = await client.post(
            "/rest/v1/system_status",
            params={"on_conflict": "id"},
            headers=_supabase_headers(
                key,
                prefer="resolution=merge-duplicates,return=minimal",
            ),
            json=[row],
        )
        response.raise_for_status()


async def _read_rows(
    *,
    url: str,
    key: str,
    table: str,
    symbol: str,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        response = await client.get(
            f"/rest/v1/{table}",
            params={"select": "*", "symbol": f"eq.{symbol}"},
            headers=_supabase_headers(key),
        )
        response.raise_for_status()
        payload = response.json()
    assert isinstance(payload, list)
    return payload


async def _cleanup_symbol(*, url: str, key: str, symbol: str) -> None:
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10.0) as client:
        headers = _supabase_headers(key)
        for table in ("trades_paper", "positions", "aggregator_logs", "strategy_logs"):
            await client.delete(
                f"/rest/v1/{table}",
                params={"symbol": f"eq.{symbol}"},
                headers=headers,
            )


async def _run_publisher(
    *,
    artifact_path: Path,
    resources: dict[str, str],
    project: str,
    emulator: str,
    supabase_url: str,
    supabase_key: str,
    clock: datetime,
) -> Any:
    async with (
        PubSubSubscriber(
            project_id=project,
            emulator_host=emulator,
            timeout_seconds=2.0,
        ) as subscriber,
        PubSubPublisher(project_id=project, emulator_host=emulator) as publisher,
        EventPaperSupabaseClient(
            url=supabase_url,
            secret_key=supabase_key,
        ) as supabase,
    ):
        return await EventPaperPublisherRunner(
            artifact=load_event_paper_artifact(artifact_path),
            target_date=TARGET_DATE,
            subscriber=subscriber,
            publisher=publisher,
            supabase=supabase,
            config=EventPaperPublishConfig(
                subscription=resources["publisher_raw"],
                max_pull_batches=20,
                idle_backoff_seconds=0.01,
                seek_before_pull=True,
                allow_test_resource_overrides=True,
            ),
            wall_clock=lambda: clock,
        ).run()


async def test_event_paper_pipeline_is_paper_only_and_idempotent(
    tmp_path: Path,
    event_resources: dict[str, str],
    pubsub_project_id: str,
    pubsub_emulator_host: str,
    supabase_url: str,
    supabase_secret_key: str,
) -> None:
    suffix = uuid4().hex[:8]
    symbol = f"IT{suffix.upper()}"
    cluster_id = f"cluster-{suffix}"
    observation_id = f"obs-{suffix}"
    candidate = make_event_candidate(
        symbol=symbol,
        symbol_name="Integration Test",
        cluster_id=cluster_id,
        observation_id=observation_id,
        execution_candidate_id=f"{cluster_id}:{observation_id}",
    )
    artifact_path = tmp_path / "event-candidates.json"
    artifact_path.write_text(
        json.dumps(make_event_artifact_payload(candidates=[candidate])),
        encoding="utf-8",
    )
    clock = datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    # Keep the synthetic spread within the production execution gate (2 ticks).
    book = make_event_book(symbol=symbol, received_at=clock, best_bid="999.8")
    original_status = await _read_system_status(url=supabase_url, key=supabase_secret_key)
    paper_status = {
        **original_status,
        "trade_mode": "paper",
        "is_trading_allowed": True,
        "daily_pnl": "0",
        "weekly_pnl": "0",
        "monthly_pnl": "0",
    }
    await _cleanup_symbol(url=supabase_url, key=supabase_secret_key, symbol=symbol)
    await _write_system_status(url=supabase_url, key=supabase_secret_key, row=paper_status)

    try:
        async with PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as raw_publisher:
            await raw_publisher.publish(
                RAW_TOPIC,
                data=book.model_dump_json().encode("utf-8"),
                attributes={"kind": "book", "symbol": symbol},
            )

        first_receipt = await _run_publisher(
            artifact_path=artifact_path,
            resources=event_resources,
            project=pubsub_project_id,
            emulator=pubsub_emulator_host,
            supabase_url=supabase_url,
            supabase_key=supabase_secret_key,
            clock=clock,
        )
        second_receipt = await _run_publisher(
            artifact_path=artifact_path,
            resources=event_resources,
            project=pubsub_project_id,
            emulator=pubsub_emulator_host,
            supabase_url=supabase_url,
            supabase_key=supabase_secret_key,
            clock=clock,
        )
        assert first_receipt.execution_profile == "opening_transport_stress_v1"
        assert first_receipt.comparable_to_registered_backtest is False
        assert first_receipt.published[0].signal_id == second_receipt.published[0].signal_id
        assert first_receipt.published[0].observed_ask == Decimal("1000")
        assert second_receipt.published[0].observed_ask == Decimal("1000")

        # The second publisher run reconstructs its receipt from the durable
        # publication checkpoint without sending again. Inject the exact
        # pre-checkpoint signal payload to exercise downstream redelivery.
        loaded_artifact = load_event_paper_artifact(artifact_path)
        event_candidate = loaded_artifact.artifact.candidates[0]
        async with EventPaperSupabaseClient(
            url=supabase_url,
            secret_key=supabase_secret_key,
        ) as event_supabase:
            reasoning = await event_supabase.read_claim_reasoning(
                signal_id=UUID(first_receipt.published[0].signal_id)
            )
        assert reasoning is not None
        checkpointed_claim, _signal = signal_from_claim(
            reasoning,
            candidate=event_candidate,
            artifact_sha256=loaded_artifact.sha256,
        )
        original_claim = checkpointed_claim.model_copy(
            update={"publication_attempt": None, "publication": None}
        )
        _claim, replay_signal = signal_from_claim(
            claim_json(original_claim),
            candidate=event_candidate,
            artifact_sha256=loaded_artifact.sha256,
        )
        async with PubSubPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as strategy_publisher:
            await strategy_publisher.publish(
                STRATEGY_A_TOPIC,
                data=replay_signal.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": replay_signal.symbol,
                    "source": replay_signal.source.value,
                    "routing_intent": replay_signal.routing_intent.value,
                    "strategy_key": replay_signal.strategy_key or "",
                    "candidate_id": replay_signal.candidate_id or "",
                    "mode": "paper",
                },
            )

        aggregator_settings = AggregatorSettings(
            supabase_url=supabase_url,
            supabase_secret_key=supabase_secret_key,
            pubsub_project_id=pubsub_project_id,
            pubsub_emulator_host=pubsub_emulator_host,
            pubsub_subscription_signals_a=event_resources["aggregator_a"],
            pubsub_subscription_signals_b=event_resources["aggregator_b"],
            pubsub_pull_max_messages=1,
            pairing_window_ms=0,
            pairing_bucket_ms=1000,
            min_confidence_rule_only=Decimal("0.5"),
            sell_requires_position=False,
        )
        consensus = ConsensusConfig(
            min_confidence_rule_only=Decimal("0.5"),
            default_holding_type=aggregator_settings.default_holding_type,
        )
        async with (
            AggregatorSubscriber(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
                timeout_seconds=2.0,
            ) as sub_a,
            AggregatorSubscriber(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
                timeout_seconds=2.0,
            ) as sub_b,
            AggregatorPublisher(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
            ) as aggregator_publisher,
            AggregatorWriter(
                url=supabase_url,
                secret_key=supabase_secret_key,
            ) as aggregator_writer,
        ):
            aggregator = AggregatorRunner(
                subscriber_a=sub_a,
                subscriber_b=sub_b,
                publisher=aggregator_publisher,
                writer=aggregator_writer,
                settings=aggregator_settings,
                consensus_config=consensus,
                monotonic=lambda: 1.0,
                wall_clock=lambda: clock,
            )
            aggregate_first = await aggregator.run_once()
            aggregate_second = await AggregatorRunner(
                subscriber_a=sub_a,
                subscriber_b=sub_b,
                publisher=aggregator_publisher,
                writer=aggregator_writer,
                settings=aggregator_settings,
                consensus_config=consensus,
                monotonic=lambda: 1.0,
                wall_clock=lambda: clock,
            ).run_once()
        assert aggregate_first.unified_emitted == 1
        assert aggregate_second.unified_emitted == 0
        assert aggregate_second.duplicates_suppressed == 1

        # The Aggregator stage emitted exactly one business message.  The
        # later direct replay below exercises Gateway's independent durable
        # inbox without treating it as a second Aggregator output.
        async with GatewaySubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=2.0,
        ) as trade_observer:
            trade_messages = await trade_observer.pull(
                event_resources["trade_observer"],
                max_messages=10,
                return_immediately=True,
            )
            assert len(trade_messages) == 1
            await trade_observer.acknowledge(
                event_resources["trade_observer"],
                [message.ack_id for message in trade_messages],
            )

        replay_unified = aggregate(
            [replay_signal],
            config=consensus,
            now=replay_signal.created_at,
        )
        assert replay_unified is not None
        async with GatewayPublisher(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
        ) as gateway_replay_publisher:
            await gateway_replay_publisher.publish(
                TRADE_TOPIC,
                data=replay_unified.model_dump_json().encode("utf-8"),
                attributes={
                    "symbol": replay_unified.symbol,
                    "signal_source": replay_unified.signal_source.value,
                    "routing_intent": replay_unified.routing_intent.value,
                    "strategy_key": replay_unified.strategy_key or "",
                    "candidate_id": replay_unified.candidate_id or "",
                    "event_paper_replay": "true",
                },
            )

        gateway_settings = GatewaySettings(
            supabase_url=supabase_url,
            supabase_secret_key=supabase_secret_key,
            pubsub_project_id=pubsub_project_id,
            pubsub_emulator_host=pubsub_emulator_host,
            pubsub_subscription_trade_signals=event_resources["gateway"],
            pubsub_topic_live_orders=LIVE_TOPIC,
            pubsub_topic_paper_orders=PAPER_TOPIC,
            pubsub_pull_max_messages=1,
            capital=Decimal("1000000"),
            max_risk_per_trade_pct=Decimal("0.02"),
            swing_risk_scale=Decimal("0.5"),
            paper_symbol_order_cooldown_seconds=0,
            day_same_symbol_reentry_block_enabled=False,
            market_regime_gateway_log_only_enabled=False,
            market_regime_gateway_guard_enabled=False,
            market_regime_paper_guard_enabled=False,
            soft_loss_throttle_log_only_enabled=False,
            soft_loss_throttle_guard_enabled=False,
            execution_gate_log_only_enabled=False,
            execution_gate_guard_enabled=True,
            scanner_gate_log_only_enabled=False,
            scanner_gate_guard_enabled=False,
            liquidity_sizing_enabled=True,
            liquidity_missing_daily_max_qty_per_order=100,
        )
        async with (
            GatewaySubscriber(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
                timeout_seconds=2.0,
            ) as gateway_subscriber,
            GatewayPublisher(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
            ) as gateway_publisher,
            GatewaySupabaseClient(
                url=supabase_url,
                secret_key=supabase_secret_key,
            ) as gateway_supabase,
        ):
            gateway = GatewayRunner(
                subscriber=gateway_subscriber,
                publisher=gateway_publisher,
                supabase=gateway_supabase,
                settings=gateway_settings,
                risk_config=RiskConfig.from_settings(gateway_settings),
                routing=TopicRouting(live_topic=LIVE_TOPIC, paper_topic=PAPER_TOPIC),
                wall_clock=lambda: clock,
                monotonic=lambda: 1.0,
            )
            gateway_first = await gateway.run_once()
            gateway_second = await GatewayRunner(
                subscriber=gateway_subscriber,
                publisher=gateway_publisher,
                supabase=gateway_supabase,
                settings=gateway_settings,
                risk_config=RiskConfig.from_settings(gateway_settings),
                routing=TopicRouting(live_topic=LIVE_TOPIC, paper_topic=PAPER_TOPIC),
                wall_clock=lambda: clock,
                monotonic=lambda: 1.0,
            ).run_once()
        assert gateway_first.approved == 1
        assert gateway_second.approved == 0
        assert gateway_second.duplicates_suppressed == 1

        async with OmsSubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=2.0,
        ) as paper_observer:
            paper_messages = await paper_observer.pull(
                event_resources["paper_observer"],
                max_messages=10,
                return_immediately=True,
            )
            assert len(paper_messages) == 1
            await paper_observer.acknowledge(
                event_resources["paper_observer"],
                [message.ack_id for message in paper_messages],
            )

        oms_settings = OmsPaperSettings(
            supabase_url=supabase_url,
            supabase_secret_key=supabase_secret_key,
            pubsub_project_id=pubsub_project_id,
            pubsub_emulator_host=pubsub_emulator_host,
            pubsub_subscription_paper_orders=event_resources["oms_orders"],
            pubsub_subscription_raw_market_data=event_resources["oms_raw"],
            pubsub_pull_max_messages=10,
            raw_book_drain_max_batches=1,
            order_book_require_received_at=True,
            order_book_max_age_seconds=10,
            order_book_max_future_skew_seconds=5,
            paper_day_stop_monitor_enabled=False,
        )
        oms_now = [clock]
        async with (
            OmsSubscriber(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
                timeout_seconds=2.0,
            ) as oms_subscriber,
            OmsSupabaseClient(
                url=supabase_url,
                secret_key=supabase_secret_key,
            ) as oms_supabase,
        ):
            oms = OmsRunner(
                subscriber=oms_subscriber,
                supabase=oms_supabase,
                settings=oms_settings,
                wall_clock=lambda: oms_now[0],
                monotonic=lambda: 1.0,
            )
            oms_stats = await oms.run_once()
            assert oms_stats.orders_pulled == 1
            assert oms_stats.filled == 1
            assert oms_stats.skipped_duplicate == 0

            entry_trades = await _read_rows(
                url=supabase_url,
                key=supabase_secret_key,
                table="trades_paper",
                symbol=symbol,
            )
            entry_positions = await _read_rows(
                url=supabase_url,
                key=supabase_secret_key,
                table="positions",
                symbol=symbol,
            )
            assert len(entry_trades) == 1
            assert entry_trades[0]["side"] == "BUY"
            assert Decimal(str(entry_trades[0]["price"])) == Decimal("1000")
            assert entry_trades[0]["position_generation_id"] == entry_trades[0]["trade_id"]
            assert len(entry_positions) == 1
            position = entry_positions[0]
            assert position["trade_type"] == "paper"
            assert position["holding_type"] == "swing"
            assert position["quantity"] == 100
            assert Decimal(str(position["entry_price"])) == Decimal("1000")
            assert Decimal(str(position["stop_loss_price"])) == Decimal("900.00")
            assert position["max_hold_days"] == 20
            assert position["scheduled_exit_time"] == "15:30:00"
            assert position["position_generation_id"] == entry_trades[0]["trade_id"]
            expected_exit_date = nth_tse_business_day_after(TARGET_DATE, 20)
            assert expected_exit_date is not None
            assert position["scheduled_exit_date"] == expected_exit_date.isoformat()

            exit_clock = datetime(
                expected_exit_date.year,
                expected_exit_date.month,
                expected_exit_date.day,
                6,
                30,
                tzinfo=UTC,
            )
            oms_now[0] = exit_clock
            partial_exit_book = make_event_book(
                symbol=symbol,
                received_at=exit_clock,
                best_bid="1100",
                best_ask="1100.2",
                bid_quantity=40,
            )
            async with PubSubPublisher(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
            ) as raw_publisher:
                await raw_publisher.publish(
                    RAW_TOPIC,
                    data=partial_exit_book.model_dump_json().encode("utf-8"),
                    attributes={"kind": "book", "symbol": symbol},
                )
            partial_warmup = await oms.warm_book_cache()
            assert partial_warmup[:3] == (1, 1, 1)
            partial_exit = await oms.run_opening_swing_max_hold_exits()
            assert partial_exit.partial_exits == 1
            assert partial_exit.closed == 0
            partial_positions = await _read_rows(
                url=supabase_url,
                key=supabase_secret_key,
                table="positions",
                symbol=symbol,
            )
            assert len(partial_positions) == 1
            assert partial_positions[0]["quantity"] == 60

            final_exit_clock = exit_clock + timedelta(seconds=1)
            oms_now[0] = final_exit_clock
            final_exit_book = make_event_book(
                symbol=symbol,
                received_at=final_exit_clock,
                best_bid="1099",
                best_ask="1099.2",
                bid_quantity=100,
            )
            async with PubSubPublisher(
                project_id=pubsub_project_id,
                emulator_host=pubsub_emulator_host,
            ) as raw_publisher:
                await raw_publisher.publish(
                    RAW_TOPIC,
                    data=final_exit_book.model_dump_json().encode("utf-8"),
                    attributes={"kind": "book", "symbol": symbol},
                )
            final_warmup = await oms.warm_book_cache()
            assert final_warmup[:3] == (1, 1, 1)
            final_exit = await oms.run_opening_swing_max_hold_exits()
            assert final_exit.partial_exits == 0
            assert final_exit.closed == 1

        final_positions = await _read_rows(
            url=supabase_url,
            key=supabase_secret_key,
            table="positions",
            symbol=symbol,
        )
        assert final_positions == []
        final_trades = sorted(
            await _read_rows(
                url=supabase_url,
                key=supabase_secret_key,
                table="trades_paper",
                symbol=symbol,
            ),
            key=lambda row: str(row["executed_at"]),
        )
        assert [row["side"] for row in final_trades] == ["BUY", "SELL", "SELL"]
        assert [row["quantity"] for row in final_trades] == [100, 40, 60]
        assert [Decimal(str(row["price"])) for row in final_trades] == [
            Decimal("1000"),
            Decimal("1100"),
            Decimal("1099"),
        ]
        assert {row["position_generation_id"] for row in final_trades} == {
            final_trades[0]["trade_id"]
        }

        async with GatewaySubscriber(
            project_id=pubsub_project_id,
            emulator_host=pubsub_emulator_host,
            timeout_seconds=2.0,
        ) as live_observer:
            live_messages = await live_observer.pull(
                event_resources["live_observer"],
                max_messages=10,
                return_immediately=True,
            )
        assert live_messages == []
    finally:
        await _cleanup_symbol(url=supabase_url, key=supabase_secret_key, symbol=symbol)
        await _write_system_status(
            url=supabase_url,
            key=supabase_secret_key,
            row=original_status,
        )
