#!/usr/bin/env python3
"""Rule-strategy parameter sweep over exported daily_ohlcv rows.

Input CSV/JSONL columns:
  symbol,date,open,high,low,close,volume

The script evaluates the fixed grid requested in docs/handoff/2026-06-fable5-feedback.md
and writes train/validation metrics to sweep_results.csv by default.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from math import sqrt
from pathlib import Path

COMMISSION_RATE = 0.00099
SLIPPAGE_RATE = 0.0005

RSI_BUY_VALUES = (20.0, 25.0, 30.0)
RSI_SELL_VALUES = (70.0, 75.0, 80.0)
SMA_SHORT_VALUES = (5, 10, 20)
SMA_LONG_VALUES = (25, 50, 75)
BOLLINGER_TOLERANCE_VALUES = (0.0, 0.05, 0.15)


@dataclass(frozen=True, slots=True)
class OhlcvRow:
    symbol: str
    date: date
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class SweepParams:
    rsi_buy: float
    rsi_sell: float
    sma_short: int
    sma_long: int
    bollinger_tolerance: float


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float

    @property
    def net_pnl(self) -> float:
        entry_notional = self.entry_price
        exit_notional = self.exit_price
        gross = exit_notional - entry_notional
        commission = (entry_notional + exit_notional) * COMMISSION_RATE
        slippage = (entry_notional + exit_notional) * SLIPPAGE_RATE
        return gross - commission - slippage


@dataclass(frozen=True, slots=True)
class Metrics:
    trade_count: int
    total_net_pnl: float
    win_rate: float
    profit_factor: float | None
    max_drawdown: float
    sharpe_ratio: float | None
    expectancy: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep rule-strategy parameters over daily OHLCV.")
    parser.add_argument("--input", type=Path, required=True, help="daily_ohlcv CSV or JSONL file.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sweep_results.csv"),
        help="Output CSV path.",
    )
    args = parser.parse_args()

    rows = read_ohlcv(args.input)
    results = run_sweep(rows)
    write_results(results, args.output)
    print(f"wrote {len(results)} rows to {args.output}")
    return 0


def read_ohlcv(path: Path) -> list[OhlcvRow]:
    if path.suffix.lower() == ".jsonl":
        raw_rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        ]
    else:
        with path.open("r", encoding="utf-8", newline="") as f:
            raw_rows = list(csv.DictReader(f))
    rows = [
        OhlcvRow(
            symbol=str(raw["symbol"]),
            date=date.fromisoformat(str(raw["date"])),
            close=float(raw["close"]),
            volume=int(float(raw.get("volume", 0))),
        )
        for raw in raw_rows
        if raw.get("close") not in (None, "")
    ]
    return sorted(rows, key=lambda row: (row.symbol, row.date))


def run_sweep(rows: list[OhlcvRow]) -> list[dict[str, object]]:
    if not rows:
        return []
    split_date = _split_date(rows)
    out: list[dict[str, object]] = []
    for params in _param_grid():
        trades = simulate_trades(rows, params)
        train = [trade for trade in trades if trade.exit_date <= split_date]
        validation = [trade for trade in trades if trade.exit_date > split_date]
        train_metrics = calculate_metrics(train)
        validation_metrics = calculate_metrics(validation)
        out.append(
            {
                "rsi_buy": params.rsi_buy,
                "rsi_sell": params.rsi_sell,
                "sma_short": params.sma_short,
                "sma_long": params.sma_long,
                "bollinger_tolerance": params.bollinger_tolerance,
                "split_date": split_date.isoformat(),
                **_prefix_metrics("train", train_metrics),
                **_prefix_metrics("validation", validation_metrics),
            }
        )
    return out


def simulate_trades(rows: list[OhlcvRow], params: SweepParams) -> list[Trade]:
    by_symbol: dict[str, list[OhlcvRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)

    trades: list[Trade] = []
    for symbol, symbol_rows in by_symbol.items():
        closes = [row.close for row in symbol_rows]
        entry: tuple[date, float] | None = None
        prev_sma_diff: float | None = None
        for idx, row in enumerate(symbol_rows):
            rsi = _rsi(closes, idx, period=14)
            sma_short = _sma(closes, idx, params.sma_short)
            sma_long = _sma(closes, idx, params.sma_long)
            boll = _bollinger(closes, idx, period=20)

            buy = rsi is not None and rsi <= params.rsi_buy
            sell = rsi is not None and rsi >= params.rsi_sell
            if sma_short is not None and sma_long is not None:
                diff = sma_short - sma_long
                if prev_sma_diff is not None:
                    buy = buy or (prev_sma_diff <= 0 < diff)
                    sell = sell or (prev_sma_diff >= 0 > diff)
                prev_sma_diff = diff
            if boll is not None:
                upper, lower = boll
                band_width = upper - lower
                margin = band_width * params.bollinger_tolerance
                buy = buy or row.close < lower - margin
                sell = sell or row.close > upper + margin

            if entry is None and buy:
                entry = (row.date, row.close)
            elif entry is not None and sell:
                trades.append(
                    Trade(
                        symbol=symbol,
                        entry_date=entry[0],
                        exit_date=row.date,
                        entry_price=entry[1],
                        exit_price=row.close,
                    )
                )
                entry = None
    return trades


def calculate_metrics(trades: Iterable[Trade]) -> Metrics:
    pnls = [trade.net_pnl for trade in trades]
    trade_count = len(pnls)
    total = sum(pnls)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return Metrics(
        trade_count=trade_count,
        total_net_pnl=total,
        win_rate=(len(wins) / trade_count) if trade_count else 0.0,
        profit_factor=None if gross_loss == 0 else gross_profit / gross_loss,
        max_drawdown=_max_drawdown(pnls),
        sharpe_ratio=_sharpe_ratio(pnls),
        expectancy=(total / trade_count) if trade_count else 0.0,
    )


def write_results(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _param_grid() -> Iterable[SweepParams]:
    for rsi_buy in RSI_BUY_VALUES:
        for rsi_sell in RSI_SELL_VALUES:
            for sma_short in SMA_SHORT_VALUES:
                for sma_long in SMA_LONG_VALUES:
                    if sma_short >= sma_long:
                        continue
                    for tolerance in BOLLINGER_TOLERANCE_VALUES:
                        yield SweepParams(
                            rsi_buy=rsi_buy,
                            rsi_sell=rsi_sell,
                            sma_short=sma_short,
                            sma_long=sma_long,
                            bollinger_tolerance=tolerance,
                        )


def _split_date(rows: list[OhlcvRow]) -> date:
    dates = sorted({row.date for row in rows})
    return dates[(len(dates) - 1) // 2]


def _prefix_metrics(prefix: str, metrics: Metrics) -> dict[str, object]:
    return {
        f"{prefix}_trade_count": metrics.trade_count,
        f"{prefix}_total_net_pnl": _round(metrics.total_net_pnl),
        f"{prefix}_win_rate": _round(metrics.win_rate),
        f"{prefix}_profit_factor": None
        if metrics.profit_factor is None
        else _round(metrics.profit_factor),
        f"{prefix}_max_drawdown": _round(metrics.max_drawdown),
        f"{prefix}_sharpe_ratio": None
        if metrics.sharpe_ratio is None
        else _round(metrics.sharpe_ratio),
        f"{prefix}_expectancy": _round(metrics.expectancy),
    }


def _round(value: float) -> float:
    return round(value, 6)


def _sma(values: list[float], idx: int, period: int) -> float | None:
    if idx + 1 < period:
        return None
    window = values[idx + 1 - period : idx + 1]
    return sum(window) / period


def _rsi(values: list[float], idx: int, *, period: int) -> float | None:
    if idx < period:
        return None
    gains = 0.0
    losses = 0.0
    for prev, current in zip(
        values[idx - period : idx],
        values[idx - period + 1 : idx + 1],
        strict=True,
    ):
        change = current - prev
        if change >= 0:
            gains += change
        else:
            losses += abs(change)
    if gains == 0 and losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _bollinger(values: list[float], idx: int, *, period: int) -> tuple[float, float] | None:
    if idx + 1 < period:
        return None
    window = values[idx + 1 - period : idx + 1]
    mean = sum(window) / period
    variance = sum((value - mean) ** 2 for value in window) / period
    std = sqrt(variance)
    return mean + 2 * std, mean - 2 * std


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _sharpe_ratio(pnls: list[float]) -> float | None:
    if len(pnls) < 2:
        return None
    mean = sum(pnls) / len(pnls)
    variance = sum((pnl - mean) ** 2 for pnl in pnls) / (len(pnls) - 1)
    if variance == 0:
        return None
    return (mean / sqrt(variance)) * sqrt(len(pnls))


if __name__ == "__main__":
    raise SystemExit(main())
