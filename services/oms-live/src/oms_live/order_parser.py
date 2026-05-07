"""Phase 1 注文応答パーサ (純関数)。

kabu ``/orders?id=<id>`` の応答 1 件を内部モデル ``KabuOrderState`` に変換し、
その上で ``FillResult`` を抽出する。

kabu の数値フィールド (``Price``, ``Details[*].Price``) は JSON 上 number で
返るため、``str()`` 経由で ``Decimal`` 化して精度落ちを回避する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from trade_contracts.enums import Side

from .models import ExecutionDetail, FillResult, KabuOrderState

_PRICE_QUANT = Decimal("0.01")
_TOKYO = ZoneInfo("Asia/Tokyo")

# kabu State コード (本番 2026-05-07 実機検証で判明した実態に基づく定義)
#
# 公式ドキュメントは ``5=取消中`` と説明するが、本番実機の挙動では:
# - State=5 は **終端ステータス** で、約定完了 / 取消完了 / 失効 を包括する
# - State=3 は「処理済」だけでなく「取引所に流れた中間状態」(Details RecType=1/4)
#   としても返り、その時点で ``CumQty=0`` のことがある (約定 Detail RecType=8 が
#   まだ来ていない)
# 詳細は memory ``oms_live_phase3_findings.md`` の section 3 と
# ``services/oms-live/CLAUDE.md`` の「kabuステーション API の前提」節を参照。
STATE_WAITING = 1
STATE_PROCESSING = 2
STATE_DONE = 3
STATE_AMENDING = 4
STATE_TERMINATED = 5


def parse_order_state(payload: dict[str, Any]) -> KabuOrderState:
    """kabu ``/orders`` の 1 要素を ``KabuOrderState`` に正規化する。

    必須フィールド: ``ID``, ``Symbol``, ``Side``, ``OrderQty``, ``CumQty``,
    ``State``, ``OrderState``。``Details`` は省略可 (約定前は空想定)。
    ``Side`` は kabu 仕様で ``"1"=売``, ``"2"=買``。
    """

    side_raw = str(payload.get("Side", "")).strip()
    if side_raw == "2":
        side = Side.BUY
    elif side_raw == "1":
        side = Side.SELL
    else:
        raise ValueError(f"unexpected kabu Side value: {side_raw!r}")

    return KabuOrderState(
        order_id=str(payload["ID"]),
        symbol=str(payload["Symbol"]),
        side=side,
        order_qty=int(payload["OrderQty"]),
        cum_qty=int(payload.get("CumQty", 0)),
        state=int(payload["State"]),
        order_state=int(payload.get("OrderState", payload["State"])),
        price=_to_decimal(payload.get("Price", 0)),
        recv_time=_parse_kabu_datetime(payload.get("RecvTime")),
        details=[_parse_detail(d) for d in payload.get("Details") or []],
    )


def to_fill_result(state: KabuOrderState) -> FillResult:
    """``KabuOrderState`` から ``FillResult`` を導出する。

    判定ルール (2026-05-07 本番実機検証で再定義):

    - 終端 (state == 5):
      - cum_qty == 0: ``cancelled`` (取消完了 / 失効、約定なし)
      - cum_qty == order_qty: ``filled``
      - 0 < cum_qty < order_qty: ``partial`` (部分約定で終端、残数量は失効)
    - 処理済 (state == 3) — 完全約定が出揃っている場合と中間状態の両方がありうる:
      - cum_qty == 0: ``pending`` — Details RecType=1/4 のみで約定 Detail (RecType=8)
        がまだ来ていない中間状態。Runner はここで poll を抜けてはならない。
      - cum_qty == order_qty: ``filled``
      - 0 < cum_qty < order_qty: ``partial``
    - 待機 / 処理中 / 訂正中 (state in {1, 2, 4}): ``pending``

    ``fill_price`` は ``Details`` から数量加重平均を ``ROUND_HALF_UP`` で
    0.01 円単位に丸めて返す。NTT (¥0.05/¥0.1 刻み) や Toyota (¥0.5 刻み) など
    呼値ティック粒度を超えた丸めを行うと実損益が大きく歪む (2026-05-07 本番
    実機で 1 円丸めにより daily_pnl が実損 -¥5 → -¥100 に歪んだ実害)。
    ``Details`` が空で cum_qty > 0 の場合は ``state.price`` を
    フォールバックとして使う (kabu の仕様上は Details が常に入るはずだが防御的)。
    """

    if state.state == STATE_TERMINATED:
        if state.cum_qty == 0:
            return FillResult(filled_quantity=0, fill_price=None, reason="cancelled")
        price = _vwap_from_details(state.details) or state.price
        if state.cum_qty == state.order_qty:
            return FillResult(filled_quantity=state.cum_qty, fill_price=price, reason="filled")
        return FillResult(filled_quantity=state.cum_qty, fill_price=price, reason="partial")

    if state.state == STATE_DONE and state.cum_qty > 0:
        price = _vwap_from_details(state.details) or state.price
        if state.cum_qty == state.order_qty:
            return FillResult(filled_quantity=state.cum_qty, fill_price=price, reason="filled")
        return FillResult(filled_quantity=state.cum_qty, fill_price=price, reason="partial")

    return FillResult(filled_quantity=state.cum_qty, fill_price=None, reason="pending")


def _parse_detail(payload: dict[str, Any]) -> ExecutionDetail:
    return ExecutionDetail(
        execution_id=str(payload.get("ExecutionID") or payload.get("ID") or ""),
        execution_time=_parse_kabu_datetime(payload.get("ExecutionTime"))
        or datetime.fromtimestamp(0, tz=UTC),
        price=_to_decimal(payload.get("Price", 0)),
        quantity=int(payload.get("Qty", 0)),
    )


def _vwap_from_details(details: list[ExecutionDetail]) -> Decimal | None:
    consumed = [(d.price, d.quantity) for d in details if d.quantity > 0]
    if not consumed:
        return None
    total_qty = sum(qty for _, qty in consumed)
    total_value = sum((price * qty for price, qty in consumed), Decimal("0"))
    raw = total_value / Decimal(total_qty)
    return raw.quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)


def _to_decimal(value: Any) -> Decimal:
    """JSON number / string から ``Decimal`` を作る。``None`` は 0 扱い。"""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_kabu_datetime(value: Any) -> datetime | None:
    """kabu の ``"2026-04-29T09:00:00+09:00"`` 等を ``datetime`` に変換する。

    kabu は ISO 8601 文字列を返すが、稀にタイムゾーン無し (naive) で来る。
    naive の場合は JST と解釈する (kabuステーションは Windows 機の時計を使う前提)。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_TOKYO)
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TOKYO)
    return dt
