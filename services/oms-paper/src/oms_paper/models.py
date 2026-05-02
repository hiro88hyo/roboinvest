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

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
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
    trailing_stop_pct: Decimal | None = None
    opened_at: datetime


class FillResult(BaseModel):
    """``simulate_fill`` の戻り値。不約定時も同じ型で返す。

    ``reason`` は機械可読コード:
    - ``"filled"`` 全量約定
    - ``"partial"`` 部分約定 (板の数量不足)
    - ``"empty_book"`` 反対側の板レベルが 0 件
    - ``"no_liquidity"`` 反対側の板はあるが quantity が 0
    - ``"limit_not_supported"`` Phase 1 では LIMIT を扱わない
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

    trade_id: UUID = Field(default_factory=uuid4)
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    price: Decimal
    signal_source: SignalSource
    unified_signal_id: UUID | None = None
    executed_at: datetime


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
