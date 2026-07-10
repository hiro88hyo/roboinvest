from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from aggregator.clients.pubsub import PubSubPublisher, PubSubSubscriber
from aggregator.clients.supabase import SupabaseWriter
from aggregator.config import AggregatorSettings
from aggregator.consensus import ConsensusConfig
from aggregator.streaming.runner import StreamRunner
from trade_contracts.enums import Action, RoutingIntent, SignalSource
from trade_contracts.signal import StrategySignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]

SUB_A = "aggregator-strategy-signals-a"
SUB_B = "aggregator-strategy-signals-b"
TOPIC = "trade-signals"
SUPABASE_URL = "https://example.supabase.co"


def _settings(**overrides: Any) -> AggregatorSettings:
    base: dict[str, Any] = dict(
        supabase_url=SUPABASE_URL,
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_signals_a=SUB_A,
        pubsub_subscription_signals_b=SUB_B,
        pubsub_topic_trade_signals=TOPIC,
        pubsub_pull_max_messages=10,
        pairing_bucket_ms=1000,
        pairing_window_ms=500,
    )
    base.update(overrides)
    return AggregatorSettings(**base)


def _strategy_signal_payload(
    *,
    source: SignalSource = SignalSource.RULE,
    symbol: str = "7203",
    action: Action = Action.BUY,
    confidence: float = 0.7,
    routing_intent: RoutingIntent = RoutingIntent.SYSTEM,
    strategy_key: str | None = None,
    candidate_id: str | None = None,
    created_at: str = "2026-04-20T09:00:00+00:00",
    signal_id: UUID | None = None,
) -> bytes:
    return json.dumps(
        {
            "signal_id": str(signal_id or uuid4()),
            "source": source.value,
            "routing_intent": routing_intent.value,
            "strategy_key": strategy_key,
            "candidate_id": candidate_id,
            "symbol": symbol,
            "action": action.value,
            "confidence": confidence,
            "reasoning": None,
            "created_at": created_at,
        }
    ).encode("utf-8")


def _pull_response(payloads: list[tuple[str, bytes]]) -> dict[str, Any]:
    return {
        "receivedMessages": [
            {
                "ackId": ack_id,
                "message": {
                    "messageId": f"m-{ack_id}",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
            for ack_id, data in payloads
        ]
    }


class _PubSubRouter:
    """Routes pull / ack / publish by sub-name + path. Pull batches are keyed
    per-subscription so A and B can be fed independently."""

    def __init__(
        self,
        *,
        pull_batches_a: list[dict[str, Any]],
        pull_batches_b: list[dict[str, Any]],
    ) -> None:
        self.pull_batches_a = list(pull_batches_a)
        self.pull_batches_b = list(pull_batches_b)
        self.published: list[httpx.Request] = []
        self.acked_a: list[httpx.Request] = []
        self.acked_b: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":pull"):
            if SUB_A in path:
                body = self.pull_batches_a.pop(0) if self.pull_batches_a else {}
            elif SUB_B in path:
                body = self.pull_batches_b.pop(0) if self.pull_batches_b else {}
            else:
                return httpx.Response(404)
            return httpx.Response(200, json=body)
        if path.endswith(":acknowledge"):
            if SUB_A in path:
                self.acked_a.append(request)
            elif SUB_B in path:
                self.acked_b.append(request)
            return httpx.Response(200, json={})
        if path.endswith(":publish"):
            self.published.append(request)
            return httpx.Response(200, json={"messageIds": [f"pub-{len(self.published)}"]})
        return httpx.Response(404)


class _SupabaseRouter:
    def __init__(
        self,
        *,
        upsert_status: int = 201,
        trade_mode: str = "paper",
        position_rows: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.upsert_status = upsert_status
        self.trade_mode = trade_mode
        self.position_rows = list(position_rows or [])
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path == "/rest/v1/aggregator_logs":
            return httpx.Response(self.upsert_status)
        if request.method == "GET" and request.url.path == "/rest/v1/system_status":
            return httpx.Response(200, json=[{"trade_mode": self.trade_mode}])
        if request.method == "GET" and request.url.path == "/rest/v1/positions":
            rows = self.position_rows.pop(0) if self.position_rows else []
            return httpx.Response(200, json=rows)
        return httpx.Response(404)


async def _with_runner(
    *,
    pubsub: _PubSubRouter,
    supabase: _SupabaseRouter,
    settings: AggregatorSettings | None = None,
    monotonic_values: list[float] | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Any:
    settings = settings or _settings()
    pubsub_transport = httpx.MockTransport(pubsub)
    supabase_transport = httpx.MockTransport(supabase)

    async def _noop_sleep(_: float) -> None:
        return None

    clock_values = list(monotonic_values) if monotonic_values is not None else None

    def _monotonic() -> float:
        if clock_values:
            return clock_values.pop(0)
        return 0.0

    def _wall_clock() -> datetime:
        return datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=pubsub_transport,
        ) as sub_a,
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=pubsub_transport,
        ) as sub_b,
        PubSubPublisher(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=pubsub_transport,
        ) as publisher,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            transport=supabase_transport,
        ) as writer,
    ):
        runner = StreamRunner(
            subscriber_a=sub_a,
            subscriber_b=sub_b,
            publisher=publisher,
            writer=writer,
            settings=settings,
            consensus_config=ConsensusConfig(),
            idle_backoff_seconds=1.0,
            sleep=sleep or _noop_sleep,
            monotonic=_monotonic,
            wall_clock=_wall_clock,
        )
        return await run_body(runner)


