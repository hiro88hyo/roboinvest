from __future__ import annotations

from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    """LLM 呼び出しの失敗。タイムアウト・5xx・SDK 例外をここに包む。"""


@runtime_checkable
class LLMClient(Protocol):
    """テキスト 1 本を入れてテキスト 1 本を返す最小限のチャットインタフェース。

    JSON モード・response schema・temperature 等の差異は実装側に閉じ込め、
    呼び出し側 (AiStrategy) はプロバイダ非依存にする。
    """

    async def complete(self, prompt: str) -> str: ...
