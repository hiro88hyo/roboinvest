#!/usr/bin/env python3
"""Rule-strategy parameter sweep over exported daily_ohlcv rows.

Input CSV/JSONL columns:
  symbol,date,open,high,low,close,volume,turnover

The script evaluates current rule-strategy candidates with daily OHLCV-only
filters and writes train/validation metrics to sweep_results.csv by default.
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
from typing import Literal

COMMISSION_RATE = 0.00099
SLIPPAGE_RATE = 0.0005

RSI_BUY_VALUES = (20.0, 25.0, 30.0)
RSI_SELL_VALUES = (70.0, 75.0, 80.0)
BOLLINGER_TOLERANCE_VALUES = (0.0, 0.05, 0.15)
FOCUSED_RSI_BUY_VALUES = (25.0,)
FOCUSED_RSI_SELL_VALUES = (75.0,)
FOCUSED_BOLLINGER_TOLERANCE_VALUES = (0.15,)
ADX_MAX_VALUES: tuple[float | None, ...] = (None, 20.0, 25.0, 30.0)
VOLUME_RATIO_MIN_VALUES = (1.0, 1.5, 2.0)
ATR_STOP_MULTIPLE_VALUES: tuple[float | None, ...] = (None, 1.0, 1.5, 2.0)
TURNOVER_MIN_VALUES = (0.0,)

RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
ATR_PERIOD = 14
ADX_PERIOD = 14
VOLUME_RATIO_PERIOD = 20
WALK_FORWARD_FOLDS = 3

GridMode = Literal["focused", "full"]


@dataclass(frozen=True, slots=True)
class OhlcvRow:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float


@dataclass(frozen=True, slots=True)
class SweepParams:
    rsi_buy: float
    rsi_sell: float
    bollinger_tolerance: float
    adx_max: float | None
    volume_ratio_min: float
    atr_stop_multiple: float | None
    turnover_min: float


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
class PreparedBar:
    date: date
    close: float
    low: float
    turnover: float
    rsi: float | None
    bollinger: tuple[float, float] | None
    adx: float | None
    atr: float | None
    volume_ratio: float | None


@dataclass(frozen=True, slots=True)
class PreparedSymbol:
    symbol: str
    bars: list[PreparedBar]


@dataclass(frozen=True, slots=True)
class Metrics:
    trade_count: int
    total_net_pnl: float
    win_rate: float
    profit_factor: float | None
    max_drawdown: float
    sharpe_ratio: float | None
    expectancy: float


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    fold_count: int
    worst_profit_factor: float | None
    median_profit_factor: float | None
    pf_ge_1_count: int
    pf_ge_1_2_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep rule-strategy parameters over daily OHLCV.")
    parser.add_argument("--input", type=Path, required=True, help="daily_ohlcv CSV or JSONL file.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sweep_results.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--grid",
        choices=("focused", "full"),
        default="focused",
        help="Parameter grid size. focused keeps current production RSI/Bollinger values fixed.",
    )
    args = parser.parse_args()

    rows = read_ohlcv(args.input)
    results = run_sweep(rows, grid=args.grid)
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
            open=_float_field(raw, "open", fallback=float(raw["close"])),
            high=_float_field(raw, "high", fallback=float(raw["close"])),
            low=_float_field(raw, "low", fallback=float(raw["close"])),
            volume=int(float(raw.get("volume", 0))),
            turnover=_float_field(raw, "turnover", fallback=0.0),
        )
        for raw in raw_rows
        if raw.get("close") not in (None, "")
    ]
    return sorted(rows, key=lambda row: (row.symbol, row.date))


def run_sweep(rows: list[OhlcvRow], *, grid: GridMode = "focused") -> list[dict[str, object]]:
    if not rows:
        return []
    split_date = _split_date(rows)
    fold_dates = sorted({row.date for row in rows})
    prepared_symbols = prepare_symbols(rows)
    out: list[dict[str, object]] = []
    for params in _param_grid(grid):
        trades = simulate_trades(prepared_symbols, params)
        train = [trade for trade in trades if trade.exit_date <= split_date]
        validation = [trade for trade in trades if trade.exit_date > split_date]
        train_metrics = calculate_metrics(train)
        validation_metrics = calculate_metrics(validation)
        fold_metrics = calculate_walk_forward_metrics(fold_dates, trades)
        out.append(
            {
                "rsi_buy": params.rsi_buy,
                "rsi_sell": params.rsi_sell,
                "bollinger_tolerance": params.bollinger_tolerance,
                "adx_max": params.adx_max,
                "volume_ratio_min": params.volume_ratio_min,
                "atr_stop_multiple": params.atr_stop_multiple,
                "turnover_min": params.turnover_min,
                "split_date": split_date.isoformat(),
                **_prefix_metrics("train", train_metrics),
                **_prefix_metrics("validation", validation_metrics),
                **_prefix_fold_metrics("validation", fold_metrics),
            }
        )
    return out


def prepare_symbols(rows: list[OhlcvRow]) -> list[PreparedSymbol]:
    by_symbol: dict[str, list[OhlcvRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)

    prepared: list[PreparedSymbol] = []
    for symbol, symbol_rows in by_symbol.items():
        highs = [row.high for row in symbol_rows]
        lows = [row.low for row in symbol_rows]
        closes = [row.close for row in symbol_rows]
        volumes = [row.volume for row in symbol_rows]
        bars = [
            PreparedBar(
                date=row.date,
                close=row.close,
                low=row.low,
                turnover=row.turnover,
                rsi=_rsi(closes, idx, period=RSI_PERIOD),
                bollinger=_bollinger(closes, idx, period=BOLLINGER_PERIOD),
                adx=_adx(highs, lows, closes, idx, period=ADX_PERIOD),
                atr=_atr(highs, lows, closes, idx, period=ATR_PERIOD),
                volume_ratio=_volume_ratio(volumes, idx, period=VOLUME_RATIO_PERIOD),
            )
            for idx, row in enumerate(symbol_rows)
        ]
        prepared.append(PreparedSymbol(symbol=symbol, bars=bars))
    return prepared


def simulate_trades(prepared_symbols: list[PreparedSymbol], params: SweepParams) -> list[Trade]:
    trades: list[Trade] = []
    for prepared_symbol in prepared_symbols:
        entry: tuple[date, float] | None = None
        stop_price: float | None = None
        for bar in prepared_symbol.bars:
            if entry is not None and stop_price is not None and bar.low <= stop_price:
                trades.append(
                    Trade(
                        symbol=prepared_symbol.symbol,
                        entry_date=entry[0],
                        exit_date=bar.date,
                        entry_price=entry[1],
                        exit_price=stop_price,
                    )
                )
                entry = None
                stop_price = None
                continue

            buy = (
                _passes_entry_filters(
                    turnover=bar.turnover,
                    params=params,
                    adx=bar.adx,
                    volume_ratio=bar.volume_ratio,
                )
                and bar.rsi is not None
                and bar.rsi <= params.rsi_buy
            )
            sell = bar.rsi is not None and bar.rsi >= params.rsi_sell
            if bar.bollinger is not None:
                upper, lower = bar.bollinger
                band_width = upper - lower
                margin = band_width * params.bollinger_tolerance
                buy = buy or (
                    _passes_entry_filters(
                        turnover=bar.turnover,
                        params=params,
                        adx=bar.adx,
                        volume_ratio=bar.volume_ratio,
                    )
                    and bar.close < lower - margin
                )
                sell = sell or bar.close > upper + margin

            if entry is None and buy:
                entry = (bar.date, bar.close)
                if params.atr_stop_multiple is not None and bar.atr is not None:
                    stop_price = bar.close - (bar.atr * params.atr_stop_multiple)
                else:
                    stop_price = None
            elif entry is not None and sell:
                trades.append(
                    Trade(
                        symbol=prepared_symbol.symbol,
                        entry_date=entry[0],
                        exit_date=bar.date,
                        entry_price=entry[1],
                        exit_price=bar.close,
                    )
                )
                entry = None
                stop_price = None
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


def calculate_walk_forward_metrics(dates: list[date], trades: list[Trade]) -> FoldMetrics:
    if len(dates) < WALK_FORWARD_FOLDS + 1:
        return FoldMetrics(0, None, None, 0, 0)

    profit_factors: list[float] = []
    for fold in range(1, WALK_FORWARD_FOLDS + 1):
        train_end_idx = (len(dates) * fold) // (WALK_FORWARD_FOLDS + 1)
        validation_end_idx = (len(dates) * (fold + 1)) // (WALK_FORWARD_FOLDS + 1)
        if train_end_idx <= 0 or validation_end_idx <= train_end_idx:
            continue
        validation_start = dates[train_end_idx]
        validation_end = dates[validation_end_idx - 1]
        fold_trades = [
            trade for trade in trades if validation_start <= trade.exit_date <= validation_end
        ]
        metrics = calculate_metrics(fold_trades)
        if metrics.profit_factor is not None:
            profit_factors.append(metrics.profit_factor)

    if not profit_factors:
        return FoldMetrics(0, None, None, 0, 0)
    sorted_pf = sorted(profit_factors)
    median = sorted_pf[len(sorted_pf) // 2]
    return FoldMetrics(
        fold_count=len(profit_factors),
        worst_profit_factor=min(profit_factors),
        median_profit_factor=median,
        pf_ge_1_count=sum(1 for value in profit_factors if value >= 1.0),
        pf_ge_1_2_count=sum(1 for value in profit_factors if value >= 1.2),
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


def _param_grid(grid: GridMode) -> Iterable[SweepParams]:
    rsi_buy_values: tuple[float, ...]
    rsi_sell_values: tuple[float, ...]
    tolerance_values: tuple[float, ...]
    if grid == "focused":
        rsi_buy_values = FOCUSED_RSI_BUY_VALUES
        rsi_sell_values = FOCUSED_RSI_SELL_VALUES
        tolerance_values = FOCUSED_BOLLINGER_TOLERANCE_VALUES
    else:
        rsi_buy_values = RSI_BUY_VALUES
        rsi_sell_values = RSI_SELL_VALUES
        tolerance_values = BOLLINGER_TOLERANCE_VALUES

    for rsi_buy in rsi_buy_values:
        for rsi_sell in rsi_sell_values:
            for tolerance in tolerance_values:
                for adx_max in ADX_MAX_VALUES:
                    for volume_ratio_min in VOLUME_RATIO_MIN_VALUES:
                        for atr_stop_multiple in ATR_STOP_MULTIPLE_VALUES:
                            for turnover_min in TURNOVER_MIN_VALUES:
                                yield SweepParams(
                                    rsi_buy=rsi_buy,
                                    rsi_sell=rsi_sell,
                                    bollinger_tolerance=tolerance,
                                    adx_max=adx_max,
                                    volume_ratio_min=volume_ratio_min,
                                    atr_stop_multiple=atr_stop_multiple,
                                    turnover_min=turnover_min,
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


def _prefix_fold_metrics(prefix: str, metrics: FoldMetrics) -> dict[str, object]:
    return {
        f"{prefix}_fold_count": metrics.fold_count,
        f"{prefix}_worst_fold_profit_factor": None
        if metrics.worst_profit_factor is None
        else _round(metrics.worst_profit_factor),
        f"{prefix}_median_fold_profit_factor": None
        if metrics.median_profit_factor is None
        else _round(metrics.median_profit_factor),
        f"{prefix}_fold_pf_ge_1_count": metrics.pf_ge_1_count,
        f"{prefix}_fold_pf_ge_1_2_count": metrics.pf_ge_1_2_count,
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


def _volume_ratio(values: list[int], idx: int, *, period: int) -> float | None:
    if idx + 1 < period:
        return None
    window = values[idx + 1 - period : idx + 1]
    average = sum(window) / period
    if average <= 0:
        return None
    return values[idx] / average


def _true_range(highs: list[float], lows: list[float], closes: list[float], idx: int) -> float:
    if idx == 0:
        return highs[idx] - lows[idx]
    return max(
        highs[idx] - lows[idx],
        abs(highs[idx] - closes[idx - 1]),
        abs(lows[idx] - closes[idx - 1]),
    )


def _atr(
    highs: list[float], lows: list[float], closes: list[float], idx: int, *, period: int
) -> float | None:
    if idx + 1 < period:
        return None
    true_ranges = [_true_range(highs, lows, closes, i) for i in range(idx + 1 - period, idx + 1)]
    return sum(true_ranges) / period


def _adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    idx: int,
    *,
    period: int,
) -> float | None:
    if idx < period:
        return None

    plus_dm_sum = 0.0
    minus_dm_sum = 0.0
    true_range_sum = 0.0
    for i in range(idx + 1 - period, idx + 1):
        if i == 0:
            continue
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm_sum += up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm_sum += down_move if down_move > up_move and down_move > 0 else 0.0
        true_range_sum += _true_range(highs, lows, closes, i)

    if true_range_sum <= 0:
        return None
    plus_di = 100.0 * plus_dm_sum / true_range_sum
    minus_di = 100.0 * minus_dm_sum / true_range_sum
    di_sum = plus_di + minus_di
    if di_sum <= 0:
        return 0.0
    return 100.0 * abs(plus_di - minus_di) / di_sum


def _passes_entry_filters(
    *,
    turnover: float,
    params: SweepParams,
    adx: float | None,
    volume_ratio: float | None,
) -> bool:
    if params.turnover_min > 0 and turnover < params.turnover_min:
        return False
    if params.adx_max is not None and (adx is None or adx > params.adx_max):
        return False
    return not (volume_ratio is None or volume_ratio < params.volume_ratio_min)


def _float_field(raw: dict[str, object], field: str, *, fallback: float) -> float:
    value = raw.get(field)
    if value in (None, ""):
        return fallback
    try:
        return float(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid numeric field {field}: {value!r}") from exc


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
