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

_PRICE_QUANT = Decimal("1")
_TOKYO = ZoneInfo("Asia/Tokyo")

# kabu State コード (公式仕様準拠)
STATE_WAITING = 1
STATE_PROCESSING = 2
STATE_DONE = 3
STATE_AMENDING = 4
STATE_CANCELLING = 5


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

    判定ルール:
    - 取消中 / 取消確定 (state == 5): ``cancelled``
    - 処理済 (state == 3) かつ cum_qty == order_qty: ``filled``
    - 処理済 (state == 3) かつ 0 < cum_qty < order_qty: ``partial``
    - 処理済 (state == 3) かつ cum_qty == 0: ``cancelled`` (拒否扱い)
    - それ以外 (待機 / 処理中 / 訂正中): ``pending``

    ``fill_price`` は ``Details`` から数量加重平均を ``ROUND_HALF_UP`` で 1 円単位
    に丸めて返す。``Details`` が空で cum_qty > 0 の場合は ``state.price`` を
    フォールバックとして使う (kabu の仕様上は Details が常に入るはずだが防御的)。
    """

    if state.state == STATE_CANCELLING:
        return FillResult(filled_quantity=0, fill_price=None, reason="cancelled")

    if state.state == STATE_DONE:
        if state.cum_qty == 0:
            return FillResult(filled_quantity=0, fill_price=None, reason="cancelled")
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
