"""Phase 3 e2e: 偽 kabu WS + ``PubSubPublisher`` + ``FeederSession`` の結合確認。

実際の kabuステーション機 / Pub/Sub エミュレータには接続せず、
- kabu 側: ``KabuStreamingClient`` Protocol を満たす in-memory fake
- Pub/Sub: ``httpx.MockTransport`` でエミュレータ相当の HTTP 応答を捏造
で組む。狙いは「Phase 2 の session 状態機械 + Phase 3 の publisher sink」が
噛み合っており、PUSH JSON が ``raw-market-data`` に対応する HTTP publish 呼び
出しまで到達することの保証。
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from feeder._testing import make_book_payload, make_tick_payload
from feeder.clients.pubsub import PubSubPublisher
from feeder.clients.supabase import WatchlistRow
from feeder.streaming.reconnect import BackoffPolicy
from feeder.streaming.runner import build_publish_sink, run_replay
from feeder.streaming.session import FeederSession


class _FakeWS:
    def __init__(self, messages: list[str | bytes]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> _FakeWS:
        return self

    async def __anext__(self) -> str | bytes:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


@dataclass(slots=True)
class _FakeKabu:
    ws_messages: list[list[str | bytes]] = field(default_factory=list)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def ensure_token(self) -> str:
        self.calls.append(("ensure_token", None))
        return "tok-1"

    async def register(self, symbols: Any) -> dict[str, Any]:
        self.calls.append(("register", list(symbols)))
        return {}

    async def unregister(self, symbols: Any) -> dict[str, Any]:
        self.calls.append(("unregister", list(symbols)))
        return {}

    async def unregister_all(self) -> dict[str, Any]:
        self.calls.append(("unregister_all", None))
        return {}

    def invalidate_token(self) -> None:
        self.calls.append(("invalidate_token", None))

    @asynccontextmanager
    async def connect_websocket(self) -> AsyncIterator[_FakeWS]:
        self.calls.append(("connect_websocket", None))
        msgs = self.ws_messages.pop(0) if self.ws_messages else []
        yield _FakeWS(msgs)


async def _watchlist_feed(
    snapshots: list[list[WatchlistRow]],
) -> AsyncIterator[list[WatchlistRow]]:
    for snap in snapshots:
        yield snap


def _row(symbol: str) -> WatchlistRow:
    return WatchlistRow(symbol=symbol, valid_date=date(2026, 4, 25))


def _publish_handler(captured: list[httpx.Request]) -> httpx.MockTransport:
    """``raw-market-data:publish`` だけを受ける固定ハンドラ。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        counter["n"] += 1
        return httpx.Response(200, json={"messageIds": [f"m-{counter['n']}"]})

    return httpx.MockTransport(handler)


async def test_session_publishes_tick_and_book_via_pubsub_sink() -> None:
    captured: list[httpx.Request] = []
    transport = _publish_handler(captured)

    tick_msg = json.dumps(make_tick_payload(symbol="7203", current_price=2500.0))
    # book-only メッセージにする (CurrentPrice を抜く)
    book_payload = make_book_payload(symbol="9984")
    book_payload.pop("CurrentPrice", None)
    book_msg = json.dumps(book_payload)

    kabu = _FakeKabu(ws_messages=[[tick_msg, book_msg]])

    async def no_sleep(_: float) -> None:
        return None

    async with PubSubPublisher(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=transport,
    ) as publisher:
        sink = build_publish_sink(publisher, topic="raw-market-data")
        session = FeederSession(
            kabu=kabu,
            watchlist_feed=_watchlist_feed([[_row("7203"), _row("9984")]]),
            default_exchange=1,
            sink=sink,
            backoff=BackoffPolicy(jitter_ratio=0.0),
            sleep=no_sleep,
        )
        results = await session.run(iterations=1)

    assert len(results) == 1
    stats = results[0]
    assert stats.messages_received == 2
    # tick メッセージ: TickData 1 件、book メッセージ: OrderBookSnapshot 1 件
    assert stats.records_emitted == 2

    # publish 呼び出しは 2 件 (tick + book)。それぞれ topic が raw-market-data
    assert len(captured) == 2
    for req in captured:
        assert req.url.path == "/v1/projects/trade-ai-dev/topics/raw-market-data:publish"

    # 1 件目は TickData (bids/asks なし) + symbol=7203
    body0 = json.loads(captured[0].content.decode())
    msg0 = body0["messages"][0]
    payload0 = json.loads(base64.b64decode(msg0["data"]).decode("utf-8"))
    assert payload0["symbol"] == "7203"
    assert "bids" not in payload0
    assert msg0["attributes"] == {"symbol": "7203", "kind": "tick"}

    # 2 件目は OrderBookSnapshot (bids/asks あり) + symbol=9984
    body1 = json.loads(captured[1].content.decode())
    msg1 = body1["messages"][0]
    payload1 = json.loads(base64.b64decode(msg1["data"]).decode("utf-8"))
    assert payload1["symbol"] == "9984"
    assert "bids" in payload1
    assert "asks" in payload1
    assert msg1["attributes"] == {"symbol": "9984", "kind": "book"}


