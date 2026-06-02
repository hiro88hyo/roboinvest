"""ユニットテスト用のドメインオブジェクト生成ヘルパ。

mypy duplicate-module 衝突回避のため ``conftest.py`` ではなくサービス内モジュー
ルとして提供する (oms-paper / strategy-ai と同方針)。
テスト側は ``from oms_live._testing import ...`` で利用する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradingStyle
from trade_contracts.order import OrderRequest

from oms_live.models import LiveFillRecord, LivePosition

DEFAULT_TS = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)


def make_order_request(
    *,
    symbol: str = "7203",
    side: Side = Side.BUY,
    quantity: int = 100,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
    trade_mode: TradeMode = TradeMode.LIVE,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    unified_signal_id: UUID | None = None,
    stop_loss_price: Decimal | None = None,
    target_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    max_hold_days: int | None = None,
    created_at: datetime | None = None,
) -> OrderRequest:
    return OrderRequest(
        unified_signal_id=unified_signal_id or uuid4(),
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        trade_mode=trade_mode,
        signal_source=signal_source,
        stop_loss_price=stop_loss_price,
        target_price=target_price,
        trailing_stop_pct=trailing_stop_pct,
        max_hold_days=max_hold_days,
        created_at=created_at or DEFAULT_TS,
    )


def make_kabu_order_payload(
    *,
    order_id: str = "20260429000001",
    symbol: str = "7203",
    side: str = "2",
    order_qty: int = 100,
    cum_qty: int = 100,
    state: int = 3,
    order_state: int | None = None,
    price: float = 0.0,
    recv_time: str = "2026-04-29T09:00:00+09:00",
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """kabu ``/orders`` 1 件分のレスポンス JSON を組み立てる。"""

    return {
        "ID": order_id,
        "Symbol": symbol,
        "Side": side,
        "OrderQty": order_qty,
        "CumQty": cum_qty,
        "State": state,
        "OrderState": state if order_state is None else order_state,
        "Price": price,
        "RecvTime": recv_time,
        "Details": details
        if details is not None
        else [
            {
                "ExecutionID": f"E-{order_id}-1",
                "ExecutionTime": "2026-04-29T09:00:01+09:00",
                "Price": 1000,
                "Qty": cum_qty,
            }
        ],
    }


def make_live_position(
    *,
    symbol: str = "7203",
    quantity: int = 100,
    entry_price: Decimal = Decimal("1000"),
    holding_type: TradingStyle = TradingStyle.DAY,
    target_price: Decimal | None = None,
    stop_loss_price: Decimal | None = None,
    max_hold_days: int | None = None,
    trailing_stop_pct: Decimal | None = None,
    opened_at: datetime | None = None,
) -> LivePosition:
    return LivePosition(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        holding_type=holding_type,
        target_price=target_price,
        stop_loss_price=stop_loss_price,
        max_hold_days=max_hold_days,
        trailing_stop_pct=trailing_stop_pct,
        opened_at=opened_at or DEFAULT_TS,
    )


def make_live_fill_record(
    *,
    symbol: str = "7203",
    side: Side = Side.BUY,
    quantity: int = 100,
    price: Decimal = Decimal("1000"),
    signal_source: SignalSource = SignalSource.CONSENSUS,
    unified_signal_id: UUID | None = None,
    executed_at: datetime | None = None,
    trade_id: UUID | None = None,
    order_id: UUID | None = None,
) -> LiveFillRecord:
    kwargs: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "signal_source": signal_source,
        "unified_signal_id": unified_signal_id or uuid4(),
        "executed_at": executed_at or DEFAULT_TS,
    }
    if trade_id is not None:
        kwargs["trade_id"] = trade_id
    if order_id is not None:
        kwargs["order_id"] = order_id
    return LiveFillRecord(**kwargs)
