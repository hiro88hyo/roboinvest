"""``clients.pubsub.PubSubPublisher`` の単体テスト。"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from feeder.clients.pubsub import PubSubError, PubSubPublisher


def _build_publisher(handler: httpx.MockTransport) -> PubSubPublisher:
    return PubSubPublisher(
        project_id="trade-ai-dev",
        emulator_host="pubsub:8085",
        transport=handler,
    )


async def test_publisher_sends_base64_payload_and_attributes() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"messageIds": ["m-7"]})

    async with _build_publisher(httpx.MockTransport(handler)) as pub:
        mid = await pub.publish(
            "raw-market-data",
            data=b'{"symbol":"7203","price":"2500"}',
            attributes={"symbol": "7203", "kind": "tick"},
        )

    assert mid == "m-7"
    assert len(captured) == 1
    req = captured[0]
    assert req.url.path == "/v1/projects/trade-ai-dev/topics/raw-market-data:publish"
    body = json.loads(req.content.decode())
    msg = body["messages"][0]
    assert base64.b64decode(msg["data"]) == b'{"symbol":"7203","price":"2500"}'
    assert msg["attributes"] == {"symbol": "7203", "kind": "tick"}


async def test_publisher_raises_on_missing_message_ids() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messageIds": []})

    async with _build_publisher(httpx.MockTransport(handler)) as pub:
        with pytest.raises(PubSubError):
            await pub.publish("raw-market-data", data=b"x")


async def test_publisher_raises_on_4xx() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    async with _build_publisher(httpx.MockTransport(handler)) as pub:
        with pytest.raises(PubSubError):
            await pub.publish("raw-market-data", data=b"x")


async def test_publisher_retries_on_5xx_then_succeeds() -> None:
    hits = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        hits["count"] += 1
        if hits["count"] < 2:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"messageIds": ["m-1"]})

    async with _build_publisher(httpx.MockTransport(handler)) as pub:
        mid = await pub.publish("raw-market-data", data=b"x")

    assert mid == "m-1"
    assert hits["count"] == 2


async def test_publisher_requires_project_id() -> None:
    with pytest.raises(PubSubError):
        async with PubSubPublisher(project_id="", emulator_host="pubsub:8085"):
            pass
