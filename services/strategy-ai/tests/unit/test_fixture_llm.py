from __future__ import annotations

import pytest
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.fixture import DEFAULT_FIXTURE_RESPONSES, FixtureLLMClient


@pytest.mark.asyncio
async def test_fixture_round_robin() -> None:
    client = FixtureLLMClient(responses=("a", "b", "c"))
    assert await client.complete("p") == "a"
    assert await client.complete("p") == "b"
    assert await client.complete("p") == "c"
    assert await client.complete("p") == "a"


@pytest.mark.asyncio
async def test_default_responses_cover_all_actions() -> None:
    client = FixtureLLMClient()
    assert len(DEFAULT_FIXTURE_RESPONSES) == 3
    seen = [await client.complete("p") for _ in range(3)]
    for needle in ("BUY", "HOLD", "SELL"):
        assert any(needle in s for s in seen)


@pytest.mark.asyncio
async def test_fixture_empty_responses_raises() -> None:
    client = FixtureLLMClient(responses=())
    with pytest.raises(LLMError):
        await client.complete("p")


def test_from_iterable() -> None:
    client = FixtureLLMClient.from_iterable(["x", "y"])
    assert client.responses == ("x", "y")