async def _blocking_watchlist_feed(
    initial: list[WatchlistRow],
) -> AsyncIterator[list[WatchlistRow]]:
    """初回 1 件 yield した後は永遠にブロックする feed。

    `_consume_watchlist` が cycle 終了より先に走り抜けると `watchlist_exhausted`
    で抜けてしまうので、WS 側のイベントを観測したいテストで使う。
    """
    yield initial
    # 次の next を呼ぶと永遠に待つ → cycle 終了は WS 側のみが起因になる
    await asyncio.Event().wait()


async def test_publish_failure_triggers_session_backoff() -> None:
    """publish 失敗時に sink が OSError を投げ、session が backoff してリトライすること。"""

    publish_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        publish_calls["count"] += 1
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)

    kabu = _FakeKabu(
        ws_messages=[
            [json.dumps(make_tick_payload(symbol="7203"))],  # 1 サイクル目: publish 失敗
            [],  # 2 サイクル目: WS 即終了
        ]
    )

    sleep_log: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_log.append(seconds)

    async with PubSubPublisher(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=transport,
    ) as publisher:
        sink = build_publish_sink(publisher, topic="raw-market-data")
        session = FeederSession(
            kabu=kabu,
            watchlist_feed=_blocking_watchlist_feed([_row("7203")]),
            default_exchange=1,
            sink=sink,
            backoff=BackoffPolicy(initial_seconds=0.5, jitter_ratio=0.0),
            sleep=fake_sleep,
        )
        results = await session.run(iterations=2)

    assert len(results) == 2
    # 1 サイクル目: publish 失敗で OSError (= "connection_error" として扱われる)
    assert results[0].ended_reason == "connection_error"
    # 2 サイクル目: 1 サイクル目で watchlist iterator が cancel されて閉じているため
    # _next_watchlist_snapshot が StopAsyncIteration を投げて watchlist_exhausted で抜ける
    assert results[1].ended_reason == "watchlist_exhausted"
    # 失敗 → backoff sleep が 1 回入る (initial 0.5s)
    assert sleep_log == [0.5]


async def test_replay_publishes_to_pubsub_sink() -> None:
    """JSONL replay → PubSubPublishSink → mock transport の経路確認。"""
    captured: list[httpx.Request] = []
    transport = _publish_handler(captured)

    tick_payload = make_tick_payload(symbol="7203", current_price=2500.0)
    book_payload = make_book_payload(symbol="9984")
    book_payload.pop("CurrentPrice", None)

    lines = [
        "# this is a comment line",
        "",
        json.dumps(tick_payload),
        json.dumps(book_payload),
    ]

    async with PubSubPublisher(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=transport,
    ) as publisher:
        sink = build_publish_sink(publisher, topic="raw-market-data")
        stats = await run_replay(lines, sink)

    assert stats.lines_read == 4
    assert stats.payloads_parsed == 2
    assert stats.records_emitted == 2
    assert stats.parse_errors == 0
    assert len(captured) == 2
    symbols = []
    for req in captured:
        body = json.loads(req.content.decode())
        msg = body["messages"][0]
        attrs = msg.get("attributes") or {}
        symbols.append(attrs.get("symbol"))
    assert symbols == ["7203", "9984"]
