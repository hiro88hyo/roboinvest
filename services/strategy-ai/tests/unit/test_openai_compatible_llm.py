from __future__ import annotations

import httpx
import pytest
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.openai_compatible import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_openai_compatible_uses_mock_transport_without_network() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleClient(
        base_url="https://local.test",
        api_key="secret",
        model="local-model",
        client_factory=lambda: httpx.AsyncClient(
            base_url="https://local.test",
            transport=transport,
        ),
    )

    assert await client.complete("prompt") == '{"ok": true}'
    assert requests[0].url.path == "/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_openai_compatible_retries_transient_status() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleClient(
        base_url="https://local.test",
        api_key="",
        model="local-model",
        max_retries=1,
        client_factory=lambda: httpx.AsyncClient(
            base_url="https://local.test",
            transport=transport,
        ),
    )

    assert await client.complete("prompt") == '{"ok": true}'
    assert calls == 2


@pytest.mark.asyncio
async def test_openai_compatible_invalid_response_fails_closed() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    client = OpenAICompatibleClient(
        base_url="https://local.test",
        api_key="",
        model="local-model",
        client_factory=lambda: httpx.AsyncClient(
            base_url="https://local.test",
            transport=transport,
        ),
    )

    with pytest.raises(LLMError):
        await client.complete("prompt")
