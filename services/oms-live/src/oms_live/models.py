"""OMS Live のサービス内モデル。

kabu ``/orders`` の応答を内部形式に正規化する ``KabuOrderState``、
``trades_live`` 1 行に対応する ``LiveFillRecord``、擬似約定と同型の
``FillResult`` を定義する。

contracts に上げない理由は oms-paper と同じ:
- ``LiveFillRecord`` は Supabase 行マッパー
- ``FillResult`` / ``KabuOrderState`` は Phase 2 Runner の内部 ABI
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from trade_contracts.enums import Side, SignalSource, TradingStyle


class ExecutionDetail(BaseModel):
    """kabu ``/orders`` 応答の ``Details[i]`` をそのまま正規化したもの。

    ``rec_type`` は kabu の ``RecType`` 生値:

    - 1: 受付 (発注受付、成行は ``Price=0``)
    - 4: 発注 (取引所への発注、成行は ``Price=0``)
    - 8: 約定 (実約定価格を ``Price`` に持つ。VWAP 計算はこの行のみ使う)

    成行注文の場合 RecType=1/4 行は ``Price=0`` で記録されるため、
    VWAP 計算で除外しないと実約定価格が大きく希釈される
    (2026-05-07 本番実機検証で発覚した実害)。
    """

    execution_id: str
    execution_time: datetime
    price: Decimal
    quantity: int = Field(ge=0)
    rec_type: int = 0


class KabuOrderState(BaseModel):
    """kabu ``/orders`` 応答 1 件分の正規化表現。

    ``state`` / ``order_state`` は kabu の生値 (1〜5) を保持する。
    実機検証 (2026-05-07 本番) で判明した実態に基づく解釈:

    - 1: 待機 (発注待ち)
    - 2: 処理中
    - 3: 処理済 — **取引所に流れた中間状態としても出現**する (Details RecType=1/4)。
      ``CumQty`` が 0 でも約定 Detail (RecType=8) がまだ来ていないだけのことがある。
    - 4: 訂正中
    - 5: **終端ステータス** (約定完了 / 取消完了 / 失効 を包括)。
      公式ドキュメントは「取消中」と表記するが、実機の挙動はライフサイクル
      の終端を表す。
    """

    order_id: str
    symbol: str
    side: Side
    order_qty: int = Field(gt=0)
    cum_qty: int = Field(ge=0)
    state: int
    order_state: int
    price: Decimal
    recv_time: datetime | None = None
    details: list[ExecutionDetail] = Field(default_factory=list)


class FillResult(BaseModel):
    """``to_fill_result`` の戻り値。Phase 2 Runner が Supabase 書込判定に使う。

    ``reason`` は機械可読コード:
    - ``"filled"`` 全量約定 (state==5 or state==3 かつ cum_qty==order_qty)
    - ``"partial"`` 部分約定 (0 < cum_qty < order_qty、state==5 は終端での部分約定、
      state==3 は処理途中の部分約定)
    - ``"pending"`` 未約定 / 中間状態 (cum_qty==0 かつ state != 5)。
      state==3 + cum_qty==0 は「取引所に流れたが約定 Detail 未着」の中間状態
      なので、Runner はここで poll を抜けてはならない。
    - ``"cancelled"`` 取消完了 / 失効 (state==5 かつ cum_qty==0)
    - ``"rejected"`` sendorder の Result != 0 を Runner が組み立てる
    """

    filled_quantity: int = Field(ge=0)
    fill_price: Decimal | None = None
    reason: str


class LiveFillRecord(BaseModel):
    """Supabase ``trades_live`` 行のメモリ表現 (約定 1 件分)。

    ``unified_signal_id`` は通常は ``OrderRequest`` から継承するが、closeout
    (14:50 強制決済) は対応する ``aggregator_logs`` 行を持たないため ``None``。
    Supabase 側の FK は nullable (NOT NULL 制約なし)。

    ``order_id`` は ``OrderRequest.order_id`` をそのまま carry する冪等性キー。
    DB の partial unique index と組み合わせて二重 INSERT を防ぐ。後付け追加の
    ため Optional だが、現行の Runner 経路では必ず付与される。
    """

    trade_id: UUID = Field(default_factory=uuid4)
    order_id: UUID | None = None
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    price: Decimal
    signal_source: SignalSource
    unified_signal_id: UUID | None = None
    executed_at: datetime


class LivePosition(BaseModel):
    """Supabase ``positions`` テーブルの ``trade_type='live'`` 行のメモリ表現。

    フィールド構成は ``PaperPosition`` と同型 (LONG 現物前提、トレーリング/ストップ等を含む)。
    contracts に上げず本サービス内に閉じる方針も oms-paper と同じ。
    """

    symbol: str
    quantity: int = Field(gt=0)
    entry_price: Decimal
    holding_type: TradingStyle
    target_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    max_hold_days: int | None = Field(default=None, ge=1)
    trailing_stop_pct: Decimal | None = None
    opened_at: datetime


class PositionUpdate(BaseModel):
    """``apply_fill`` の戻り値 (oms-paper の同名型と同じ意味)。

    - 新規 / 更新時: ``position`` に新しい ``LivePosition``、``delete=False``
    - 全決済時: ``position=None``、``delete=True``、``realized_pnl`` に確定損益
    - 約定スキップ時 (no_fill / no_position_for_sell など):
      ``position=existing``、``delete=False``、``error`` に理由
    """

    position: LivePosition | None = None
    delete: bool = False
    realized_pnl: Decimal | None = None
    error: str | None = None
