from __future__ import annotations

from ..config import StrategyAiSettings
from .base import LLMClient, LLMError
from .gemini import GeminiClient
from .openai_compatible import OpenAICompatibleClient


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
    if provider == "openai_compatible":
        return OpenAICompatibleClient(
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
            model=settings.local_llm_model,
            timeout_seconds=settings.local_llm_timeout_seconds,
            temperature=settings.ai_temperature,
            max_concurrency=settings.local_llm_max_concurrency,
        )
    raise LLMError(f"unknown llm_provider: {provider}")
