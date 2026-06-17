"""Phase 2 backtest runner.

Drives the Phase 1 pure-function stack over a stream of ``UnifiedTradeSignal``
and produces approved ``OrderRequest`` + rejection records. The
``KillSwitchState`` and ``positions`` snapshot are held constant for the whole
run — no pnl simulation (that belongs to OMS Paper's backtest instead).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from trade_contracts.enums import Action
from trade_contracts.order import OrderRequest
from trade_contracts.risk import KillSwitchState
from trade_contracts.signal import UnifiedTradeSignal

from ..config import RiskConfig
from ..order_builder import build
from ..validator import validate

logger = logging.getLogger(__name__)


class RejectionRecord(BaseModel):
    """Gateway が拒否したシグナルの記録 (backtest 出力用)。"""

    unified_signal_id: UUID
    symbol: str
    action: Action
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    approved: list[OrderRequest]
    rejected: list[RejectionRecord]

    @property
    def input_count(self) -> int:
        return len(self.approved) + len(self.rejected)

    @property
    def approved_count(self) -> int:
        return len(self.approved)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def run_backtest(
    signals: Iterable[UnifiedTradeSignal],
    *,
    state: KillSwitchState,
    risk_config: RiskConfig,
    entry_price: Decimal,
    positions: dict[str, int] | None = None,
    now: datetime | None = None,
) -> BacktestSummary:
    """1 パスで全シグナルを検証し、approved / rejected に振り分ける。

    * ``state`` と ``positions`` は不変。pnl や建玉変化のシミュレートはしない。
    * ``entry_price`` は全シグナル共通のフラット値。
    * ``now`` を指定すると全 approved OrderRequest の ``created_at`` が固定され、
      再現可能な出力になる。未指定時は実行時刻。
    """
    positions_map = positions or {}
    stamp = now or datetime.now(UTC)

    approved: list[OrderRequest] = []
    rejected: list[RejectionRecord] = []

    for signal in signals:
        check = validate(
            signal=signal,
            state=state,
            risk_config=risk_config,
            entry_price=entry_price,
            existing_long_quantity=positions_map.get(signal.symbol),
        )
        if check.passed and check.adjusted_quantity is not None and check.adjusted_quantity > 0:
            approved.append(
                build(
                    signal=signal,
                    quantity=check.adjusted_quantity,
                    trade_mode=state.trade_mode,
                    entry_price=entry_price if signal.action is Action.BUY else None,
                    default_stop_loss_spread_pct=risk_config.default_stop_loss_spread_pct,
                    created_at=stamp,
                )
            )
        else:
            rejected.append(
                RejectionRecord(
                    unified_signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    action=signal.action,
                    reason=check.reason or "unknown",
                    created_at=signal.created_at,
                )
            )

    summary = BacktestSummary(approved=approved, rejected=rejected)
    logger.info(
        "backtest done: inputs=%d approved=%d rejected=%d",
        summary.input_count,
        summary.approved_count,
        summary.rejected_count,
    )
    return summary
