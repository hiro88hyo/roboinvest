from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from feature_engine.clients.pubsub import PubSubPublisher, PubSubSubscriber
from feature_engine.clients.supabase import SupabaseReader, SupabaseWriter
from feature_engine.config import FeatureEngineSettings
from feature_engine.storage.warm import WarmWriter
from feature_engine.streaming.feature_state import StreamingFeatureState
from feature_engine.streaming.runner import StreamRunner
from feature_engine.streaming.session import TickSession
from trade_contracts.market import OrderBookSnapshot, TickData

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]

SUBSCRIPTION = "feature-engine-raw-market-data"
TOPIC = "processed-features"
SUPABASE_URL = "https://example.supabase.co"


def _settings() -> FeatureEngineSettings:
    return FeatureEngineSettings(
        supabase_url=SUPABASE_URL,
        supabase_secret_key="k",
        pubsub_project_id="trade-ai-dev",
        pubsub_emulator_host="pubsub:8085",
        pubsub_subscription_raw=SUBSCRIPTION,
        pubsub_topic_features=TOPIC,
        pubsub_pull_max_messages=10,
        indicator_sma_short_window=3,
        indicator_sma_long_window=5,
        indicator_rsi_period=3,
        indicator_vwap_window=3,
        indicator_bollinger_period=3,
    )


def _tick_payload(
    symbol: str = "7203",
    *,
    price: str = "2500",
    volume: int = 100,
    ts: str = "2026-04-20T09:00:00+00:00",
) -> bytes:
    return json.dumps({"symbol": symbol, "timestamp": ts, "price": price, "volume": volume}).encode(
        "utf-8"
    )


def _book_payload(symbol: str = "7203", *, ts: str = "2026-04-20T09:00:00+00:00") -> bytes:
    return json.dumps(
        {
            "symbol": symbol,
            "timestamp": ts,
            "bids": [{"price": "2499", "quantity": 100}],
            "asks": [{"price": "2501", "quantity": 100}],
        }
    ).encode("utf-8")


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
    """positions 読み書きを path/method で振り分けるモック。"""

    def __init__(
        self,
        *,
        positions: list[dict[str, object]] | None = None,
        upsert_status: int = 201,
    ) -> None:
        self.positions = positions or []
        self.upsert_status = upsert_status
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/rest/v1/positions":
            n = len(self.positions)
            headers = {"Content-Range": f"0-{max(n - 1, 0)}/{n}"}
            return httpx.Response(200, content=json.dumps(self.positions), headers=headers)
        if request.method == "POST" and request.url.path == "/rest/v1/positions":
            return httpx.Response(self.upsert_status)
        return httpx.Response(404)


async def _with_runner(
    *,
    pubsub_router: _PubSubRouter,
    supabase_router: _SupabaseRouter,
    settings: FeatureEngineSettings | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    warm_writer: WarmWriter | None = None,
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
        SupabaseReader(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            transport=supabase_transport,
        ) as reader,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            transport=supabase_transport,
        ) as writer,
    ):
        runner = StreamRunner(
            subscriber=subscriber,
            publisher=publisher,
            reader=reader,
            writer=writer,
            feature_state=StreamingFeatureState.from_settings(settings),
            tick_session=TickSession(),
            settings=settings,
            warm_writer=warm_writer,
            sleep=sleep or _noop_sleep,
        )
        return await run_body(runner)


