from __future__ import annotations

import base64
import json
from collections.abc import Callable, Coroutine

import httpx
import pytest
from oms_paper.clients.pubsub import PubSubError, PubSubSubscriber

Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def _build_subscriber(handler: Handler) -> PubSubSubscriber:
    return PubSubSubscriber(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=httpx.MockTransport(handler),
    )


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
                            "data": base64.b64encode(b"order-1").decode("ascii"),
                            "attributes": {"symbol": "7203"},
                        },
                    },
                    {
                        "ackId": "a2",
                        "message": {
                            "messageId": "m2",
                            "data": base64.b64encode(b"order-2").decode("ascii"),
                        },
                    },
                ]
            },
        )

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull("oms-paper-paper-orders", max_messages=10)

    assert len(msgs) == 2
    assert msgs[0].ack_id == "a1"
    assert msgs[0].data == b"order-1"
    assert msgs[0].attributes == {"symbol": "7203"}
    assert msgs[1].data == b"order-2"
    assert msgs[1].attributes == {}

    body = json.loads(captured[0].content.decode())
    assert body == {"maxMessages": 10, "returnImmediately": False}
    assert (
        captured[0].url.path
        == "/v1/projects/trade-ai-dev/subscriptions/oms-paper-paper-orders:pull"
    )


async def test_subscriber_pull_returns_empty_when_no_messages() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull("oms-paper-paper-orders", max_messages=5, return_immediately=True)
    assert msgs == []


async def test_subscriber_pull_returns_empty_on_read_timeout() -> None:
    """long-poll deadline 切れの ReadTimeout はアイドル扱いで空配列を返す。"""

    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated long-poll idle", request=request)

    async with _build_subscriber(_handler) as sub:
        msgs = await sub.pull("oms-paper-paper-orders", max_messages=5)
    assert msgs == []


async def test_subscriber_pull_rejects_non_positive_max_messages() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        with pytest.raises(ValueError):
            await sub.pull("oms-paper-paper-orders", max_messages=0)


async def test_subscriber_acknowledge_skips_on_empty_list() -> None:
    called = 0

    async def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(200)

    async with _build_subscriber(_handler) as sub:
        await sub.acknowledge("oms-paper-paper-orders", [])
    assert called == 0


async def test_subscriber_acknowledge_posts_ack_ids() -> None:
    captured: list[httpx.Request] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    async with _build_subscriber(_handler) as sub:
        await sub.acknowledge("oms-paper-paper-orders", ["a1", "a2"])

    assert len(captured) == 1
    assert (
        captured[0].url.path
        == "/v1/projects/trade-ai-dev/subscriptions/oms-paper-paper-orders:acknowledge"
    )
    assert json.loads(captured[0].content.decode()) == {"ackIds": ["a1", "a2"]}


async def test_subscriber_raises_on_4xx() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no sub")

    async with _build_subscriber(_handler) as sub:
        with pytest.raises(PubSubError):
            await sub.pull("oms-paper-paper-orders", max_messages=5)


async def test_subscriber_requires_project_id() -> None:
    with pytest.raises(PubSubError):
        async with PubSubSubscriber(project_id="", emulator_host="pubsub:8085"):
            pass