async def test_paired_a_and_b_emit_consensus_unified_signal() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response([("a1", _strategy_signal_payload(source=SignalSource.RULE))])
        ],
        pull_batches_b=[_pull_response([("b1", _strategy_signal_payload(source=SignalSource.AI))])],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.pulled_a == 1
    assert stats.pulled_b == 1
    assert stats.buckets_emitted == 1
    assert stats.unified_emitted == 1
    assert stats.acked_a == 1
    assert stats.acked_b == 1

    assert len(pubsub.published) == 1
    pub_body = json.loads(pubsub.published[0].content.decode())
    msg = pub_body["messages"][0]
    decoded = json.loads(base64.b64decode(msg["data"]).decode("utf-8"))
    assert decoded["symbol"] == "7203"
    assert decoded["signal_source"] == "CONSENSUS"
    assert msg["attributes"] == {
        "symbol": "7203",
        "signal_source": "CONSENSUS",
        "routing_intent": "SYSTEM",
        "strategy_key": "",
        "candidate_id": "",
    }

    log_posts = [
        r
        for r in supabase.requests
        if r.method == "POST" and r.url.path == "/rest/v1/aggregator_logs"
    ]
    assert len(log_posts) == 1
    rows = json.loads(log_posts[0].content.decode())
    assert rows[0]["signal_source"] == "CONSENSUS"

    assert len(pubsub.acked_a) == 1
    assert len(pubsub.acked_b) == 1
    assert json.loads(pubsub.acked_a[0].content.decode()) == {"ackIds": ["a1"]}
    assert json.loads(pubsub.acked_b[0].content.decode()) == {"ackIds": ["b1"]}


async def test_only_a_within_window_stays_buffered() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response([("a1", _strategy_signal_payload(source=SignalSource.RULE))])
        ],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        monotonic_values=[0.0, 0.0, 0.0],  # pull_a now, pull_b now, drain_ready now
        run_body=_body,
    )

    assert stats.pulled_a == 1
    assert stats.buckets_emitted == 0
    assert stats.unified_emitted == 0
    assert stats.acked_a == 0  # still buffered
    assert pubsub.published == []


async def test_window_expiry_emits_singleton_bucket() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response([("a1", _strategy_signal_payload(source=SignalSource.RULE))]),
            {},
        ],
        pull_batches_b=[{}, {}],
    )
    supabase = _SupabaseRouter()
    settings = _settings(pairing_window_ms=100)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    # tick 1: pull_a monotonic=0, pull_b monotonic=0, drain_ready at 0.0 — too early
    # tick 2: pull_a monotonic=1.0, pull_b monotonic=1.0, drain_ready at 1.0 — > 0.1s window
    monotonic = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]

    results = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        settings=settings,
        monotonic_values=monotonic,
        run_body=_body,
    )

    assert results[0].buckets_emitted == 0
    assert results[1].buckets_emitted == 1
    assert results[1].unified_emitted == 1
    assert results[1].acked_a == 1
    assert results[1].acked_b == 0  # no B ever arrived

    pub_body = json.loads(pubsub.published[0].content.decode())
    decoded = json.loads(base64.b64decode(pub_body["messages"][0]["data"]).decode("utf-8"))
    assert decoded["signal_source"] == "RULE"  # single-source passthrough


