from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from .base import LLMError


@dataclass(slots=True)
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0
    temperature: Decimal = Decimal("0")
    seed: int | None = None
    max_concurrency: int = 2
    max_retries: int = 2
    client_factory: Callable[[], httpx.AsyncClient] | None = None
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def complete(self, prompt: str) -> str:
        if not self.base_url:
            raise LLMError("LOCAL_LLM_BASE_URL is empty")
        if not self.model:
            raise LLMError("LOCAL_LLM_MODEL is empty")
        async with self._semaphore:
            return await self._complete_with_retries(prompt)

    async def _complete_with_retries(self, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._complete_once(prompt)
            except LLMError as exc:
                last_error = exc
                if "transient" not in str(exc) or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        raise LLMError(f"openai-compatible call failed: {last_error}")

    async def _complete_once(self, prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(self.temperature),
            "stream": False,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        timeout = httpx.Timeout(self.timeout_seconds)
        if self.client_factory is None:
            client_cm = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        else:
            client_cm = self.client_factory()
        async with client_cm as client:
            try:
                resp = await client.post("/chat/completions", headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                raise LLMError("openai-compatible timeout") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"openai-compatible http error: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise LLMError(f"openai-compatible transient status={resp.status_code}")
        if resp.status_code != 200:
            raise LLMError(
                f"openai-compatible failed: status={resp.status_code} body={resp.text[:200]}"
            )
        body: dict[str, Any] = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("openai-compatible invalid response shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("openai-compatible empty content")
        return content
