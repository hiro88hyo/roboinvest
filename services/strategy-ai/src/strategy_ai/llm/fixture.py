from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

from .base import LLMClient

DEFAULT_FIXTURE_RESPONSES: tuple[str, ...] = (
    json.dumps({"action": "BUY", "confidence": 0.7, "reasoning": "fixture: BUY"}),
    json.dumps({"action": "HOLD", "confidence": 0.5, "reasoning": "fixture: HOLD"}),
    json.dumps({"action": "SELL", "confidence": 0.7, "reasoning": "fixture: SELL"}),
)


@dataclass(slots=True)
class FixtureLLMClient(LLMClient):
    """決定論的なバックテスト用クライアント。

    与えた `responses` を順番に返し、末尾まで行ったら先頭に戻る。
    Phase 2 で `--live` を指定しなかった場合のデフォルト。実ネットワークを叩かないため、
    同じ入力 JSONL を流せば必ず同じ signals JSONL が出る。
    """

    responses: tuple[str, ...] = DEFAULT_FIXTURE_RESPONSES
    _index: int = field(default=0, init=False)

    async def complete(self, prompt: str) -> str:
        if not self.responses:
            from .base import LLMError

            raise LLMError("FixtureLLMClient: responses is empty")
        out = self.responses[self._index % len(self.responses)]
        self._index += 1
        return out

    @classmethod
    def from_iterable(cls, responses: Iterable[str]) -> FixtureLLMClient:
        return cls(responses=tuple(responses))
