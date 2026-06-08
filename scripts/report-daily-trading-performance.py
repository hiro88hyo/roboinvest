#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""Report daily realized/unrealized trading performance from Cloud Supabase.

Run under resolved production env:

    set -a && . infra/.op.service-account.env && set +a
    op run --env-file infra/env.production -- \\
      uv run python scripts/report-daily-trading-performance.py --trade-mode both
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

TradeMode = Literal["live", "paper"]

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    price: Decimal
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class OpenLot:
    quantity: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class RealizedSummary:
    realized_pnl: Decimal
    round_trip_sell_quantity: int
    unmatched_sell_quantity: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=datetime.now(JST).date(),
        help="JST trading date to report, YYYY-MM-DD. Defaults to today in JST.",
    )
    parser.add_argument(
        "--trade-mode",
        choices=("live", "paper", "both"),
        default="paper",
        help="which trade history to report",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _date_bounds_jst(trading_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trading_date, time.min, JST).astimezone(UTC)
    end = datetime.combine(trading_date + timedelta(days=1), time.min, JST).astimezone(UTC)
    return start, end


def _decimal(raw: Any, *, field: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError(f"invalid decimal field={field} value={raw!r}") from exc


def _int(raw: Any, *, field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid int field={field} value={raw!r}") from exc


def _datetime(raw: Any, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise RuntimeError(f"invalid datetime field={field} value={raw!r}")
    value = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid datetime field={field} value={raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _get(client: httpx.Client, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    resp = client.get(path, params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected response for {path}: {payload!r}")
    return payload


def _fetch_trades(
    client: httpx.Client, *, trade_mode: TradeMode, trading_date: date
) -> list[Trade]:
    start, end = _date_bounds_jst(trading_date)
    rows = _get(
        client,
        f"/rest/v1/trades_{trade_mode}",
        {
            "select": "symbol,side,quantity,price,executed_at",
            "and": f"(executed_at.gte.{start.isoformat()},executed_at.lt.{end.isoformat()})",
            "order": "executed_at.asc",
        },
    )
    trades: list[Trade] = []
    for row in rows:
        side = row.get("side")
        if side not in {"BUY", "SELL"}:
            raise RuntimeError(f"invalid trade side: {side!r}")
        trades.append(
            Trade(
                symbol=str(row["symbol"]),
                side=side,
                quantity=_int(row.get("quantity"), field="quantity"),
                price=_decimal(row.get("price"), field="price"),
                executed_at=_datetime(row.get("executed_at"), field="executed_at"),
            )
        )
    return trades


def _fetch_open_positions(client: httpx.Client, *, trade_mode: TradeMode) -> list[dict[str, Any]]:
    return _get(
        client,
        "/rest/v1/positions",
        {
            "trade_type": f"eq.{trade_mode}",
            "select": (
                "symbol,side,quantity,entry_price,current_price,unrealized_pnl,"
                "holding_type,opened_at"
            ),
            "order": "symbol.asc",
        },
    )


def _calculate_realized(trades: list[Trade]) -> RealizedSummary:
    lots: defaultdict[str, deque[OpenLot]] = defaultdict(deque)
    realized = Decimal("0")
    sold_qty = 0
    unmatched_sell_qty = 0

    for trade in trades:
        if trade.side == "BUY":
            lots[trade.symbol].append(OpenLot(quantity=trade.quantity, price=trade.price))
            continue

        remaining = trade.quantity
        while remaining > 0 and lots[trade.symbol]:
            lot = lots[trade.symbol].popleft()
            matched_qty = min(remaining, lot.quantity)
            realized += (trade.price - lot.price) * matched_qty
            sold_qty += matched_qty
            remaining -= matched_qty
            if lot.quantity > matched_qty:
                lots[trade.symbol].appendleft(
                    OpenLot(quantity=lot.quantity - matched_qty, price=lot.price)
                )
        unmatched_sell_qty += remaining

    return RealizedSummary(
        realized_pnl=realized,
        round_trip_sell_quantity=sold_qty,
        unmatched_sell_quantity=unmatched_sell_qty,
    )


def _position_unrealized(rows: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        total += _decimal(row.get("unrealized_pnl", "0"), field="unrealized_pnl")
    return total


def _format_yen(value: Decimal) -> str:
    quantized = value.quantize(Decimal("1"))
    sign = "+" if quantized > 0 else ""
    return f"{sign}{quantized:,}円"


def _print_mode_report(
    *,
    trade_mode: TradeMode,
    trading_date: date,
    trades: list[Trade],
    positions: list[dict[str, Any]],
) -> None:
    realized = _calculate_realized(trades)
    unrealized = _position_unrealized(positions)
    mark_to_market = realized.realized_pnl + unrealized
    buy_count = sum(1 for trade in trades if trade.side == "BUY")
    sell_count = len(trades) - buy_count

    print(f"== {trade_mode} {trading_date.isoformat()} JST ==")
    print(f"trades: total={len(trades)} buy={buy_count} sell={sell_count}")
    print(f"realized_pnl: {_format_yen(realized.realized_pnl)}")
    print(f"open_positions: count={len(positions)} unrealized={_format_yen(unrealized)}")
    print(f"mark_to_market: {_format_yen(mark_to_market)}")
    if realized.unmatched_sell_quantity:
        print(f"warning: unmatched_sell_quantity={realized.unmatched_sell_quantity}")
    if positions:
        details = ", ".join(
            f"{row.get('symbol')} qty={row.get('quantity')} "
            f"entry={row.get('entry_price')} current={row.get('current_price')} "
            f"unrealized={row.get('unrealized_pnl')}"
            for row in positions
        )
        print(f"positions: {details}")
    print()


def main() -> int:
    args = parse_args()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY missing", file=sys.stderr)
        return 2

    modes: tuple[TradeMode, ...] = (
        ("live", "paper") if args.trade_mode == "both" else (args.trade_mode,)
    )

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url, headers=headers, timeout=args.timeout) as client:
        for mode in modes:
            trades = _fetch_trades(client, trade_mode=mode, trading_date=args.date)
            positions = _fetch_open_positions(client, trade_mode=mode)
            _print_mode_report(
                trade_mode=mode,
                trading_date=args.date,
                trades=trades,
                positions=positions,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