async def test_conflict_skip_drops_signal_but_still_acks() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response(
                [("a1", _strategy_signal_payload(source=SignalSource.RULE, action=Action.BUY))]
            )
        ],
        pull_batches_b=[
            _pull_response(
                [("b1", _strategy_signal_payload(source=SignalSource.AI, action=Action.SELL))]
            )
        ],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.buckets_emitted == 1  # bucket fired (fast path, both sources)
    assert stats.unified_emitted == 0  # but consensus returned None
    assert pubsub.published == []
    assert stats.acked_a == 1
    assert stats.acked_b == 1


async def test_sell_without_position_is_dropped_before_publish() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response(
                [("a1", _strategy_signal_payload(source=SignalSource.RULE, action=Action.SELL))]
            )
        ],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter(position_rows=[[]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    results = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        monotonic_values=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
        run_body=_body,
    )

    assert results[1].buckets_emitted == 1
    assert results[1].unified_emitted == 0
    assert pubsub.published == []
    assert len(pubsub.acked_a) == 1
    assert any(req.url.path == "/rest/v1/system_status" for req in supabase.requests)
    assert any(req.url.path == "/rest/v1/positions" for req in supabase.requests)
    assert not any(req.url.path == "/rest/v1/aggregator_logs" for req in supabase.requests)


async def test_sell_with_position_is_published() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response(
                [("a1", _strategy_signal_payload(source=SignalSource.RULE, action=Action.SELL))]
            )
        ],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter(position_rows=[[{"quantity": 100}]])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    results = await _with_runner(
        pubsub=pubsub,
        supabase=supabase,
        monotonic_values=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
        run_body=_body,
    )

    assert results[1].unified_emitted == 1
    assert len(pubsub.published) == 1
    pub_body = json.loads(pubsub.published[0].content.decode())
    decoded = json.loads(base64.b64decode(pub_body["messages"][0]["data"]).decode("utf-8"))
    assert decoded["action"] == "SELL"


async def test_malformed_json_is_acked_immediately() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[_pull_response([("a1", b"not-json")])],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.parse_errors == 1
    assert stats.acked_a == 1  # poison acked on the pull path
    assert pubsub.published == []


async def test_validation_failure_is_acked_immediately() -> None:
    bad = json.dumps({"symbol": "7203"}).encode()  # missing required fields
    pubsub = _PubSubRouter(
        pull_batches_a=[_pull_response([("a1", bad)])],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert stats.parse_errors == 1
    assert stats.acked_a == 1
    assert pubsub.published == []


async def test_empty_pulls_trigger_idle_sleep() -> None:
    pubsub = _PubSubRouter(pull_batches_a=[{}], pull_batches_b=[{}])
    supabase = _SupabaseRouter()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=1)

    await _with_runner(pubsub=pubsub, supabase=supabase, sleep=_sleep, run_body=_body)
    assert sleeps == [1.0]


async def test_flush_drains_remaining_buckets_without_window_wait() -> None:
    pubsub = _PubSubRouter(
        pull_batches_a=[
            _pull_response([("a1", _strategy_signal_payload(source=SignalSource.RULE))])
        ],
        pull_batches_b=[{}],
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        await runner.run_once()  # buffers a1, no emit
        return await runner.flush()

    final = await _with_runner(pubsub=pubsub, supabase=supabase, run_body=_body)

    assert final is not None
    assert final.buckets_emitted == 1
    assert final.unified_emitted == 1
    assert final.acked_a == 1
    assert final.acked_b == 0


def test_parse_signal_round_trip() -> None:
    from aggregator.clients.pubsub import PulledMessage
    from aggregator.streaming.runner import _parse_signal

    good = _strategy_signal_payload(source=SignalSource.RULE)
    msg = PulledMessage(ack_id="a1", message_id="m1", data=good, attributes={})
    parsed = _parse_signal(msg)
    assert isinstance(parsed, StrategySignal)
    assert parsed.symbol == "7203"

    bad = PulledMessage(ack_id="a1", message_id="m1", data=b"not-json", attributes={})
    assert _parse_signal(bad) is None

    array_payload = PulledMessage(ack_id="a1", message_id="m1", data=b"[1,2,3]", attributes={})
    assert _parse_signal(array_payload) is None

    missing = PulledMessage(
        ack_id="a1",
        message_id="m1",
        data=json.dumps({"symbol": "7203"}).encode(),
        attributes={},
    )
    assert _parse_signal(missing) is None
