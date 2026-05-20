from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .base import LLMError

if TYPE_CHECKING:
    from collections.abc import Awaitable


class DecisionSchema(BaseModel):
    action: str
    confidence: float
    reasoning: str = ""


@dataclass(slots=True)
class GeminiClient:
    """`google-genai` SDK を使った Gemini 実装。"""

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
            response_schema=DecisionSchema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True,
            ),
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

        parsed = getattr(response, "parsed", None)
        normalized = _normalize_structured_response(parsed)
        if normalized is not None:
            return normalized

        text = getattr(response, "text", None)
        if not text:
            raise LLMError("gemini returned empty text")

        # If the SDK couldn't populate `response.parsed`, try normalizing the text once
        # so downstream parser gets a compact JSON string.
        try:
            normalized_text = DecisionSchema.model_validate_json(str(text))
        except Exception:
            return str(text)
        return json.dumps(normalized_text.model_dump(), ensure_ascii=False)


def _normalize_structured_response(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, DecisionSchema):
        return payload.model_dump_json(ensure_ascii=False)
    if isinstance(payload, BaseModel):
        try:
            decision = DecisionSchema.model_validate(payload.model_dump())
        except Exception:
            return None
        return decision.model_dump_json(ensure_ascii=False)
    if isinstance(payload, dict):
        try:
            decision = DecisionSchema.model_validate(payload)
        except Exception:
            return None
        return decision.model_dump_json(ensure_ascii=False)
    return None
