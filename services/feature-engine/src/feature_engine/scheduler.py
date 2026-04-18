from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
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
