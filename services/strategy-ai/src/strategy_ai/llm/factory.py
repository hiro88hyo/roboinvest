from __future__ import annotations

from ..config import StrategyAiSettings
from .base import LLMClient, LLMError
from .gemini import GeminiClient


def build_llm_client(settings: StrategyAiSettings) -> LLMClient:
    """`settings.llm_provider` に従って LLM クライアントを組み立てる。

    新プロバイダを足すときはここに分岐を増やすだけにする。
    """
    provider = settings.llm_provider.lower().strip()
    if provider == "gemini":
        return GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.gemini_timeout_seconds,
            temperature=settings.ai_temperature,
            max_output_tokens=settings.ai_max_output_tokens,
        )
    raise LLMError(f"unknown llm_provider: {provider}")
