from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.gemini import DecisionSchema, GeminiClient


class _FakeModels:
    def __init__(self, response: object) -> None:
        self._response = response
        self.kwargs: dict[str, Any] | None = None

    async def generate_content(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return self._response


class _FakeAio:
    def __init__(self, response: object) -> None:
        self.models = _FakeModels(response)


class _FakeClient:
    def __init__(self, response: object) -> None:
        self.aio = _FakeAio(response)


@pytest.mark.asyncio
async def test_complete_prefers_parsed_response() -> None:
    response = SimpleNamespace(
        parsed=DecisionSchema(action="BUY", confidence=0.8, reasoning="強い"),
        text='{"action":"SELL"}',
    )
    client = GeminiClient(api_key="dummy", client=_FakeClient(response))

    result = await client.complete("prompt")

    assert json.loads(result) == {
        "action": "BUY",
        "confidence": 0.8,
        "reasoning": "強い",
    }


@pytest.mark.asyncio
async def test_complete_accepts_dict_parsed_response() -> None:
    response = SimpleNamespace(
        parsed={"action": "SELL", "confidence": 0.35, "reasoning": "弱い"},
        text=None,
    )
    client = GeminiClient(api_key="dummy", client=_FakeClient(response))

    result = await client.complete("prompt")

    assert json.loads(result) == {
        "action": "SELL",
        "confidence": 0.35,
        "reasoning": "弱い",
    }


@pytest.mark.asyncio
async def test_complete_sets_json_schema_and_disables_afc() -> None:
    response = SimpleNamespace(
        parsed=DecisionSchema(action="HOLD", confidence=0.5, reasoning=""),
        text='{"action":"HOLD","confidence":0.5,"reasoning":""}',
    )
    fake_client = _FakeClient(response)
    client = GeminiClient(api_key="dummy", client=fake_client)

    await client.complete("prompt")

    assert fake_client.aio.models.kwargs is not None
    config = fake_client.aio.models.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is DecisionSchema
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True


@pytest.mark.asyncio
async def test_complete_raises_on_empty_text_without_parsed() -> None:
    response = SimpleNamespace(parsed=None, text=None)
    client = GeminiClient(api_key="dummy", client=_FakeClient(response))

    with pytest.raises(LLMError):
        await client.complete("prompt")
