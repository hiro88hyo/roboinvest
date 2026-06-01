from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from strategy_ai.clients.pubsub import PubSubPublisher, PubSubSubscriber
from strategy_ai.clients.supabase import SupabaseWriter
from strategy_ai.config import StrategyAiSettings
from strategy_ai.engine import StrategyAiEngine
from strategy_ai.streaming.runner import StreamRunner
from trade_contracts.enums import Action, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]

SUBSCRIPTION = "strategy-ai-rule-signals"
TOPIC = "strategy-signals-b"
SUPABASE_URL = "https://example.supabase.co"


def _settings() -> StrategyAiSettings:
    return StrategyAiSettings(
        supabase_url=SUPABASE_URL,
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_features=SUBSCRIPTION,
        pubsub_topic_signals=TOPIC,
        pubsub_pull_max_messages=10,
    )


def _features_payload(
    symbol: str = "7203",
    *,
    price: str = "2500",
    ts: str = "2026-04-20T09:00:00+00:00",
) -> dict[str, str]:
    return {"symbol": symbol, "timestamp": ts, "price": price}


def _trigger_payload(
    symbol: str = "7203",
    *,
    price: str = "2500",
    ts: str = "2026-04-20T09:00:00+00:00",
    action: str = "BUY",
    confidence: float = 0.9,
) -> bytes:
    return json.dumps(
        {
            "signal": {
                "source": "RULE",
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "reasoning": "rule trigger",
                "created_at": ts,
            },
            "features": _features_payload(symbol=symbol, price=price, ts=ts),
        }
    ).encode("utf-8")


def _make_pull_response(payloads: list[tuple[str, bytes]]) -> dict[str, Any]:
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


class _FakeAsyncStrategy:
    """非同期の AiStrategy 互換テストダブル。設定可能なシグナルを返す。"""

    def __init__(
        self,
        name: str,
        *,
        action: Action | None = Action.BUY,
        confidence: Decimal = Decimal("0.7"),
        raise_exc: bool = False,
        signal_id: UUID | None = None,
    ) -> None:
        self.name = name
        self._action = action
        self._confidence = confidence
        self._raise = raise_exc
        self._signal_id = signal_id
        self.calls = 0

    async def evaluate(
        self,
        features: ProcessedFeatures,
        state: MutableMapping[str, Any],
    ) -> StrategySignal | None:
        self.calls += 1
        if self._raise:
            raise RuntimeError("boom")
        if self._action is None:
            return None
        return StrategySignal(
            signal_id=self._signal_id or uuid4(),
            source=SignalSource.AI,
            symbol=features.symbol,
            action=self._action,
            confidence=self._confidence,
            reasoning="ai pick",
            created_at=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
        )


class _PubSubRouter:
    """pull / ack / publish を path で振り分けるモック。"""

    def __init__(
        self,
        *,
        pull_batches: list[dict[str, Any]],
        publish_message_id: str = "pub-1",
    ) -> None:
        self.pull_batches = list(pull_batches)
        self.publish_message_id = publish_message_id
        self.published: list[httpx.Request] = []
        self.acked: list[httpx.Request] = []
        self.pulled: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(":pull"):
            self.pulled.append(request)
            body = self.pull_batches.pop(0) if self.pull_batches else {}
            return httpx.Response(200, json=body)
        if path.endswith(":acknowledge"):
            self.acked.append(request)
            return httpx.Response(200, json={})
        if path.endswith(":publish"):
            self.published.append(request)
            return httpx.Response(200, json={"messageIds": [self.publish_message_id]})
        return httpx.Response(404)


class _SupabaseRouter:
    """strategy_logs upsert を受けるモック。"""

    def __init__(self, *, upsert_status: int = 201) -> None:
        self.upsert_status = upsert_status
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path == "/rest/v1/strategy_logs":
            return httpx.Response(self.upsert_status)
        return httpx.Response(404)


async def _with_runner(
    *,
    pubsub_router: _PubSubRouter,
    supabase_router: _SupabaseRouter,
    engine: StrategyAiEngine,
    settings: StrategyAiSettings | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    run_body: Callable[[StreamRunner], Coroutine[None, None, Any]],
) -> Any:
    settings = settings or _settings()
    pubsub_transport = httpx.MockTransport(pubsub_router)
    supabase_transport = httpx.MockTransport(supabase_router)

    async def _noop_sleep(_: float) -> None:
        return None

    async with (
        PubSubSubscriber(
            project_id=settings.pubsub_project_id,
            emulator_host=settings.pubsub_emulator_host,
            transport=pubsub_transport,
        ) as subscriber,
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
            subscriber=subscriber,
            publisher=publisher,
            writer=writer,
            engine=engine,
            settings=settings,
            sleep=sleep or _noop_sleep,
        )
        return await run_body(runner)


