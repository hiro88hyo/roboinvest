from __future__ import annotations

import base64
import json
from collections.abc import Callable, Coroutine

import httpx
import pytest
from aggregator.clients.pubsub import PubSubError, PubSubPublisher, PubSubSubscriber

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _build_publisher(handler: Handler) -> PubSubPublisher:
    return PubSubPublisher(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=httpx.MockTransport(handler),
    )


def _build_subscriber(handler: Handler) -> PubSubSubscriber:
    return PubSubSubscriber(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=httpx.MockTransport(handler),
    )


async def test_publisher_sends_base64_payload_and_returns_message_id() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"messageIds": ["m-1"]})

    async with _build_publisher(_handler) as pub:
        mid = await pub.publish(
            "trade-signals",
            data=b'{"symbol":"7203"}',
            attributes={"source": "aggregator"},
        )

    assert mid == "m-1"
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/v1/projects/trade-ai-dev/topics/trade-signals:publish"
    body = json.loads(req.content.decode())
    msg = body["messages"][0]
    assert base64.b64decode(msg["data"]) == b'{"symbol":"7203"}'
    assert msg["attributes"] == {"source": "aggregator"}


async def test_publisher_raises_on_missing_message_ids() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messageIds": []})

    async with _build_publisher(_handler) as pub:
        with pytest.raises(PubSubError):
            await pub.publish("trade-signals", data=b"x")


async def test_publisher_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="no access")

    async with _build_publisher(_handler) as pub:
        with pytest.raises(PubSubError):
            await pub.publish("trade-signals", data=b"x")


async def test_publisher_requires_project_id() -> None:
    with pytest.raises(PubSubError):
        async with PubSubPublisher(project_id="", emulator_host="pubsub:8085"):
            pass


async def test_subscriber_pull_decodes_messages() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "receivedMessages": [
                    {
                        "ackId": "a1",
                        "message": {
                            "messageId": "m1",
                            "data": base64.b64encode(b"sig-a-1").decode("ascii"),
                            "attributes": {"symbol": "7203", "source": "RULE"},
                        },
                    },
                    {
                        "ackId": "a2",
                        "message": {
                            "messageId": "m2",
                            "data": base64.b64encode(b"sig-b-1").decode("ascii"),
                        },
                    },
                ]
            },
        )

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull("aggregator-strategy-signals-a", max_messages=10)

    assert len(msgs) == 2
    assert msgs[0].ack_id == "a1"
    assert msgs[0].message_id == "m1"
    assert msgs[0].data == b"sig-a-1"
    assert msgs[0].attributes == {"symbol": "7203", "source": "RULE"}
    assert msgs[1].data == b"sig-b-1"
    assert msgs[1].attributes == {}

    body = json.loads(captured[0].content.decode())
    assert body == {"maxMessages": 10, "returnImmediately": False}
    assert (
        captured[0].url.path
        == "/v1/projects/trade-ai-dev/subscriptions/aggregator-strategy-signals-a:pull"
    )


async def test_subscriber_pull_returns_empty_when_no_messages() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull(
            "aggregator-strategy-signals-a", max_messages=5, return_immediately=True
        )
    assert msgs == []


async def test_subscriber_pull_returns_empty_on_read_timeout() -> None:
    """long-poll deadline 切れの ReadTimeout はアイドル扱いで空配列を返す。"""

    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated long-poll idle", request=request)

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull("aggregator-strategy-signals-a", max_messages=5)
    assert msgs == []


async def test_subscriber_pull_rejects_non_positive_max_messages() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        with pytest.raises(ValueError):
            await sub.pull("aggregator-strategy-signals-a", max_messages=0)


async def test_subscriber_acknowledge_skips_on_empty_list() -> None:
    called = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200)

    async with _build_subscriber(_handler) as sub:
        await sub.acknowledge("aggregator-strategy-signals-a", [])
    assert called == 0


async def test_subscriber_acknowledge_posts_ack_ids() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        await sub.acknowledge("aggregator-strategy-signals-a", ["a1", "a2"])

    assert len(captured) == 1
    assert (
        captured[0].url.path
        == "/v1/projects/trade-ai-dev/subscriptions/aggregator-strategy-signals-a:acknowledge"
    )
    assert json.loads(captured[0].content.decode()) == {"ackIds": ["a1", "a2"]}


async def test_subscriber_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no sub")

    async with _build_subscriber(_handler) as sub:
        with pytest.raises(PubSubError):
            await sub.pull("aggregator-strategy-signals-a", max_messages=5)
