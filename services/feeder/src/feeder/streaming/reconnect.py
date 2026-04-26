"""WebSocket / Supabase 切断時の指数バックオフ計算。純関数のみ。

session.py のループは ``BackoffPolicy.wait_for(attempt)`` だけを呼び、
sleep は呼び出し元 (テストでは ``asyncio.sleep`` を差し替え可能) に委ねる。

仕様:
- ``attempt=1`` で ``initial`` 秒、``attempt=k`` で ``initial * 2**(k-1)``
  をベースに ``max`` で上限クリップ
- ``jitter_ratio`` (デフォルト 0.2) で ``[base*(1-jitter), base*(1+jitter)]``
  の一様乱数を返す。``rng`` を差し込めば決定論にできる (テスト用)
- ``attempt <= 0`` は ``initial`` 扱い (フェイルセーフ)
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """指数バックオフのパラメータ。``compute`` で 1 回分の wait 時間を返す。"""

    initial_seconds: float = 1.0
    max_seconds: float = 60.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.2

    def base_for(self, attempt: int) -> float:
        """jitter なしのベース wait 時間 (attempt=1 で initial)。"""
        if attempt <= 1:
            base = self.initial_seconds
        else:
            base = self.initial_seconds * (self.multiplier ** (attempt - 1))
        return min(base, self.max_seconds)

    def compute(
        self,
        attempt: int,
        *,
        rng: Callable[[], float] = random.random,
    ) -> float:
        """``attempt`` 回目の retry 前に待つ秒数を返す。

        ``rng`` は ``[0, 1)`` の一様乱数。テストでは固定関数を渡す。
        """
        base = self.base_for(attempt)
        if self.jitter_ratio <= 0.0:
            return base
        jitter_span = base * self.jitter_ratio
        # rng() を [-1, 1) に写像
        offset = (rng() * 2.0 - 1.0) * jitter_span
        return max(0.0, base + offset)
