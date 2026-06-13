from __future__ import annotations

from decimal import Decimal

import pytest
from strategy_ai.config import StrategyAiSettings
from strategy_ai.llm.base import LLMError
from strategy_ai.llm.factory import build_llm_client
from strategy_ai.llm.gemini import GeminiClient


def test_defaults() -> None:
    settings = StrategyAiSettings(_env_file=None)
    assert settings.llm_provider == "gemini"
    assert settings.gemini_model == "gemini-2.0-flash"
    assert settings.ai_min_interval_seconds == 300.0
    assert settings.ai_temperature == Decimal("0.0")
    assert settings.ai_max_output_tokens == 2048
    assert settings.ai_silence_warn_seconds == 3600.0
    assert settings.pubsub_pull_max_messages == 5
    assert settings.pubsub_subscription_features == "strategy-ai-rule-signals"


def test_factory_builds_gemini() -> None:
    settings = StrategyAiSettings(_env_file=None, gemini_api_key="dummy")
    client = build_llm_client(settings)
    assert isinstance(client, GeminiClient)
    assert client.api_key == "dummy"
    assert client.model == "gemini-2.0-flash"


def test_factory_rejects_unknown_provider() -> None:
    settings = StrategyAiSettings(_env_file=None, llm_provider="claude")
    with pytest.raises(LLMError):
        build_llm_client(settings)
