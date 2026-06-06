from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from trade_contracts.enums import OrderType, Side, SignalSource, TradeMode, TradeType, TradingStyle
from trade_contracts.market import TickData
from trade_contracts.order import OrderRequest

from feature_engine.clients.supabase import PositionSnapshot


@dataclass(frozen=True, slots=True)
class ExitTrigger:
    symbol: str
    trade_type: TradeType
    quantity: int
    reason: str
    price: Decimal
    threshold: Decimal


@dataclass(slots=True)
class ExitOrderMonitor:
    """Position exit monitor for stop/target/trailing conditions.

    It emits at most one pending exit per symbol/trade_type/reason while the
    condition remains true. The pending key is cleared when the condition is no
    longer true or when the position disappears from subsequent snapshots.
    """

    _pending: set[tuple[str, TradeType, str]] = field(default_factory=set)
    _trailing_peaks: dict[tuple[str, TradeType], Decimal] = field(default_factory=dict)

    def collect_triggers(
        self,
        *,
        tick: TickData,
        positions: list[PositionSnapshot],
        max_hold_minutes: int | None = None,
    ) -> list[ExitTrigger]:
        active_keys: set[tuple[str, TradeType]] = set()
        triggers: list[ExitTrigger] = []

        for pos in positions:
            if pos.symbol != tick.symbol or pos.quantity <= 0:
                continue
            key = (pos.symbol, pos.trade_type)
            active_keys.add(key)
            self._update_trailing_peak(key, pos, tick.price)

            condition = _exit_condition(
                pos,
                tick,
                self._trailing_peaks.get(key),
                max_hold_minutes=max_hold_minutes,
            )
            if condition is None:
                self._clear_pending_for_position(key)
                continue

            reason, threshold = condition
            pending_key = (pos.symbol, pos.trade_type, reason)
            if pending_key in self._pending:
                continue
            self._pending.add(pending_key)
            triggers.append(
                ExitTrigger(
                    symbol=pos.symbol,
                    trade_type=pos.trade_type,
                    quantity=pos.quantity,
                    reason=reason,
                    price=tick.price,
                    threshold=threshold,
                )
            )

        self._clear_gone_positions(active_keys)
        return triggers

    def _update_trailing_peak(
        self,
        key: tuple[str, TradeType],
        pos: PositionSnapshot,
        price: Decimal,
    ) -> None:
        if pos.trailing_stop_pct is None or pos.trailing_stop_pct <= 0:
            self._trailing_peaks.pop(key, None)
            return
        baseline = max(pos.entry_price, pos.current_price, price)
        previous = self._trailing_peaks.get(key, baseline)
        self._trailing_peaks[key] = max(previous, price)

    def _clear_pending_for_position(self, key: tuple[str, TradeType]) -> None:
        self._pending = {pending for pending in self._pending if pending[:2] != key}

    def _clear_gone_positions(self, active_keys: set[tuple[str, TradeType]]) -> None:
        self._pending = {pending for pending in self._pending if pending[:2] in active_keys}
        self._trailing_peaks = {
            key: peak for key, peak in self._trailing_peaks.items() if key in active_keys
        }


def build_exit_order(trigger: ExitTrigger, *, created_at: datetime | None = None) -> OrderRequest:
    return OrderRequest(
        unified_signal_id=None,
        symbol=trigger.symbol,
        side=Side.SELL,
        quantity=trigger.quantity,
        order_type=OrderType.MARKET,
        trade_mode=TradeMode(trigger.trade_type.value),
        signal_source=SignalSource.RULE,
        created_at=created_at or datetime.now(UTC),
    )


def topic_for_exit_order(trigger: ExitTrigger, *, live_topic: str, paper_topic: str) -> str:
    if trigger.trade_type is TradeType.LIVE:
        return live_topic
    if trigger.trade_type is TradeType.PAPER:
        return paper_topic
    raise AssertionError(f"unexpected trade_type: {trigger.trade_type}")


def _exit_condition(
    pos: PositionSnapshot,
    tick: TickData,
    trailing_peak: Decimal | None,
    *,
    max_hold_minutes: int | None,
) -> tuple[str, Decimal] | None:
    price = tick.price
    if pos.stop_loss_price is not None and price <= pos.stop_loss_price:
        return ("stop_loss", pos.stop_loss_price)
    if pos.target_price is not None and price >= pos.target_price:
        return ("target_price", pos.target_price)
    if (
        pos.trailing_stop_pct is not None
        and pos.trailing_stop_pct > 0
        and trailing_peak is not None
        and trailing_peak > pos.entry_price
    ):
        threshold = trailing_peak * (Decimal("1") - pos.trailing_stop_pct)
        if price <= threshold:
            return ("trailing_stop", threshold)
    if (
        max_hold_minutes is not None
        and max_hold_minutes > 0
        and pos.holding_type is TradingStyle.DAY
    ):
        held_seconds = (tick.timestamp - pos.opened_at).total_seconds()
        if held_seconds >= max_hold_minutes * 60:
            return ("max_hold_minutes", Decimal(max_hold_minutes))
    return None
