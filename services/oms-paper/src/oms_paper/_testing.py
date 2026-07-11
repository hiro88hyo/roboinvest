"""ユニットテスト用のドメインオブジェクト生成ヘルパ。

`conftest.py` を置くと mypy の重複モジュール検出で他サービスと衝突するため、
パッケージ内のプライベートモジュールとしてファクトリ関数を提供する。
テスト側は ``from oms_paper._testing import ...`` で利用する。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from trade_contracts.enums import (
    OrderType,
    RoutingIntent,
    Side,
    SignalSource,
    TradeMode,
    TradingStyle,
)
from trade_contracts.market import OrderBookSnapshot, PriceLevel
from trade_contracts.order import OrderRequest

from oms_paper.models import PaperPosition

DEFAULT_TS = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)


def make_order_request(
    *,
    symbol: str = "7203",
    side: Side = Side.BUY,
    quantity: int = 100,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
    trade_mode: TradeMode = TradeMode.PAPER,
    signal_source: SignalSource = SignalSource.CONSENSUS,
    unified_signal_id: UUID | None = None,
    routing_intent: RoutingIntent = RoutingIntent.SYSTEM,
    strategy_key: str | None = None,
    candidate_id: str | None = None,
    holding_type: TradingStyle | None = None,
    stop_loss_price: Decimal | None = None,
    stop_loss_pct: Decimal | None = None,
    target_price: Decimal | None = None,
    trailing_stop_pct: Decimal | None = None,
    max_hold_days: int | None = None,
    scheduled_exit_date: date | None = None,
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
        routing_intent=routing_intent,
        strategy_key=strategy_key,
        candidate_id=candidate_id,
        holding_type=holding_type,
        stop_loss_price=stop_loss_price,
        stop_loss_pct=stop_loss_pct,
        target_price=target_price,
        trailing_stop_pct=trailing_stop_pct,
        max_hold_days=max_hold_days,
        scheduled_exit_date=scheduled_exit_date,
        created_at=created_at or DEFAULT_TS,
    )


def make_order_book(
    *,
    symbol: str = "7203",
    bids: Sequence[tuple[Decimal | str | int, int]] = (("999", 200), ("998", 500)),
    asks: Sequence[tuple[Decimal | str | int, int]] = (("1000", 200), ("1001", 500)),
    timestamp: datetime | None = None,
    received_at: datetime | None = None,
) -> OrderBookSnapshot:
    def _to_levels(raw: Sequence[tuple[Decimal | str | int, int]]) -> list[PriceLevel]:
        return [PriceLevel(price=Decimal(str(p)), quantity=q) for p, q in raw]

    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=timestamp or DEFAULT_TS,
        received_at=received_at,
        bids=_to_levels(bids),
        asks=_to_levels(asks),
    )


def make_paper_position(
    *,
    symbol: str = "7203",
    quantity: int = 100,
    entry_price: Decimal = Decimal("1000"),
    holding_type: TradingStyle = TradingStyle.DAY,
    target_price: Decimal | None = None,
    stop_loss_price: Decimal | None = None,
    max_hold_days: int | None = None,
    scheduled_exit_date: date | None = None,
    trailing_stop_pct: Decimal | None = None,
    opened_at: datetime | None = None,
    position_generation_id: UUID | None = None,
) -> PaperPosition:
    return PaperPosition(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        holding_type=holding_type,
        target_price=target_price,
        stop_loss_price=stop_loss_price,
        max_hold_days=max_hold_days,
        scheduled_exit_date=scheduled_exit_date,
        trailing_stop_pct=trailing_stop_pct,
        opened_at=opened_at or DEFAULT_TS,
        position_generation_id=position_generation_id,
    )
