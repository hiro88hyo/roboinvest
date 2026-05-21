from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine, MutableMapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from strategy_rule.clients.pubsub import PubSubPublisher, PubSubSubscriber
from strategy_rule.clients.supabase import SupabaseWriter
from strategy_rule.config import StrategyRuleSettings
from strategy_rule.engine import StrategyEngine
from strategy_rule.streaming.runner import StreamRunner
from trade_contracts.enums import Side, SignalSource
from trade_contracts.features import ProcessedFeatures
from trade_contracts.signal import StrategySignal

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]

SUBSCRIPTION = "strategy-rule-processed-features"
TOPIC = "strategy-signals-a"
SUPABASE_URL = "https://example.supabase.co"


def _settings() -> StrategyRuleSettings:
    return StrategyRuleSettings(
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
) -> bytes:
    return json.dumps({"symbol": symbol, "timestamp": ts, "price": price}).encode("utf-8")


def _make_pull_response(payloads: list[tuple[str, bytes]]) -> dict[str, Any]:
    """payloads: list of (ack_id, data_bytes)."""
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


class _FakeStrategy:
    """Test strategy that emits a configurable signal per evaluation."""

    def __init__(
        self,
        name: str,
        *,
        action: Side | None = Side.BUY,
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

    def evaluate(
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
            source=SignalSource.RULE,
            symbol=features.symbol,
            action=self._action,
            confidence=self._confidence,
            reasoning=None,
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
    engine: StrategyEngine,
    settings: StrategyRuleSettings | None = None,
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


async def test_features_message_publishes_signal_and_logs() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])

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
    assert decoded["source"] == "RULE"
    assert msg["attributes"] == {"symbol": "7203", "source": "RULE"}

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


async def test_multiple_strategies_emit_one_signal_per_strategy() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine(
        [
            _FakeStrategy("fake_buy", action=Side.BUY),
            _FakeStrategy("fake_sell", action=Side.SELL),
        ]
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.signals_emitted == 2
    assert len(pubsub.published) == 2  # 1 publish per signal
    assert stats.acked == 1  # but only 1 ack for the source message

    log_posts = [
        r
        for r in supabase.requests
        if r.method == "POST" and r.url.path == "/rest/v1/strategy_logs"
    ]
    assert len(log_posts) == 1  # signals batched into a single insert
    rows = json.loads(log_posts[0].content.decode())
    assert {row["action"] for row in rows} == {"BUY", "SELL"}


async def test_no_signal_acks_without_publish_or_log_write() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine([_FakeStrategy("silent", action=None)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

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


async def test_strategy_exception_is_isolated_and_message_acked() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine(
        [
            _FakeStrategy("broken", raise_exc=True),
            _FakeStrategy("ok", action=Side.BUY),
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


async def test_malformed_json_is_treated_as_poison_and_acked() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", b"not-json")])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.parse_errors == 1
    assert stats.acked == 1
    assert pubsub.published == []


async def test_validation_failure_is_acked() -> None:
    bad = json.dumps({"symbol": "7203"}).encode()  # missing required fields
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", bad)])])
    supabase = _SupabaseRouter()
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert stats.parse_errors == 1
    assert stats.acked == 1


async def test_supabase_failure_prevents_ack_for_redelivery() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter(upsert_status=500)
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])

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
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])
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
            _make_pull_response([("a1", _features_payload())]),
            _make_pull_response([("a2", _features_payload(symbol="9984"))]),
        ]
    )
    supabase = _SupabaseRouter()
    engine = StrategyEngine([_FakeStrategy("fake_buy", action=Side.BUY)])

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    results = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, engine=engine, run_body=_body
    )
    assert len(results) == 2
    assert results[0].features_processed == 1
    assert results[1].features_processed == 1


def test_parse_features_round_trip() -> None:
    from strategy_rule.streaming.runner import _parse_features

    feat = _parse_features(_features_payload())
    assert isinstance(feat, ProcessedFeatures)
    assert feat.symbol == "7203"
    assert feat.price == Decimal("2500")

    assert _parse_features(b"not-json") is None
    assert _parse_features(b"[1,2,3]") is None
    assert _parse_features(json.dumps({"symbol": "7203"}).encode()) is None


async def test_strong_rule_signal_also_publishes_ai_trigger() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _features_payload())])])
    supabase = _SupabaseRouter()
    settings = _settings()
    settings.ai_trigger_min_confidence = 0.8
    engine = StrategyEngine(
        [_FakeStrategy("strong_buy", action=Side.BUY, confidence=Decimal("0.9"))]
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub,
        supabase_router=supabase,
        engine=engine,
        settings=settings,
        run_body=_body,
    )

    assert stats.signals_emitted == 1
    assert len(pubsub.published) == 2
    assert pubsub.published[0].url.path.endswith("/topics/strategy-signals-a:publish")
    assert pubsub.published[1].url.path.endswith("/topics/strategy-ai-triggers:publish")

    trigger_body = json.loads(pubsub.published[1].content.decode())
    trigger_msg = trigger_body["messages"][0]
    trigger_payload = json.loads(base64.b64decode(trigger_msg["data"]).decode("utf-8"))
    assert trigger_payload["signal"]["symbol"] == "7203"
    assert trigger_payload["signal"]["confidence"] == 0.9
    assert trigger_payload["features"]["symbol"] == "7203"
    assert trigger_msg["attributes"] == {"symbol": "7203", "source": "RULE", "action": "BUY"}
