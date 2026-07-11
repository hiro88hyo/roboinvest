"""OMS Paper のサービス内モデル。

Supabase `positions` / `trades_paper` 行のメモリ表現と、
擬似約定・ポジション遷移の純関数が返す中間構造を定義する。

contracts に上げず本サービス内に閉じている理由:
- ``PaperPosition`` は Supabase 行マッパーであり、書き込み時に列順を Postgres
  に合わせるアダプタが入るため、サービス境界で生のドメインモデルとして
  共有する必要がない。
- ``FillResult`` / ``PositionUpdate`` は擬似約定ロジックの内部 ABI。
  Phase 4 以降で oms-live と共有する判断が出れば contracts/ に昇格する。
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trade_contracts.enums import Side, SignalSource, TradingStyle


class PaperPosition(BaseModel):
    """Supabase ``positions`` テーブルの ``trade_type='paper'`` 行のメモリ表現。"""

    symbol: str
    quantity: int = Field(gt=0)
    entry_price: Decimal
    holding_type: TradingStyle
    target_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    scheduled_exit_date: date | None = None
    # Optional JST close-session time for a fixed-hold exit.  ``None`` retains
    # the legacy behaviour of exiting as soon as the scheduled date is due.
    scheduled_exit_time: time | None = None
    trailing_stop_pct: Decimal | None = None
    opened_at: datetime
    # The immutable first-BUY trade ID for this position.  Legacy rows created
    # before lineage support can be null; new RPC-managed positions cannot.
    position_generation_id: UUID | None = None


class FillResult(BaseModel):
    """``simulate_fill`` の戻り値。不約定時も同じ型で返す。

    ``reason`` は機械可読コード:
    - ``"filled"`` 全量約定
    - ``"partial"`` 部分約定 (板の数量不足)
    - ``"empty_book"`` 反対側の板レベルが 0 件
    - ``"no_liquidity"`` 反対側の板はあるが quantity が 0
    - ``"limit_not_crossed"`` LIMIT が反対側 best price に届かない
    - ``"missing_limit_price"`` LIMIT だが limit_price がない
    - ``"symbol_mismatch"`` order.symbol と book.symbol が一致しない
    """

    filled_quantity: int = Field(ge=0)
    fill_price: Decimal | None = None
    reason: str


class PaperFillRecord(BaseModel):
    """Supabase ``trades_paper`` 行のメモリ表現 (擬似約定 1 件分)。

    ``unified_signal_id`` は通常は ``OrderRequest`` から継承するが、closeout
    (14:50 強制決済) は対応する ``aggregator_logs`` 行を持たないため ``None``。
    Supabase 側の FK は nullable (NOT NULL 制約なし)。
    """

    order_id: UUID
    trade_id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    price: Decimal
    signal_source: SignalSource
    unified_signal_id: UUID | None = None
    executed_at: datetime


class PaperFillOutcome(StrEnum):
    """``oms_paper_apply_fill`` が返すトランザクション結果。"""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class PaperFillReason(StrEnum):
    """RPC の重複判定または約定拒否理由。"""

    ORDER_ID = "order_id"
    UNIFIED_SIGNAL_ID = "unified_signal_id"
    TRADE_ID = "trade_id"
    NO_POSITION_FOR_SELL = "no_position_for_sell"
    OVERSELL = "oversell"
    POSITION_GENERATION_MISMATCH = "position_generation_mismatch"


class PaperPositionAction(StrEnum):
    """RPC が同一トランザクション内で行った position 操作。"""

    INSERTED = "inserted"
    UPDATED = "updated"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


class PaperFillApplyResult(BaseModel):
    """``oms_paper_apply_fill`` の型付き 1 行レスポンス。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: PaperFillOutcome
    reason: PaperFillReason | None
    committed_trade_id: UUID | None
    position_action: PaperPositionAction
    resulting_position: PaperPosition | None

    @model_validator(mode="after")
    def validate_rpc_invariants(self) -> Self:
        """Fail closed when the RPC response contradicts its declared outcome."""

        duplicate_reasons = {
            PaperFillReason.ORDER_ID,
            PaperFillReason.UNIFIED_SIGNAL_ID,
            PaperFillReason.TRADE_ID,
        }
        rejected_reasons = {
            PaperFillReason.NO_POSITION_FOR_SELL,
            PaperFillReason.OVERSELL,
            PaperFillReason.POSITION_GENERATION_MISMATCH,
        }

        if self.outcome is PaperFillOutcome.APPLIED:
            if self.reason is not None or self.committed_trade_id is None:
                raise ValueError("applied fill requires committed_trade_id and no reason")
            if self.position_action is PaperPositionAction.UNCHANGED:
                raise ValueError("applied fill cannot leave the position action unchanged")
        elif self.outcome is PaperFillOutcome.DUPLICATE:
            if self.reason not in duplicate_reasons or self.committed_trade_id is None:
                raise ValueError("duplicate fill requires an idempotency reason and trade id")
            if self.position_action is not PaperPositionAction.UNCHANGED:
                raise ValueError("duplicate fill must leave the position unchanged")
        else:
            if self.reason not in rejected_reasons or self.committed_trade_id is not None:
                raise ValueError("rejected fill requires a rejection reason and no trade id")
            if self.position_action is not PaperPositionAction.UNCHANGED:
                raise ValueError("rejected fill must leave the position unchanged")

        if (
            self.position_action
            in {
                PaperPositionAction.INSERTED,
                PaperPositionAction.UPDATED,
            }
            and self.resulting_position is None
        ):
            raise ValueError("inserted/updated position action requires resulting_position")
        if (
            self.position_action is PaperPositionAction.DELETED
            and self.resulting_position is not None
        ):
            raise ValueError("deleted position action cannot return resulting_position")
        if (
            self.outcome is PaperFillOutcome.REJECTED
            and self.reason is PaperFillReason.NO_POSITION_FOR_SELL
            and self.resulting_position is not None
        ):
            raise ValueError("no_position_for_sell cannot return resulting_position")
        if (
            self.outcome is PaperFillOutcome.REJECTED
            and self.reason
            in {
                PaperFillReason.OVERSELL,
                PaperFillReason.POSITION_GENERATION_MISMATCH,
            }
            and self.resulting_position is None
        ):
            raise ValueError("position-aware rejection must return the authoritative position")
        return self


