"""``streaming.runner`` の単体テスト (sink + replay)。

``run_stream`` 自体は KabuClient / Supabase / Pub/Sub の 3 連 async with を組む
glue 関数なので、ここでは触らず integration test に回す。本ファイルは:

- ``PubSubPublishSink``: TickData / OrderBookSnapshot を JSON で publish し、
  失敗時に ``OSError`` に詰め替えること
- ``run_replay``: JSONL の各行を ``parse_push_message`` に通して sink に流す
  (空行 / コメント / parse error / non-object payload のスキップを確認)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from feeder._testing import make_book_payload, make_tick_payload
from feeder.clients.pubsub import PubSubError
from feeder.streaming.runner import PubSubPublishSink, run_replay
from trade_contracts.market import OrderBookSnapshot, PriceLevel, TickData

JST = timezone(timedelta(hours=9), name="JST")


@dataclass
class _FakePublisher:
    """``PubSubPublisher.publish`` 互換の最小モック。"""

    handler: Callable[[str, bytes, dict[str, str]], Awaitable[str]]
    calls: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)

    async def publish(
        self,
        topic: str,
        *,
        data: bytes,
        attributes: dict[str, str] | None = None,
    ) -> str:
        attrs = dict(attributes or {})
        self.calls.append((topic, data, attrs))
        return await self.handler(topic, data, attrs)


def _tick_record() -> TickData:
    return TickData(
        symbol="7203",
        timestamp=datetime(2026, 4, 25, 9, 0, tzinfo=JST),
        price=Decimal("2500.0"),
        volume=100,
    )


def _book_record() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol="9984",
        timestamp=datetime(2026, 4, 25, 9, 0, tzinfo=JST),
        bids=[PriceLevel(price=Decimal("1000"), quantity=200)],
        asks=[PriceLevel(price=Decimal("1001"), quantity=200)],
    )


async def test_sink_publishes_tick_with_attributes() -> None:
    async def ok(_topic: str, _data: bytes, _attrs: dict[str, str]) -> str:
        return "m-1"

    pub = _FakePublisher(handler=ok)
    sink = PubSubPublishSink(publisher=pub, topic="raw-market-data")  # type: ignore[arg-type]

    await sink(_tick_record())

    assert len(pub.calls) == 1
    topic, data, attrs = pub.calls[0]
    assert topic == "raw-market-data"
    assert attrs == {"symbol": "7203", "kind": "tick"}
    payload = json.loads(data.decode("utf-8"))
    assert payload["symbol"] == "7203"
    # bids/asks が存在しないので feature-engine 側は TickData として解釈する
    assert "bids" not in payload and "asks" not in payload


async def test_sink_publishes_book_with_kind_book() -> None:
    async def ok(_topic: str, _data: bytes, _attrs: dict[str, str]) -> str:
        return "m-2"

    pub = _FakePublisher(handler=ok)
    sink = PubSubPublishSink(publisher=pub, topic="raw-market-data")  # type: ignore[arg-type]

    await sink(_book_record())

    topic, data, attrs = pub.calls[0]
    assert topic == "raw-market-data"
    assert attrs == {"symbol": "9984", "kind": "book"}
    payload = json.loads(data.decode("utf-8"))
    # OrderBookSnapshot は bids / asks フィールドを持つので feature-engine /
    # oms-paper 側はこの存在で型を判別する
    assert "bids" in payload
    assert "asks" in payload


async def test_sink_wraps_pubsub_error_into_oserror() -> None:
    """publish 失敗時は OSError に詰め替えて投げ、session の reconnect に乗せる。"""

    async def fail(_topic: str, _data: bytes, _attrs: dict[str, str]) -> str:
        raise PubSubError("boom")

    pub = _FakePublisher(handler=fail)
    sink = PubSubPublishSink(publisher=pub, topic="raw-market-data")  # type: ignore[arg-type]

    with pytest.raises(OSError) as exc:
        await sink(_tick_record())
    assert "pubsub publish failed" in str(exc.value)
    assert isinstance(exc.value.__cause__, PubSubError)


@dataclass(slots=True)
class _CapturingSink:
    records: list[TickData | OrderBookSnapshot] = field(default_factory=list)

    async def __call__(self, record: TickData | OrderBookSnapshot) -> None:
        self.records.append(record)


async def test_replay_emits_records_for_each_line() -> None:
    sink = _CapturingSink()
    lines = [
        json.dumps(make_tick_payload(symbol="7203", current_price=2500.0)),
        json.dumps(make_book_payload(symbol="9984")),
    ]

    stats = await run_replay(lines, sink)

    assert stats.lines_read == 2
    assert stats.payloads_parsed == 2
    # tick 1 件 + book ペイロード (CurrentPrice 込みで tick + book で 2 件)
    assert stats.records_emitted == len(sink.records)
    assert any(isinstance(r, TickData) and r.symbol == "7203" for r in sink.records)
    assert any(isinstance(r, OrderBookSnapshot) and r.symbol == "9984" for r in sink.records)


async def test_replay_skips_blank_and_comment_lines() -> None:
    sink = _CapturingSink()
    lines = [
        "",
        "   ",
        "# header comment",
        json.dumps(make_tick_payload(symbol="7203")),
    ]

    stats = await run_replay(lines, sink)

    assert stats.lines_read == 4
    assert stats.payloads_parsed == 1
    assert stats.parse_errors == 0
    assert len(sink.records) == 1


async def test_replay_counts_parse_errors_and_continues() -> None:
    sink = _CapturingSink()
    lines = [
        "not-json",
        json.dumps(["array-not-object"]),
        json.dumps(make_tick_payload(symbol="7203")),
    ]

    stats = await run_replay(lines, sink)

    assert stats.lines_read == 3
    assert stats.parse_errors == 2
    assert stats.payloads_parsed == 1
    assert len(sink.records) == 1