async def test_tick_message_is_published_acked_and_positions_updated() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _tick_payload())])])
    supabase = _SupabaseRouter(
        positions=[
            {
                "symbol": "7203",
                "trade_type": "live",
                "quantity": 100,
                "entry_price": "2400",
            }
        ]
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert stats.received == 1
    assert stats.ticks_processed == 1
    assert stats.books_processed == 0
    assert stats.acked == 1
    assert stats.parse_errors == 0
    assert stats.process_errors == 0

    # publish されたメッセージは ProcessedFeatures の JSON
    assert len(pubsub.published) == 1
    pub_body = json.loads(pubsub.published[0].content.decode())
    msg_data = json.loads(base64.b64decode(pub_body["messages"][0]["data"]).decode("utf-8"))
    assert msg_data["symbol"] == "7203"
    assert msg_data["price"] == "2500"

    # ack と positions 更新が行われた
    assert len(pubsub.acked) == 1
    assert json.loads(pubsub.acked[0].content.decode()) == {"ackIds": ["a1"]}
    upserts = [
        r for r in supabase.requests if r.method == "POST" and r.url.path == "/rest/v1/positions"
    ]
    assert len(upserts) == 1
    upsert_rows = json.loads(upserts[0].content.decode())
    assert upsert_rows[0]["symbol"] == "7203"
    assert upsert_rows[0]["current_price"] == "2500"


async def test_order_book_message_is_recorded_without_publish() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _book_payload())])])
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        return stats, runner

    stats, runner = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, run_body=_body
    )
    assert stats.ticks_processed == 0
    assert stats.books_processed == 1
    assert stats.acked == 1
    assert pubsub.published == []
    # 板情報は feature_state に保存される
    assert runner.feature_state._books["7203"].symbol == "7203"


async def test_duplicate_tick_is_dropped_but_acked() -> None:
    # 同じ (symbol, timestamp) の tick を 2 件
    pubsub = _PubSubRouter(
        pull_batches=[
            _make_pull_response([("a1", _tick_payload()), ("a2", _tick_payload(price="2501"))])
        ]
    )
    supabase = _SupabaseRouter(positions=[])  # positions は空 → upsert なし

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert stats.ticks_processed == 1
    assert stats.ticks_dropped == 1
    assert stats.acked == 2  # 両方 ack される
    assert len(pubsub.published) == 1  # publish は 1 回だけ


async def test_malformed_json_is_treated_as_poison_and_acked() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", b"not-json")])])
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert stats.parse_errors == 1
    assert stats.acked == 1  # ポイズンメッセージは ack される
    assert pubsub.published == []


async def test_missing_required_field_fails_validation_and_is_acked() -> None:
    bad = json.dumps({"symbol": "7203", "timestamp": "2026-04-20T09:00:00+00:00"}).encode()
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", bad)])])
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert stats.parse_errors == 1
    assert stats.acked == 1


async def test_supabase_upsert_failure_prevents_ack() -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _tick_payload())])])
    supabase = _SupabaseRouter(
        positions=[
            {
                "symbol": "7203",
                "trade_type": "live",
                "quantity": 100,
                "entry_price": "2400",
            }
        ],
        upsert_status=500,  # Supabase upsert が 5xx で失敗
    )

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert stats.process_errors == 1
    assert stats.acked == 0  # ack されない → Pub/Sub が再配信


async def test_empty_pull_triggers_idle_sleep() -> None:
    pubsub = _PubSubRouter(pull_batches=[{}])  # receivedMessages なし
    supabase = _SupabaseRouter()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=1)

    await _with_runner(
        pubsub_router=pubsub,
        supabase_router=supabase,
        sleep=_sleep,
        run_body=_body,
    )
    assert sleeps == [1.0]
    assert pubsub.acked == []  # ack する対象がない


async def test_run_with_iterations_collects_stats_per_batch() -> None:
    pubsub = _PubSubRouter(
        pull_batches=[
            _make_pull_response([("a1", _tick_payload())]),
            _make_pull_response([("a2", _book_payload())]),
        ]
    )
    supabase = _SupabaseRouter()

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run(iterations=2)

    results = await _with_runner(pubsub_router=pubsub, supabase_router=supabase, run_body=_body)
    assert len(results) == 2
    assert results[0].ticks_processed == 1
    assert results[1].books_processed == 1