async def test_trigger_message_publishes_signal_and_logs() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _trigger_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )

    assert stats.received == 1
    assert stats.features_processed == 1
    assert stats.signals_emitted == 1
    assert stats.acked == 1
    assert stats.parse_errors == 0
    assert stats.process_errors == 0

    assert len(pubsub.published) == 1
    pub_body = json.loads(pubsub.published[0].content.decode())
    msg = pub_body["messages"][0]
    decoded = json.loads(base64.b64decode(msg["data"]).decode("utf-8"))
    assert decoded["symbol"] == "7203"
    assert decoded["action"] == "BUY"
    assert decoded["source"] == "AI"
    assert msg["attributes"] == {"symbol": "7203", "source": "AI"}

    assert len(pubsub.acked) == 1
    assert json.loads(pubsub.acked[0].content.decode()) == {"ackIds": ["a1"]}

    log_posts = [
        r
        for r in supabase.requests
        if r.method == "POST" and r.url.path == "/rest/v1/strategy_logs"
    ]
    assert len(log_posts) == 1
    rows = json.loads(log_posts[0].content.decode())
    assert rows[0]["symbol"] == "7203"
    assert rows[0]["action"] == "BUY"
    assert rows[0]["source"] == "AI"
    assert rows[0]["reasoning"] == "ai pick"


async def test_no_signal_acks_without_publish_or_log_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _trigger_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("silent", action=None)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.INFO, logger="strategy_ai.streaming.runner")

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.features_processed == 1
    assert stats.signals_emitted == 0
    assert stats.acked == 1
    assert pubsub.published == []
    assert all(
        not (r.method == "POST" and r.url.path == "/rest/v1/strategy_logs")
        for r in supabase.requests
    )
    skipped = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_decision_skipped"
    ]
    assert len(skipped) == 1
    record: Any = skipped[0]
    assert record.reason == "no_signal"
    assert record.symbol == "7203"
    assert record.trigger_action == "BUY"
    assert record.trigger_confidence == 0.9


async def test_acknowledges_each_message_without_waiting_for_batch_end() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _make_pull_response(
                [
                    ("a1", _trigger_payload(symbol="7203")),
                    ("a2", _trigger_payload(symbol="6758")),
                ]
            )
        ]
    )
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.received == 2
    assert stats.features_processed == 2
    assert stats.acked == 2
    assert len(pubsub.acked) == 2
    assert json.loads(pubsub.acked[0].content.decode()) == {"ackIds": ["a1"]}
    assert json.loads(pubsub.acked[1].content.decode()) == {"ackIds": ["a2"]}


async def test_strategy_exception_is_isolated_and_message_acked() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _trigger_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine(
        [
            _FakeAsyncStrategy("broken", raise_exc=True),
            _FakeAsyncStrategy("ok", action=Action.BUY),
        ]
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.features_processed == 1
    assert stats.signals_emitted == 1
    assert stats.acked == 1


async def test_malformed_json_is_treated_as_poison_and_acked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", b"not-json")])])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    caplog.set_level(logging.WARNING, logger="strategy_ai.streaming.runner")

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.parse_errors == 1
    assert stats.acked == 1
    assert pubsub.published == []
    parse_failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "ai_trigger_parse_failed"
    ]
    assert len(parse_failures) == 1
    record: Any = parse_failures[0]
    assert record.message_id == "m-a1"
    assert record.reason == "invalid_trigger_payload"
    assert record.subscription == SUBSCRIPTION


async def test_validation_failure_is_acked() -> None:
    bad = json.dumps({"signal": {"symbol": "7203"}}).encode()
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", bad)])])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.parse_errors == 1
    assert stats.acked == 1


async def test_supabase_failure_prevents_ack_for_redelivery() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _trigger_payload())])])
    supabase = _SupabaseRouter(upsert_status=500)
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.process_errors == 1
    assert stats.acked == 0


async def test_empty_pull_triggers_idle_sleep() -> None:
    pubsub = _PubSubRouter(pull_batches=[{}])
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=1)

    await _with_runner(
        pubsub_router=pubsub,
        supabase_router=supabase,
        engine=engine,
        sleep=_sleep,
        run_body=_body,
    )
    assert sleeps == [1.0]
    assert pubsub.acked == []


async def test_run_with_iterations_collects_stats_per_batch() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _make_pull_response([("a1", _trigger_payload())]),
            _make_pull_response([("a2", _trigger_payload(symbol="9984"))]),
        ]
    )
    supabase = _SupabaseRouter()
    engine = StrategyAiEngine([_FakeAsyncStrategy("fake_buy", action=Action.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    results = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert len(results) == 2
    assert results[0].features_processed == 1
    assert results[1].features_processed == 1


def test_parse_trigger_round_trip() -> None:
    from strategy_ai.streaming.runner import _parse_trigger

    trigger = _parse_trigger(_trigger_payload())
    assert trigger is not None
    assert trigger.signal.symbol == "7203"
    assert trigger.signal.source is SignalSource.RULE
    assert trigger.features.symbol == "7203"
    assert trigger.features.price == Decimal("2500")

    assert _parse_trigger(b"not-json") is None
    assert _parse_trigger(b"[1,2,3]") is None
    assert _parse_trigger(json.dumps({"signal": {"symbol": "7203"}}).encode()) is None