class PaperStopUpdateOutcome(StrEnum):
    """``oms_paper_update_stop_loss`` の結果。"""

    APPLIED = "applied"
    REJECTED = "rejected"


class PaperStopUpdateReason(StrEnum):
    """Trailing-stop mutation rejection reason."""

    NO_POSITION_FOR_UPDATE = "no_position_for_update"
    POSITION_GENERATION_MISMATCH = "position_generation_mismatch"
    STOP_NOT_RAISED = "stop_not_raised"


class PaperStopUpdateResult(BaseModel):
    """Generation-checked trailing-stop RPC response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: PaperStopUpdateOutcome
    reason: PaperStopUpdateReason | None
    resulting_position: PaperPosition | None

    @model_validator(mode="after")
    def validate_rpc_invariants(self) -> Self:
        if self.outcome is PaperStopUpdateOutcome.APPLIED:
            if self.reason is not None or self.resulting_position is None:
                raise ValueError("applied stop update requires the resulting position")
            return self
        if self.reason is None:
            raise ValueError("rejected stop update requires a reason")
        if (
            self.reason is PaperStopUpdateReason.NO_POSITION_FOR_UPDATE
            and self.resulting_position is not None
        ):
            raise ValueError("missing position rejection cannot return a position")
        if (
            self.reason
            in {
                PaperStopUpdateReason.POSITION_GENERATION_MISMATCH,
                PaperStopUpdateReason.STOP_NOT_RAISED,
            }
            and self.resulting_position is None
        ):
            raise ValueError("position-aware rejection must return the authoritative position")
        return self


class PositionUpdate(BaseModel):
    """``apply_fill`` の戻り値。

    - 新規 / 更新時: ``position`` に新しい ``PaperPosition``、``delete=False``
    - 全決済時: ``position=None``、``delete=True``
    - 約定スキップ時 (no_fill / no_position_for_sell など):
      ``position=existing``、``delete=False``、``error`` に理由
    """

    position: PaperPosition | None = None
    delete: bool = False
    error: str | None = None


class SwingDecision(BaseModel):
    """``evaluate_swing_exit`` の戻り値。

    swing ポジションに対する 3 種類の処置を表す:

    - ``action='exit'``: 損切り / 利確 / 期限超過のいずれかで成行決済する。
      ``reason`` は ``"stop_loss"`` | ``"target"`` | ``"max_hold_days"``。
    - ``action='trail'``: ``stop_loss_price`` を ``new_stop_loss_price`` に
      切り上げるだけ。約定は発生しない。
    - ``action='hold'``: 何もしない (holding_type が swing でない / 全閾値が
      未設定 / トリガー未発火)。

    ``reason`` は ``action='exit'`` のときだけ非 ``None``、
    ``new_stop_loss_price`` は ``action='trail'`` のときだけ非 ``None``。
    """

    action: Literal["exit", "trail", "hold"]
    reason: str | None = None
    new_stop_loss_price: Decimal | None = None
