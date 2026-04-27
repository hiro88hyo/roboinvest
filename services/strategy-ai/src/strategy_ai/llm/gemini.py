from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .base import LLMError

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GeminiClient:
    """`google-genai` SDK を使った Gemini 実装。

    JSON 出力を強制 (`response_mime_type=application/json`) し、決定論寄りの
    `temperature=0.0` をデフォルトにする。SDK 例外は `LLMError` に詰め替えて投げる。

    SDK は Lazy import: `client` を未指定で `complete` を呼んだ時に初めて
    `google.genai.Client` を生成する。テストでは `client` に fake を DI する。
    """

    api_key: str
    model: str = "gemini-2.0-flash"
    timeout_seconds: float = 30.0
    temperature: Decimal = Decimal("0.0")
    max_output_tokens: int = 512
    client: Any = None  # google.genai.Client (lazy)

    def _ensure_client(self) -> Any:
        if self.client is not None:
            return self.client
        if not self.api_key:
            raise LLMError("gemini api_key is empty")
        try:
            from google import genai
        except ImportError as exc:
            raise LLMError(f"google-genai not installed: {exc}") from exc
        self.client = genai.Client(api_key=self.api_key)
        return self.client

    async def complete(self, prompt: str) -> str:
        client = self._ensure_client()
        try:
            from google.genai import types
        except ImportError as exc:
            raise LLMError(f"google-genai not installed: {exc}") from exc

        config = types.GenerateContentConfig(
            temperature=float(self.temperature),
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
        )
        try:
            generate: Awaitable[Any] = client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            response = await generate
        except Exception as exc:
            raise LLMError(f"gemini call failed: {type(exc).__name__}: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise LLMError("gemini returned empty text")
        return str(text)