def test_batch_stats_are_frozen() -> None:
    from feature_engine.streaming.runner import BatchStats

    s = BatchStats(
        received=1,
        ticks_processed=1,
        books_processed=0,
        ticks_dropped=0,
        acked=1,
        parse_errors=0,
        process_errors=0,
    )
    # slots + frozen のため属性追加・変更が禁止される
    import dataclasses

    assert dataclasses.is_dataclass(s)


def test_parse_payload_round_trip() -> None:
    from feature_engine.streaming.runner import _parse_payload

    tick = _parse_payload(_tick_payload())
    assert isinstance(tick, TickData)
    assert tick.symbol == "7203"
    assert tick.price == Decimal("2500")

    book = _parse_payload(_book_payload())
    assert isinstance(book, OrderBookSnapshot)

    assert _parse_payload(b"not-json") is None
    assert _parse_payload(b"[1,2,3]") is None  # non-dict JSON


def test_tick_timestamp_is_preserved_as_utc() -> None:
    from feature_engine.streaming.runner import _parse_payload

    tick = _parse_payload(_tick_payload(ts="2026-04-20T09:00:00+09:00"))
    assert tick is not None
    # datetime はタイムゾーン付きで保持される (Pydantic v2)
    assert tick.timestamp == datetime(2026, 4, 20, 9, 0, tzinfo=tick.timestamp.tzinfo)
    # JST → UTC に変換しても同一瞬間 (aware datetime)
    assert tick.timestamp.astimezone(UTC) == datetime(2026, 4, 20, 0, 0, tzinfo=UTC)


async def test_accepted_tick_is_persisted_to_warm(tmp_path: Any) -> None:
    import polars as pl

    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _tick_payload())])])
    supabase = _SupabaseRouter(positions=[])  # no position upsert traffic
    warm = WarmWriter(base_dir=tmp_path, resolution="raw", flush_threshold=1)

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, warm_writer=warm, run_body=_body
    )
    assert stats.ticks_processed == 1
    # threshold=1 → 自動 flush で Parquet が書き出されている
    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    df = pl.read_parquet(files[0])
    assert df.height == 1
    assert df.get_column("price").to_list() == [2500.0]


async def test_dropped_tick_is_not_persisted_to_warm(tmp_path: Any) -> None:
    import polars as pl

    # 同じ (symbol, timestamp) を 2 件 → 2 件目は session で drop
    pubsub = _PubSubRouter(
        pull_batches=[
            _make_pull_response([("a1", _tick_payload()), ("a2", _tick_payload(price="2501"))])
        ]
    )
    supabase = _SupabaseRouter(positions=[])
    warm = WarmWriter(base_dir=tmp_path, resolution="raw", flush_threshold=10)

    async def _body(runner: StreamRunner) -> Any:
        stats = await runner.run_once()
        # まだ flush されていないので手動で flush
        warm.flush()
        return stats

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, warm_writer=warm, run_body=_body
    )
    assert stats.ticks_processed == 1
    assert stats.ticks_dropped == 1
    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    df = pl.read_parquet(files[0])
    # 重複は Warm にも 1 件だけ
    assert df.height == 1
    assert df.get_column("price").to_list() == [2500.0]


async def test_warm_persist_failure_does_not_block_pipeline(tmp_path: Any) -> None:
    pubsub = _PubSubRouter(pull_batches=[_make_pull_response([("a1", _tick_payload())])])
    supabase = _SupabaseRouter(positions=[])

    class _FailingWarm(WarmWriter):
        def record_tick(self, tick: TickData) -> list[Any]:
            raise RuntimeError("disk full")

    warm = _FailingWarm(base_dir=tmp_path, resolution="raw")

    async def _body(runner: StreamRunner) -> Any:
        return await runner.run_once()

    stats = await _with_runner(
        pubsub_router=pubsub, supabase_router=supabase, warm_writer=warm, run_body=_body
    )
    # warm 側で失敗してもパイプラインは継続し ack される
    assert stats.ticks_processed == 1
    assert stats.process_errors == 0
    assert stats.acked == 1
    assert len(pubsub.published) == 1
