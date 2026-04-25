"""14:50 JST デイクローズアウト用のスケジューラ。

feature-engine の pnl リセットと同じ「次回発火時刻を内部状態に持つ」方式。
``run_closeout_scheduler`` は 1 日 1 回 ``runner.run_closeout()`` を呼ぶ無限
ループ。テストでは ``iterations`` を有限にし、``clock`` / ``sleep`` を差し替えて
時間進行を制御する。

休場日でも cron 自体は発火するが、``run_closeout`` 内の
``trading_style != day`` チェックで no-op になるため、ここでは曜日判定を持たない
(将来 ``trading_style`` を強制的に swing にしている期間も自然にスキップされる)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .streaming.runner import CloseoutStats, StreamRunner

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def _default_clock() -> datetime:
    return datetime.now(tz=JST)


def parse_hhmm(value: str) -> tuple[int, int]:
    """``HH:MM`` 文字列を ``(hour, minute)`` に分解する。"""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM: {value!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"out of range HH:MM: {value!r}")
    return hour, minute


def seconds_until_next_fire(now: datetime, *, hour: int, minute: int) -> float:
    """``now`` から次の ``hour:minute`` JST までの秒数。``now`` がちょうどなら 0。"""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_jst = now.astimezone(JST)
    target = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now_jst:
        target = target + timedelta(days=1)
    return (target - now_jst).total_seconds()


async def run_closeout_scheduler(
    runner: StreamRunner,
    *,
    hour: int = 14,
    minute: int = 50,
    clock: Clock = _default_clock,
    sleep: Sleep = asyncio.sleep,
    iterations: int | None = None,
) -> list[CloseoutStats]:
    """毎日 ``hour:minute`` JST に ``runner.run_closeout()`` を発火する常駐ループ。

    ``iterations=None`` で無限ループ。テストでは有限回を渡し、``clock`` / ``sleep`` を
    フェイクに差し替えて時間進行を制御する。``asyncio.CancelledError`` はそのまま伝播。
    """
    now_jst = clock().astimezone(JST)
    next_fire = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_fire < now_jst:
        next_fire = next_fire + timedelta(days=1)

    results: list[CloseoutStats] = []
    count = 0
    while iterations is None or count < iterations:
        now_jst = clock().astimezone(JST)
        wait_seconds = max(0.0, (next_fire - now_jst).total_seconds())
        logger.info(
            "closeout scheduler: waiting %.1fs until %s", wait_seconds, next_fire.isoformat()
        )
        await sleep(wait_seconds)
        stats = await runner.run_closeout()
        results.append(stats)
        next_fire = next_fire + timedelta(days=1)
        count += 1
    return results
