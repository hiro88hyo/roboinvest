from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from feature_engine.calendar import (
    is_first_business_day_of_month,
    is_first_business_day_of_week,
    is_tse_business_day,
)
from feature_engine.clients.supabase import SupabaseWriter

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def _default_clock() -> datetime:
    return datetime.now(tz=JST)


class ResetKind(StrEnum):
    """pnl リセットの種類。"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class ResetDecision:
    """ある時点で実施すべきリセットの集合。"""

    kinds: frozenset[ResetKind]


def compute_resets(now_jst: datetime) -> ResetDecision:
    """9:00 JST ちょうどに呼ばれた前提で、当日に発火すべきリセット種別を返す。

    非営業日は何も返さない。月曜 (週の初営業日) で週次、月初営業日で月次を追加する。
    """
    if now_jst.tzinfo is None:
        raise ValueError("now_jst must be timezone-aware (JST)")
    d = now_jst.date()
    if not is_tse_business_day(d):
        return ResetDecision(kinds=frozenset())
    kinds: set[ResetKind] = {ResetKind.DAILY}
    if is_first_business_day_of_week(d):
        kinds.add(ResetKind.WEEKLY)
    if is_first_business_day_of_month(d):
        kinds.add(ResetKind.MONTHLY)
    return ResetDecision(kinds=frozenset(kinds))


_RESET_COLUMNS: dict[ResetKind, str] = {
    ResetKind.DAILY: "daily_pnl",
    ResetKind.WEEKLY: "weekly_pnl",
    ResetKind.MONTHLY: "monthly_pnl",
}


async def apply_pnl_resets(writer: SupabaseWriter, decision: ResetDecision) -> None:
    """`decision` に含まれる pnl カラムを 0 にセットする。

    `is_trading_allowed` は決して変更しない。既存の手動操作を尊重する方針。
    """
    if not decision.kinds:
        logger.info("pnl reset skipped: non-business day or empty decision")
        return
    values: dict[str, object] = {}
    for kind in decision.kinds:
        values[_RESET_COLUMNS[kind]] = 0
    values["updated_at"] = datetime.now(tz=UTC).isoformat()
    await writer.patch(
        "system_status",
        filters={"id": "eq.1"},
        values=values,
    )
    logger.info("pnl reset applied: kinds=%s", sorted(k.value for k in decision.kinds))


def seconds_until_next_fire(now: datetime, *, hour: int = 9, minute: int = 0) -> float:
    """`now` から次の `hour:minute` JST までの秒数を返す。

    `now` が当日の fire 時刻ちょうど (秒/マイクロ秒 0) の場合は 0 を返す。
    過ぎていれば翌日にスキップ。tz-aware 必須。
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_jst = now.astimezone(JST)
    target = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now_jst:
        target = target + timedelta(days=1)
    return (target - now_jst).total_seconds()


async def run_pnl_reset_scheduler(
    writer: SupabaseWriter,
    *,
    clock: Clock = _default_clock,
    sleep: Sleep = asyncio.sleep,
    hour: int = 9,
    minute: int = 0,
    iterations: int | None = None,
) -> None:
    """毎日 `hour:minute` JST に pnl リセットを発火する長時間ループ。

    `iterations=None` で無限ループ。テストでは有限回を指定し、`clock` と `sleep` を
    差し込んで時刻の進行を制御する。`asyncio.CancelledError` はそのまま伝播する。

    内部で次の発火時刻を状態として持ち、発火後は厳密に +1 日進める。
    これにより「発火時刻ちょうどで再突入したときに連続発火する」問題を避ける。

    非営業日は `compute_resets` が空の決定を返すので apply 側で no-op になる。
    """
    now_jst = clock().astimezone(JST)
    next_fire = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_fire < now_jst:
        next_fire = next_fire + timedelta(days=1)

    count = 0
    while iterations is None or count < iterations:
        now_jst = clock().astimezone(JST)
        wait_seconds = max(0.0, (next_fire - now_jst).total_seconds())
        logger.info(
            "pnl reset scheduler: waiting %.1fs until %s",
            wait_seconds,
            next_fire.isoformat(),
        )
        await sleep(wait_seconds)
        fire_time = clock()
        decision = compute_resets(fire_time)
        await apply_pnl_resets(writer, decision)
        next_fire = next_fire + timedelta(days=1)
        count += 1
