from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trade_contracts.market import TickData

logger = logging.getLogger(__name__)


class TickDecision(StrEnum):
    """`TickSession.observe` の判定結果。"""

    ACCEPT = "accept"
    DUPLICATE = "duplicate"
    STALE = "stale"


@dataclass(slots=True)
class TickSession:
    """同一銘柄の tick 順序保証と `(symbol, timestamp)` ベキ等化を担うインメモリ状態。

    - 同じ `(symbol, timestamp)` が二度来たら `DUPLICATE` として破棄する (Pub/Sub リトライ対策)。
    - 既に処理済みの時点より古い tick は `STALE` として破棄する。
    - 受理した tick の timestamp は銘柄ごとに保持する。

    プロセスを跨いだ永続化は持たない。再起動直後は最初の tick から受け付ける
    (ウォームアップ期間は上位の指標計算層が面倒を見る前提)。
    """

    _last_ts: dict[str, datetime] = field(default_factory=dict)

    def observe(self, tick: TickData) -> TickDecision:
        last = self._last_ts.get(tick.symbol)
        if last is None:
            self._last_ts[tick.symbol] = tick.timestamp
            return TickDecision.ACCEPT
        if tick.timestamp == last:
            logger.debug(
                "tick duplicate dropped: symbol=%s timestamp=%s",
                tick.symbol,
                tick.timestamp.isoformat(),
            )
            return TickDecision.DUPLICATE
        if tick.timestamp < last:
            logger.debug(
                "tick stale dropped: symbol=%s last=%s received=%s",
                tick.symbol,
                last.isoformat(),
                tick.timestamp.isoformat(),
            )
            return TickDecision.STALE
        self._last_ts[tick.symbol] = tick.timestamp
        return TickDecision.ACCEPT

    def last_timestamp(self, symbol: str) -> datetime | None:
        """テスト・デバッグ用: 銘柄ごとの直近受理 timestamp を返す。"""
        return self._last_ts.get(symbol)

    def reset(self, symbol: str | None = None) -> None:
        """状態をクリアする。`symbol` 指定時は当該銘柄のみ。"""
        if symbol is None:
            self._last_ts.clear()
        else:
            self._last_ts.pop(symbol, None)
