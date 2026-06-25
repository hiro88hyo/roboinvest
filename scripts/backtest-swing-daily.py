#!/usr/bin/env python3
"""Backtest preregistered daily OHLCV swing strategy candidates.

The first candidate is intentionally fixed as ``daily_trend_pullback_v0``.
Do not use this script as a parameter optimizer; change parameters only after
documenting a new candidate in docs/features/swing-rebuild-plan.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal

SelectionMode = Literal[
    "ranked",
    "random",
    "score_ascending",
    "score_middle",
    "rank_2_3_first",
]

CandidateName = Literal[
    "daily_trend_pullback_v0",
    "daily_trend_pullback_v1",
    "daily_trend_pullback_v2",
    "daily_trend_pullback_v3",
    "daily_trend_pullback_v4",
    "daily_trend_pullback_v5",
]

RESEARCH_CANDIDATES: tuple[CandidateName, ...] = (
    "daily_trend_pullback_v0",
    "daily_trend_pullback_v1",
    "daily_trend_pullback_v2",
    "daily_trend_pullback_v3",
    "daily_trend_pullback_v4",
    "daily_trend_pullback_v5",
)
DETERMINISTIC_SELECTIONS: tuple[SelectionMode, ...] = (
    "ranked",
    "score_ascending",
    "score_middle",
    "rank_2_3_first",
)

ENTRY_GAP_BINS = (-0.03, -0.01, 0.0, 0.01, 0.03)
ATR_PCT_BINS = (0.02, 0.03, 0.04, 0.06)
RETURN_20D_BINS = (0.08, 0.12, 0.18, 0.25)
DISTANCE_ABOVE_SMA_BINS = (0.0, 0.01, 0.02, 0.03)
BUCKET_DIAGNOSTIC_KEYS = (
    "entry_gap_pct_bins",
    "signal_atr_pct_bins",
    "signal_return_20d_bins",
    "distance_above_sma20_bins",
    "signal_avg_turnover_bins",
    "entry_score_bins",
    "ranked_position_bins",
    "candidate_count_bins",
    "market_close_above_sma20_bins",
    "market_trend_breadth_bins",
    "market_positive_return_5d_bins",
    "market_avg_return_5d_bins",
    "market_positive_return_20d_bins",
    "market_avg_return_20d_bins",
    "market_positive_return_60d_bins",
    "market_avg_return_60d_bins",
)


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
class SwingParams:
    sma_short_period: int = 20
    sma_long_period: int = 60
    sma_long_slope_lookback: int = 20
    atr_period: int = 14
    avg_turnover_period: int = 20
    min_avg_turnover: float = 200_000_000.0
    max_avg_turnover: float | None = None
    min_price: float = 300.0
    max_price: float = 5_000.0
    min_return_20d: float = 0.05
    max_return_20d: float | None = None
    pullback_lookback: int = 5
    pullback_sma_tolerance: float = 0.01
    max_distance_above_sma_short: float = 0.04
    min_atr_pct: float = 0.015
    max_atr_pct: float = 0.08
    min_entry_gap_pct: float | None = None
    max_entry_gap_pct: float | None = None
    blocked_market_positive_return_20d_min: float | None = None
    blocked_market_positive_return_20d_max: float | None = None
    stop_atr_multiple: float = 1.5
    target_r_multiple: float = 2.0
    max_hold_days: int = 10
    starting_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.01
    max_notional_per_position_pct: float = 0.20
    max_positions: int = 5
    max_new_positions_per_day: int | None = None
    lot_size: int = 100
    commission_rate: float = 0.00099
    slippage_rate: float = 0.0005


@dataclass(frozen=True, slots=True)
class PreparedBar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float
    sma_short: float | None
    sma_long: float | None
    sma_long_past: float | None
    atr: float | None
    avg_turnover: float | None
    return_5d: float | None
    return_20d: float | None
    return_60d: float | None
    touched_sma_short_recently: bool


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    symbol: str
    signal_date: date
    signal_close: float
    signal_sma_short: float
    signal_atr_pct: float
    signal_return_20d: float
    signal_avg_turnover: float
    entry_gap_pct: float
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float
    quantity: int
    score: float
    ranked_position: int = 0
    candidate_count: int = 0
    market_close_above_sma20_ratio: float | None = None
    market_trend_breadth_ratio: float | None = None
    market_positive_return_5d_ratio: float | None = None
    market_avg_return_5d: float | None = None
    market_positive_return_20d_ratio: float | None = None
    market_avg_return_20d: float | None = None
    market_positive_return_60d_ratio: float | None = None
    market_avg_return_60d: float | None = None


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    signal_date: date
    signal_close: float
    signal_sma_short: float
    signal_atr_pct: float
    signal_return_20d: float
    signal_avg_turnover: float
    entry_gap_pct: float
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float
    quantity: int
    max_exit_date: date
    entry_score: float
    ranked_position: int
    candidate_count: int
    market_close_above_sma20_ratio: float | None = None
    market_trend_breadth_ratio: float | None = None
    market_positive_return_5d_ratio: float | None = None
    market_avg_return_5d: float | None = None
    market_positive_return_20d_ratio: float | None = None
    market_avg_return_20d: float | None = None
    market_positive_return_60d_ratio: float | None = None
    market_avg_return_60d: float | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    signal_date: date
    signal_close: float
    signal_sma_short: float
    signal_atr_pct: float
    signal_return_20d: float
    signal_avg_turnover: float
    entry_gap_pct: float
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    exit_reason: str
    gross_pnl: float
    costs: float
    net_pnl: float
    entry_score: float
    ranked_position: int
    candidate_count: int
    market_close_above_sma20_ratio: float | None = None
    market_trend_breadth_ratio: float | None = None
    market_positive_return_5d_ratio: float | None = None
    market_avg_return_5d: float | None = None
    market_positive_return_20d_ratio: float | None = None
    market_avg_return_20d: float | None = None
    market_positive_return_60d_ratio: float | None = None
    market_avg_return_60d: float | None = None


@dataclass(frozen=True, slots=True)
class Metrics:
    trade_count: int
    total_net_pnl: float
    win_rate: float | None
    profit_factor: float | None
    max_drawdown: float
    expectancy: float | None
    positive_month_ratio: float | None
    worst_month_net_pnl: float | None


@dataclass(frozen=True, slots=True)
class DrawdownPeriod:
    amount: float
    peak_date: date | None
    trough_date: date | None
    peak_equity: float
    trough_equity: float


@dataclass(frozen=True, slots=True)
class MarketContext:
    date: date
    close_above_sma20_ratio: float | None
    trend_breadth_ratio: float | None
    positive_return_5d_ratio: float | None
    avg_return_5d: float | None
    positive_return_20d_ratio: float | None
    avg_return_20d: float | None
    positive_return_60d_ratio: float | None
    avg_return_60d: float | None


def main() -> int:
    args = build_parser().parse_args()
    rows = read_ohlcv(args.input)
    if args.walk_forward_research:
        result = build_walk_forward_research(
            rows=rows,
            input_path=args.input,
            capital=args.capital,
            min_avg_turnover=args.min_avg_turnover,
            min_train_days=args.min_train_days,
            oos_block_days=args.oos_block_days,
            random_seeds=parse_seed_list(args.random_baseline_seeds),
            fold_count=args.walk_forward_folds,
        )
        if args.output_summary is not None:
            args.output_summary.parent.mkdir(parents=True, exist_ok=True)
            args.output_summary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print("walk_forward_research")
        print(
            "oos_selected: "
            f"net={result['selected_oos']['total_net_pnl']} "
            f"trades={result['selected_oos']['trade_count']} "
            f"pf={result['selected_oos']['profit_factor']} "
            f"max_dd={result['selected_oos']['max_drawdown']} "
            f"research_gate={result['research_gate']['status']}"
        )
        print(
            "blocks: "
            f"count={result['block_count']} "
            f"selected_train_pass={result['selected_train_pass_count']}/"
            f"{result['block_count']} "
            f"selected_oos_pass={result['selected_oos_pass_count']}/"
            f"{result['block_count']}"
        )
        return 0

    params = params_for_candidate(args.candidate, args.capital, args.min_avg_turnover)
    prepared = prepare_bars(rows, params)
    validation_start = args.validation_start or default_validation_start(rows)
    if args.compare_selections:
        comparison_validation_starts = parse_date_list(args.comparison_validation_starts)
        if comparison_validation_starts:
            result = build_multi_split_selection_comparison(
                prepared=prepared,
                params=params,
                candidate=args.candidate,
                input_path=args.input,
                validation_starts=comparison_validation_starts,
                fold_count=args.walk_forward_folds,
                random_seeds=parse_seed_list(args.random_baseline_seeds),
            )
        else:
            result = build_selection_comparison(
                prepared=prepared,
                params=params,
                candidate=args.candidate,
                input_path=args.input,
                validation_start=validation_start,
                fold_count=args.walk_forward_folds,
                random_seeds=parse_seed_list(args.random_baseline_seeds),
            )
        if args.output_summary is not None:
            args.output_summary.parent.mkdir(parents=True, exist_ok=True)
            args.output_summary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        if "selection_summary" in result:
            print(f"multi_split_selection_comparison  {args.candidate}")
            for row in result["selection_summary"]:
                print(
                    f"- {row['label']}: pass={row['pass_count']}/{row['split_count']} "
                    f"validation_net_sum={row['total_validation_net_pnl']} "
                    f"worst_dd={row['worst_validation_max_drawdown']} "
                    f"worst_month={row['worst_validation_month_net_pnl']}"
                )
            return 0
        print(f"selection_comparison  {args.candidate}")
        for row in result["selections"]:
            print(
                f"- {row['label']}: net={row['validation']['total_net_pnl']} "
                f"pf={row['validation']['profit_factor']} "
                f"max_dd={row['validation']['max_drawdown']} "
                f"positive_folds={row['validation_walk_forward']['positive_fold_count']}/"
                f"{row['validation_walk_forward']['fold_count']} "
                f"gate={row['gate']['status']}"
            )
        return 0

    trades = simulate(
        prepared,
        params,
        selection=args.selection,
        random_seed=args.random_seed,
    )
    train_trades = [trade for trade in trades if trade.exit_date < validation_start]
    validation_trades = [trade for trade in trades if trade.exit_date >= validation_start]
    random_baselines = build_random_baselines(
        prepared=prepared,
        params=params,
        validation_start=validation_start,
        seeds=parse_seed_list(args.random_baseline_seeds),
        fold_count=args.walk_forward_folds,
    )
    result = build_result(
        candidate=args.candidate,
        selection=args.selection,
        random_seed=args.random_seed,
        params=params,
        input_path=args.input,
        validation_start=validation_start,
        train_trades=train_trades,
        validation_trades=validation_trades,
        random_baselines=random_baselines,
        fold_count=args.walk_forward_folds,
    )

    if args.output_summary is not None:
        args.output_summary.parent.mkdir(parents=True, exist_ok=True)
        args.output_summary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if args.output_trades is not None:
        args.output_trades.parent.mkdir(parents=True, exist_ok=True)
        write_trades(trades, args.output_trades)

    print(f"{result['gate']['status']}  {args.candidate} selection={args.selection}")
    print(
        "validation: "
        f"net={result['validation']['total_net_pnl']} "
        f"trades={result['validation']['trade_count']} "
        f"pf={result['validation']['profit_factor']} "
        f"max_dd={result['validation']['max_drawdown']} "
        f"positive_month_ratio={result['validation']['positive_month_ratio']} "
        f"worst_month={result['validation']['worst_month_net_pnl']} "
        f"positive_folds={result['validation_walk_forward']['positive_fold_count']}/"
        f"{result['validation_walk_forward']['fold_count']}"
    )
    if random_baselines:
        best_random = max(row["validation"]["total_net_pnl"] for row in random_baselines)
        print(f"random_baseline: seeds={len(random_baselines)} best_validation_net={best_random}")
    for failure in result["gate"]["failures"]:
        print(f"- {failure}")
    return 1 if result["gate"]["failures"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        choices=(
            "daily_trend_pullback_v0",
            "daily_trend_pullback_v1",
            "daily_trend_pullback_v2",
            "daily_trend_pullback_v3",
            "daily_trend_pullback_v4",
            "daily_trend_pullback_v5",
        ),
        default="daily_trend_pullback_v0",
    )
    parser.add_argument("--input", type=Path, required=True, help="daily_ohlcv CSV or JSONL.")
    parser.add_argument(
        "--output-summary",
        type=Path,
        default=Path("out/swing-daily/backtest-summary.json"),
    )
    parser.add_argument(
        "--output-trades",
        type=Path,
        default=Path("out/swing-daily/trades.csv"),
    )
    parser.add_argument(
        "--validation-start",
        type=date.fromisoformat,
        default=None,
        help="First validation exit date. Defaults to the last 30%% of trading dates.",
    )
    parser.add_argument("--capital", type=float, default=1_000_000.0)
    parser.add_argument("--min-avg-turnover", type=float, default=200_000_000.0)
    parser.add_argument(
        "--selection",
        choices=("ranked", "random", "score_ascending", "score_middle", "rank_2_3_first"),
        default="ranked",
        help="How to order same-day eligible candidates before position caps.",
    )
    parser.add_argument("--random-seed", type=int, default=1)
    parser.add_argument(
        "--random-baseline-seeds",
        default="",
        help="Comma-separated seeds to run random selection baselines in the summary.",
    )
    parser.add_argument(
        "--walk-forward-folds",
        type=int,
        default=3,
        help="Number of contiguous validation folds to summarize.",
    )
    parser.add_argument(
        "--compare-selections",
        action="store_true",
        help="Write one summary comparing ranked, score-based, and random selection modes.",
    )
    parser.add_argument(
        "--comparison-validation-starts",
        default="",
        help=(
            "Comma-separated validation start dates for multi-split selection comparison. "
            "Only used with --compare-selections."
        ),
    )
    parser.add_argument(
        "--walk-forward-research",
        action="store_true",
        help=(
            "Run non-overlapping OOS research blocks. Each block chooses one deterministic "
            "candidate/selection using prior train data only, then evaluates the next block once."
        ),
    )
    parser.add_argument(
        "--min-train-days",
        type=int,
        default=250,
        help="Minimum trading dates before the first walk-forward research OOS block.",
    )
    parser.add_argument(
        "--oos-block-days",
        type=int,
        default=60,
        help="Trading dates per non-overlapping OOS block in walk-forward research mode.",
    )
    return parser


def params_for_candidate(
    candidate: CandidateName,
    capital: float,
    min_avg_turnover: float,
) -> SwingParams:
    if candidate == "daily_trend_pullback_v0":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
        )
    if candidate == "daily_trend_pullback_v1":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            risk_per_trade_pct=0.0075,
            max_notional_per_position_pct=0.15,
            max_positions=4,
        )
    if candidate == "daily_trend_pullback_v2":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
        )
    if candidate == "daily_trend_pullback_v3":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
            max_new_positions_per_day=1,
        )
    if candidate == "daily_trend_pullback_v4":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            max_avg_turnover=3_000_000_000.0,
            min_return_20d=0.08,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
            max_new_positions_per_day=1,
        )
    if candidate == "daily_trend_pullback_v5":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            max_avg_turnover=3_000_000_000.0,
            min_return_20d=0.08,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
            max_new_positions_per_day=1,
            blocked_market_positive_return_20d_min=0.45,
            blocked_market_positive_return_20d_max=0.55,
        )
    raise ValueError(f"unknown candidate: {candidate}")


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
            open=_float_field(raw, "open"),
            high=_float_field(raw, "high"),
            low=_float_field(raw, "low"),
            close=_float_field(raw, "close"),
            volume=int(float(raw["volume"])),
            turnover=_float_field(raw, "turnover"),
        )
        for raw in raw_rows
        if raw.get("close") not in (None, "")
    ]
    return sorted(rows, key=lambda row: (row.symbol, row.date))


def prepare_bars(rows: list[OhlcvRow], params: SwingParams) -> dict[str, list[PreparedBar]]:
    by_symbol: dict[str, list[OhlcvRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)

    prepared: dict[str, list[PreparedBar]] = {}
    for symbol, symbol_rows in by_symbol.items():
        symbol_rows = sorted(symbol_rows, key=lambda row: row.date)
        highs = [row.high for row in symbol_rows]
        lows = [row.low for row in symbol_rows]
        closes = [row.close for row in symbol_rows]
        turnovers = [row.turnover for row in symbol_rows]
        bars: list[PreparedBar] = []
        for idx, row in enumerate(symbol_rows):
            sma_short = _sma(closes, idx, params.sma_short_period)
            sma_long = _sma(closes, idx, params.sma_long_period)
            sma_long_past = _sma(
                closes,
                idx - params.sma_long_slope_lookback,
                params.sma_long_period,
            )
            atr = _atr(highs, lows, closes, idx, params.atr_period)
            avg_turnover = _sma(turnovers, idx, params.avg_turnover_period)
            return_5d = _return(closes, idx, 5)
            return_20d = _return(closes, idx, 20)
            return_60d = _return(closes, idx, 60)
            touched = _touched_sma_recently(
                lows=lows,
                closes=closes,
                idx=idx,
                period=params.sma_short_period,
                lookback=params.pullback_lookback,
                tolerance=params.pullback_sma_tolerance,
            )
            bars.append(
                PreparedBar(
                    symbol=symbol,
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    turnover=row.turnover,
                    sma_short=sma_short,
                    sma_long=sma_long,
                    sma_long_past=sma_long_past,
                    atr=atr,
                    avg_turnover=avg_turnover,
                    return_5d=return_5d,
                    return_20d=return_20d,
                    return_60d=return_60d,
                    touched_sma_short_recently=touched,
                )
            )
        prepared[symbol] = bars
    return prepared


def build_market_context_by_date(
    prepared: dict[str, list[PreparedBar]],
) -> dict[date, MarketContext]:
    by_date: dict[date, list[PreparedBar]] = defaultdict(list)
    for bars in prepared.values():
        for bar in bars:
            by_date[bar.date].append(bar)

    contexts: dict[date, MarketContext] = {}
    for item_date, bars in by_date.items():
        close_above_sma20 = [bar.close > bar.sma_short for bar in bars if bar.sma_short is not None]
        trend_breadth = [
            bar.sma_short > bar.sma_long and bar.close > bar.sma_long
            for bar in bars
            if bar.sma_short is not None and bar.sma_long is not None
        ]
        positive_return_20d = [bar.return_20d > 0 for bar in bars if bar.return_20d is not None]
        positive_return_5d = [bar.return_5d > 0 for bar in bars if bar.return_5d is not None]
        positive_return_60d = [bar.return_60d > 0 for bar in bars if bar.return_60d is not None]
        returns_5d = [bar.return_5d for bar in bars if bar.return_5d is not None]
        returns_20d = [bar.return_20d for bar in bars if bar.return_20d is not None]
        returns_60d = [bar.return_60d for bar in bars if bar.return_60d is not None]
        contexts[item_date] = MarketContext(
            date=item_date,
            close_above_sma20_ratio=_true_ratio(close_above_sma20),
            trend_breadth_ratio=_true_ratio(trend_breadth),
            positive_return_5d_ratio=_true_ratio(positive_return_5d),
            avg_return_5d=None if not returns_5d else sum(returns_5d) / len(returns_5d),
            positive_return_20d_ratio=_true_ratio(positive_return_20d),
            avg_return_20d=None if not returns_20d else sum(returns_20d) / len(returns_20d),
            positive_return_60d_ratio=_true_ratio(positive_return_60d),
            avg_return_60d=None if not returns_60d else sum(returns_60d) / len(returns_60d),
        )
    return contexts


def simulate(
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    *,
    selection: SelectionMode = "ranked",
    random_seed: int = 1,
) -> list[Trade]:
    rng = random.Random(random_seed)
    by_date: dict[date, dict[str, PreparedBar]] = defaultdict(dict)
    previous_by_symbol: dict[tuple[str, date], PreparedBar] = {}
    for symbol, bars in prepared.items():
        for idx, bar in enumerate(bars):
            by_date[bar.date][symbol] = bar
            if idx > 0:
                previous_by_symbol[(symbol, bar.date)] = bars[idx - 1]
    market_context_by_date = build_market_context_by_date(prepared)

    equity = params.starting_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    for current_date in sorted(by_date):
        today = by_date[current_date]
        entries = _entry_candidates(
            today=today,
            previous_by_symbol=previous_by_symbol,
            market_context_by_date=market_context_by_date,
            current_date=current_date,
            equity=equity,
            positions=positions,
            params=params,
        )
        entries = order_candidates(entries, selection=selection, rng=rng)
        opened_today = 0
        for candidate in entries:
            open_notional = sum(pos.entry_price * pos.quantity for pos in positions.values())
            max_total_notional = equity
            if (
                params.max_new_positions_per_day is not None
                and opened_today >= params.max_new_positions_per_day
            ):
                break
            if len(positions) >= params.max_positions:
                break
            if open_notional + candidate.entry_price * candidate.quantity > max_total_notional:
                continue
            positions[candidate.symbol] = Position(
                symbol=candidate.symbol,
                signal_date=candidate.signal_date,
                signal_close=candidate.signal_close,
                signal_sma_short=candidate.signal_sma_short,
                signal_atr_pct=candidate.signal_atr_pct,
                signal_return_20d=candidate.signal_return_20d,
                signal_avg_turnover=candidate.signal_avg_turnover,
                entry_gap_pct=candidate.entry_gap_pct,
                entry_date=candidate.entry_date,
                entry_price=candidate.entry_price,
                stop_price=candidate.stop_price,
                target_price=candidate.target_price,
                quantity=candidate.quantity,
                max_exit_date=_nth_symbol_date(
                    prepared[candidate.symbol],
                    candidate.entry_date,
                    params.max_hold_days,
                ),
                entry_score=candidate.score,
                ranked_position=candidate.ranked_position,
                candidate_count=candidate.candidate_count,
                market_close_above_sma20_ratio=candidate.market_close_above_sma20_ratio,
                market_trend_breadth_ratio=candidate.market_trend_breadth_ratio,
                market_positive_return_5d_ratio=candidate.market_positive_return_5d_ratio,
                market_avg_return_5d=candidate.market_avg_return_5d,
                market_positive_return_20d_ratio=candidate.market_positive_return_20d_ratio,
                market_avg_return_20d=candidate.market_avg_return_20d,
                market_positive_return_60d_ratio=candidate.market_positive_return_60d_ratio,
                market_avg_return_60d=candidate.market_avg_return_60d,
            )
            opened_today += 1

        for symbol, position in list(positions.items()):
            bar = today.get(symbol)
            if bar is None:
                continue
            trade = _exit_on_bar(position, bar, params)
            if trade is None:
                continue
            trades.append(trade)
            equity += trade.net_pnl
            del positions[symbol]

    for position in positions.values():
        bars = prepared[position.symbol]
        last_bar = bars[-1]
        trades.append(
            _close_trade(
                position=position,
                exit_date=last_bar.date,
                exit_price=last_bar.close,
                exit_reason="end_of_data",
                params=params,
            )
        )
    return sorted(trades, key=lambda trade: (trade.exit_date, trade.symbol))


def _entry_candidates(
    *,
    today: dict[str, PreparedBar],
    previous_by_symbol: dict[tuple[str, date], PreparedBar],
    market_context_by_date: dict[date, MarketContext],
    current_date: date,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
) -> list[EntryCandidate]:
    candidates: list[EntryCandidate] = []
    for symbol, bar in today.items():
        if symbol in positions:
            continue
        signal_bar = previous_by_symbol.get((symbol, current_date))
        if signal_bar is None or not is_entry_signal(signal_bar, params):
            continue
        market_context = market_context_by_date.get(signal_bar.date)
        if not is_market_context_allowed(market_context, params):
            continue
        if signal_bar.atr is None or signal_bar.atr <= 0:
            continue
        entry_gap_pct = (bar.open / signal_bar.close) - 1.0
        if params.min_entry_gap_pct is not None and entry_gap_pct < params.min_entry_gap_pct:
            continue
        if params.max_entry_gap_pct is not None and entry_gap_pct >= params.max_entry_gap_pct:
            continue
        stop_distance = signal_bar.atr * params.stop_atr_multiple
        stop_price = bar.open - stop_distance
        if stop_price <= 0:
            continue
        quantity = _position_size(
            entry_price=bar.open,
            stop_price=stop_price,
            equity=equity,
            params=params,
        )
        if quantity <= 0:
            continue
        assert signal_bar.sma_short is not None
        assert signal_bar.atr is not None
        assert signal_bar.return_20d is not None
        assert signal_bar.avg_turnover is not None
        candidates.append(
            EntryCandidate(
                symbol=symbol,
                signal_date=signal_bar.date,
                signal_close=signal_bar.close,
                signal_sma_short=signal_bar.sma_short,
                signal_atr_pct=signal_bar.atr / signal_bar.close,
                signal_return_20d=signal_bar.return_20d,
                signal_avg_turnover=signal_bar.avg_turnover,
                entry_gap_pct=entry_gap_pct,
                entry_date=current_date,
                entry_price=bar.open,
                stop_price=stop_price,
                target_price=bar.open + stop_distance * params.target_r_multiple,
                quantity=quantity,
                score=_entry_score(signal_bar),
                market_close_above_sma20_ratio=(
                    None if market_context is None else market_context.close_above_sma20_ratio
                ),
                market_trend_breadth_ratio=(
                    None if market_context is None else market_context.trend_breadth_ratio
                ),
                market_positive_return_5d_ratio=(
                    None if market_context is None else market_context.positive_return_5d_ratio
                ),
                market_avg_return_5d=(
                    None if market_context is None else market_context.avg_return_5d
                ),
                market_positive_return_20d_ratio=(
                    None if market_context is None else market_context.positive_return_20d_ratio
                ),
                market_avg_return_20d=(
                    None if market_context is None else market_context.avg_return_20d
                ),
                market_positive_return_60d_ratio=(
                    None if market_context is None else market_context.positive_return_60d_ratio
                ),
                market_avg_return_60d=(
                    None if market_context is None else market_context.avg_return_60d
                ),
            )
        )
    ranked = sorted(candidates, key=lambda item: (-item.score, item.symbol))
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(ranked))
        for idx, candidate in enumerate(ranked, start=1)
    ]


def order_candidates(
    candidates: list[EntryCandidate],
    *,
    selection: SelectionMode,
    rng: random.Random,
) -> list[EntryCandidate]:
    if selection == "ranked":
        return candidates
    if selection == "score_ascending":
        return sorted(candidates, key=lambda item: (item.score, item.symbol))
    if selection == "score_middle":
        return sorted(candidates, key=lambda item: (abs(item.score - 0.15), item.symbol))
    if selection == "rank_2_3_first":
        return sorted(candidates, key=lambda item: (_rank_preference(item), item.symbol))
    if selection == "random":
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        return shuffled
    raise ValueError(f"unknown selection: {selection}")


def _rank_preference(candidate: EntryCandidate) -> tuple[int, int, float]:
    if 2 <= candidate.ranked_position <= 3:
        bucket = 0
    elif candidate.ranked_position == 1:
        bucket = 1
    elif 4 <= candidate.ranked_position <= 5:
        bucket = 2
    else:
        bucket = 3
    return (bucket, candidate.ranked_position, abs(candidate.score - 0.15))


def is_entry_signal(bar: PreparedBar, params: SwingParams) -> bool:
    required = (
        bar.sma_short,
        bar.sma_long,
        bar.sma_long_past,
        bar.atr,
        bar.avg_turnover,
        bar.return_20d,
    )
    if any(value is None for value in required):
        return False
    assert bar.sma_short is not None
    assert bar.sma_long is not None
    assert bar.sma_long_past is not None
    assert bar.atr is not None
    assert bar.avg_turnover is not None
    assert bar.return_20d is not None

    atr_pct = bar.atr / bar.close
    distance_above_sma = (bar.close / bar.sma_short) - 1.0
    return (
        params.min_price <= bar.close <= params.max_price
        and bar.avg_turnover >= params.min_avg_turnover
        and (params.max_avg_turnover is None or bar.avg_turnover < params.max_avg_turnover)
        and bar.sma_short > bar.sma_long
        and bar.close > bar.sma_long
        and bar.sma_long > bar.sma_long_past
        and bar.return_20d >= params.min_return_20d
        and (params.max_return_20d is None or bar.return_20d < params.max_return_20d)
        and bar.touched_sma_short_recently
        and bar.close >= bar.sma_short
        and distance_above_sma <= params.max_distance_above_sma_short
        and params.min_atr_pct <= atr_pct <= params.max_atr_pct
    )


def is_market_context_allowed(context: MarketContext | None, params: SwingParams) -> bool:
    if (
        params.blocked_market_positive_return_20d_min is None
        and params.blocked_market_positive_return_20d_max is None
    ):
        return True
    if context is None or context.positive_return_20d_ratio is None:
        return False
    lower = params.blocked_market_positive_return_20d_min
    upper = params.blocked_market_positive_return_20d_max
    if lower is not None and context.positive_return_20d_ratio < lower:
        return True
    return upper is not None and context.positive_return_20d_ratio >= upper


def _exit_on_bar(position: Position, bar: PreparedBar, params: SwingParams) -> Trade | None:
    if bar.open <= position.stop_price:
        return _close_trade(position, bar.date, bar.open, "gap_stop", params)
    if bar.low <= position.stop_price:
        return _close_trade(position, bar.date, position.stop_price, "stop", params)
    if bar.high >= position.target_price:
        return _close_trade(position, bar.date, position.target_price, "target", params)
    if bar.date >= position.max_exit_date:
        return _close_trade(position, bar.date, bar.close, "max_hold", params)
    return None


def _close_trade(
    position: Position,
    exit_date: date,
    exit_price: float,
    exit_reason: str,
    params: SwingParams,
) -> Trade:
    entry_notional = position.entry_price * position.quantity
    exit_notional = exit_price * position.quantity
    gross_pnl = exit_notional - entry_notional
    costs = (entry_notional + exit_notional) * (params.commission_rate + params.slippage_rate)
    return Trade(
        symbol=position.symbol,
        signal_date=position.signal_date,
        signal_close=position.signal_close,
        signal_sma_short=position.signal_sma_short,
        signal_atr_pct=position.signal_atr_pct,
        signal_return_20d=position.signal_return_20d,
        signal_avg_turnover=position.signal_avg_turnover,
        entry_gap_pct=position.entry_gap_pct,
        entry_date=position.entry_date,
        exit_date=exit_date,
        entry_price=position.entry_price,
        exit_price=exit_price,
        quantity=position.quantity,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=gross_pnl - costs,
        entry_score=position.entry_score,
        ranked_position=position.ranked_position,
        candidate_count=position.candidate_count,
        market_close_above_sma20_ratio=position.market_close_above_sma20_ratio,
        market_trend_breadth_ratio=position.market_trend_breadth_ratio,
        market_positive_return_5d_ratio=position.market_positive_return_5d_ratio,
        market_avg_return_5d=position.market_avg_return_5d,
        market_positive_return_20d_ratio=position.market_positive_return_20d_ratio,
        market_avg_return_20d=position.market_avg_return_20d,
        market_positive_return_60d_ratio=position.market_positive_return_60d_ratio,
        market_avg_return_60d=position.market_avg_return_60d,
    )


def calculate_metrics(trades: list[Trade]) -> Metrics:
    if not trades:
        return Metrics(
            trade_count=0,
            total_net_pnl=0.0,
            win_rate=None,
            profit_factor=None,
            max_drawdown=0.0,
            expectancy=None,
            positive_month_ratio=None,
            worst_month_net_pnl=None,
        )
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    by_month: dict[str, float] = defaultdict(float)
    for trade in trades:
        by_month[trade.exit_date.isoformat()[:7]] += trade.net_pnl
    return Metrics(
        trade_count=len(trades),
        total_net_pnl=sum(trade.net_pnl for trade in trades),
        win_rate=len(wins) / len(trades),
        profit_factor=None if gross_loss == 0 else gross_win / gross_loss,
        max_drawdown=_max_drawdown(trades),
        expectancy=sum(trade.net_pnl for trade in trades) / len(trades),
        positive_month_ratio=(
            None
            if not by_month
            else sum(1 for value in by_month.values() if value > 0) / len(by_month)
        ),
        worst_month_net_pnl=min(by_month.values()) if by_month else None,
    )


def check_gate(
    metrics: Metrics, params: SwingParams, *, label: str = "validation"
) -> dict[str, Any]:
    failures: list[str] = []
    if metrics.total_net_pnl <= 0:
        failures.append(f"{label}_total_net_pnl {metrics.total_net_pnl:.3f} <= 0")
    if metrics.profit_factor is None or metrics.profit_factor <= 1.2:
        failures.append(f"{label}_profit_factor {metrics.profit_factor} <= 1.2")
    max_allowed_drawdown = params.starting_capital * 0.10
    if metrics.max_drawdown >= max_allowed_drawdown:
        failures.append(
            f"{label}_max_drawdown {metrics.max_drawdown:.3f} >= {max_allowed_drawdown:.3f}"
        )
    if metrics.trade_count < 30:
        failures.append(f"{label}_trade_count {metrics.trade_count} < 30")
    if metrics.positive_month_ratio is None or metrics.positive_month_ratio < 0.55:
        failures.append(f"{label}_positive_month_ratio {metrics.positive_month_ratio} < 0.55")
    max_month_loss = -(params.starting_capital * 0.05)
    if metrics.worst_month_net_pnl is None or metrics.worst_month_net_pnl < max_month_loss:
        failures.append(
            f"{label}_worst_month_net_pnl {metrics.worst_month_net_pnl} < {max_month_loss:.3f}"
        )
    return {"status": "FAIL" if failures else "PASS", "failures": failures}


def combine_gates(*gates: dict[str, Any]) -> dict[str, Any]:
    failures = [failure for gate in gates for failure in gate["failures"]]
    return {"status": "FAIL" if failures else "PASS", "failures": failures}


def build_result(
    *,
    candidate: CandidateName,
    selection: SelectionMode,
    random_seed: int,
    params: SwingParams,
    input_path: Path,
    validation_start: date,
    train_trades: list[Trade],
    validation_trades: list[Trade],
    random_baselines: list[dict[str, Any]],
    fold_count: int,
) -> dict[str, Any]:
    train_metrics = calculate_metrics(train_trades)
    validation_metrics = calculate_metrics(validation_trades)
    train_diagnostics = build_diagnostics(train_trades)
    validation_diagnostics = build_diagnostics(validation_trades)
    validation_walk_forward = build_walk_forward_summary(validation_trades, fold_count=fold_count)
    train_gate = check_gate(train_metrics, params, label="train")
    validation_gate = check_gate(validation_metrics, params, label="validation")
    gate = combine_gates(train_gate, validation_gate)
    apply_walk_forward_gate(gate, validation_walk_forward)
    if random_baselines:
        best_random_net = max(row["validation"]["total_net_pnl"] for row in random_baselines)
        if validation_metrics.total_net_pnl <= best_random_net:
            gate["failures"].append(
                "validation_total_net_pnl "
                f"{validation_metrics.total_net_pnl:.3f} <= "
                f"best_random_baseline {best_random_net:.3f}"
            )
            gate["status"] = "FAIL"
    return {
        "strategy": candidate,
        "selection": selection,
        "random_seed": random_seed if selection == "random" else None,
        "input": str(input_path),
        "validation_start": validation_start.isoformat(),
        "params": asdict(params),
        "train": _metrics_dict(train_metrics),
        "validation": _metrics_dict(validation_metrics),
        "train_gate": train_gate,
        "validation_gate": validation_gate,
        "validation_walk_forward": validation_walk_forward,
        "diagnostics": {
            "train": train_diagnostics,
            "validation": validation_diagnostics,
            "bucket_stability": build_bucket_stability_report(
                train_diagnostics,
                validation_diagnostics,
            ),
        },
        "random_baselines": random_baselines,
        "gate": gate,
    }


def build_selection_comparison(
    *,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    candidate: CandidateName,
    input_path: Path,
    validation_start: date,
    fold_count: int,
    random_seeds: list[int],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    specs: list[tuple[str, SelectionMode, int | None]] = [
        ("ranked", "ranked", None),
        ("score_ascending", "score_ascending", None),
        ("score_middle", "score_middle", None),
        ("rank_2_3_first", "rank_2_3_first", None),
    ]
    specs.extend((f"random_seed_{seed}", "random", seed) for seed in random_seeds)

    for label, selection, seed in specs:
        trades = simulate(
            prepared,
            params,
            selection=selection,
            random_seed=1 if seed is None else seed,
        )
        train_trades = [trade for trade in trades if trade.exit_date < validation_start]
        validation_trades = [trade for trade in trades if trade.exit_date >= validation_start]
        validation_metrics = calculate_metrics(validation_trades)
        validation_walk_forward = build_walk_forward_summary(
            validation_trades,
            fold_count=fold_count,
        )
        train_gate = check_gate(calculate_metrics(train_trades), params, label="train")
        validation_gate = check_gate(validation_metrics, params, label="validation")
        gate = combine_gates(train_gate, validation_gate)
        apply_walk_forward_gate(gate, validation_walk_forward)
        rows.append(
            {
                "label": label,
                "selection": selection,
                "random_seed": seed,
                "train": _metrics_dict(calculate_metrics(train_trades)),
                "validation": _metrics_dict(validation_metrics),
                "train_gate": train_gate,
                "validation_gate": validation_gate,
                "validation_walk_forward": validation_walk_forward,
                "gate": gate,
            }
        )

    rows.sort(
        key=lambda row: (
            row["gate"]["status"] != "PASS",
            -float(row["validation"]["total_net_pnl"]),
        )
    )
    return {
        "strategy": candidate,
        "input": str(input_path),
        "validation_start": validation_start.isoformat(),
        "fold_count": fold_count,
        "params": asdict(params),
        "selections": rows,
    }


def build_multi_split_selection_comparison(
    *,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    candidate: CandidateName,
    input_path: Path,
    validation_starts: list[date],
    fold_count: int,
    random_seeds: list[int],
) -> dict[str, Any]:
    split_comparisons = [
        build_selection_comparison(
            prepared=prepared,
            params=params,
            candidate=candidate,
            input_path=input_path,
            validation_start=validation_start,
            fold_count=fold_count,
            random_seeds=random_seeds,
        )
        for validation_start in validation_starts
    ]

    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for comparison in split_comparisons:
        for row in comparison["selections"]:
            by_label[row["label"]].append(row)

    selection_summary: list[dict[str, Any]] = []
    for label, rows in by_label.items():
        validation_fold_ratios = [
            _positive_fold_ratio(row["validation_walk_forward"]) for row in rows
        ]
        worst_months = [
            float(row["validation"]["worst_month_net_pnl"])
            for row in rows
            if row["validation"]["worst_month_net_pnl"] is not None
        ]
        positive_month_ratios = [
            float(row["validation"]["positive_month_ratio"])
            for row in rows
            if row["validation"]["positive_month_ratio"] is not None
        ]
        selection_summary.append(
            {
                "label": label,
                "selection": rows[0]["selection"],
                "random_seed": rows[0]["random_seed"],
                "split_count": len(rows),
                "pass_count": sum(1 for row in rows if row["gate"]["status"] == "PASS"),
                "train_pass_count": sum(1 for row in rows if row["train_gate"]["status"] == "PASS"),
                "validation_pass_count": sum(
                    1 for row in rows if row["validation_gate"]["status"] == "PASS"
                ),
                "total_validation_net_pnl": _round(
                    sum(float(row["validation"]["total_net_pnl"]) for row in rows)
                ),
                "worst_validation_max_drawdown": _round(
                    max(float(row["validation"]["max_drawdown"]) for row in rows)
                ),
                "worst_validation_month_net_pnl": (
                    None if not worst_months else _round(min(worst_months))
                ),
                "min_validation_positive_month_ratio": (
                    None if not positive_month_ratios else _round(min(positive_month_ratios), 4)
                ),
                "min_validation_positive_fold_ratio": _round(min(validation_fold_ratios), 4),
                "failed_splits": [
                    {
                        "validation_start": comparison["validation_start"],
                        "failures": row["gate"]["failures"],
                    }
                    for comparison in split_comparisons
                    for row in comparison["selections"]
                    if row["label"] == label and row["gate"]["status"] == "FAIL"
                ],
            }
        )

    selection_summary.sort(
        key=lambda row: (
            -int(row["pass_count"]),
            -float(row["total_validation_net_pnl"]),
            float(row["worst_validation_max_drawdown"]),
        )
    )
    return {
        "strategy": candidate,
        "input": str(input_path),
        "validation_starts": [item.isoformat() for item in validation_starts],
        "fold_count": fold_count,
        "params": asdict(params),
        "selection_summary": selection_summary,
        "split_comparisons": split_comparisons,
    }


def build_walk_forward_research(
    *,
    rows: list[OhlcvRow],
    input_path: Path,
    capital: float,
    min_avg_turnover: float,
    min_train_days: int,
    oos_block_days: int,
    random_seeds: list[int],
    fold_count: int,
) -> dict[str, Any]:
    if min_train_days <= 0:
        raise ValueError("min_train_days must be positive")
    if oos_block_days <= 0:
        raise ValueError("oos_block_days must be positive")

    dates = sorted({row.date for row in rows})
    if len(dates) <= min_train_days:
        raise ValueError("not enough dates for walk-forward research")

    deterministic_runs = _build_research_runs(
        rows=rows,
        capital=capital,
        min_avg_turnover=min_avg_turnover,
        selections=DETERMINISTIC_SELECTIONS,
    )
    random_runs = _build_research_runs(
        rows=rows,
        capital=capital,
        min_avg_turnover=min_avg_turnover,
        selections=("random",),
        random_seeds=random_seeds,
    )

    blocks: list[dict[str, Any]] = []
    oos_ranges: list[tuple[date, date]] = []
    selected_oos_trades: list[Trade] = []
    selected_train_pass_count = 0
    selected_oos_pass_count = 0
    last_full_block_start = len(dates) - oos_block_days
    for block_number, start_idx in enumerate(
        range(min_train_days, last_full_block_start + 1, oos_block_days),
        start=1,
    ):
        end_idx = start_idx + oos_block_days
        if start_idx >= end_idx:
            continue
        oos_start = dates[start_idx]
        oos_end = dates[end_idx - 1]
        oos_ranges.append((oos_start, oos_end))
        selected = _select_walk_forward_run(deterministic_runs, oos_start)
        selected_train_gate = selected["train_gate"]
        if selected_train_gate["status"] == "PASS":
            selected_train_pass_count += 1
        selected_oos_trades_for_block = _oos_block_trades(
            selected["trades"],
            oos_start=oos_start,
            oos_end=oos_end,
        )
        selected_oos_trades.extend(selected_oos_trades_for_block)
        selected_oos_metrics = calculate_metrics(selected_oos_trades_for_block)
        selected_oos_gate = check_gate(
            selected_oos_metrics,
            selected["params"],
            label="oos",
        )
        if selected_oos_gate["status"] == "PASS":
            selected_oos_pass_count += 1

        random_baselines = [
            {
                "label": run["label"],
                "candidate": run["candidate"],
                "selection": run["selection"],
                "random_seed": run["random_seed"],
                "oos": _metrics_dict(
                    calculate_metrics(
                        _oos_block_trades(run["trades"], oos_start=oos_start, oos_end=oos_end)
                    )
                ),
            }
            for run in random_runs
        ]
        blocks.append(
            {
                "block": block_number,
                "train_end": dates[start_idx - 1].isoformat(),
                "oos_start": oos_start.isoformat(),
                "oos_end": oos_end.isoformat(),
                "selected_label": selected["label"],
                "selected_candidate": selected["candidate"],
                "selected_selection": selected["selection"],
                "selection_reason": selected["selection_reason"],
                "selected_train": _metrics_dict(selected["train_metrics"]),
                "selected_train_gate": selected_train_gate,
                "selected_oos": _metrics_dict(selected_oos_metrics),
                "selected_oos_gate": selected_oos_gate,
                "best_random_oos_net_pnl": (
                    None
                    if not random_baselines
                    else max(row["oos"]["total_net_pnl"] for row in random_baselines)
                ),
                "random_baselines": random_baselines,
            }
        )

    selected_oos_metrics = calculate_metrics(selected_oos_trades)
    selected_oos_walk_forward = build_walk_forward_summary(
        selected_oos_trades,
        fold_count=fold_count,
    )
    selected_oos_gate = check_gate(
        selected_oos_metrics,
        SwingParams(starting_capital=capital, min_avg_turnover=min_avg_turnover),
        label="selected_oos",
    )
    apply_walk_forward_gate(selected_oos_gate, selected_oos_walk_forward, label="selected_oos")
    random_oos_summaries = build_random_oos_summaries(random_runs, oos_ranges)
    research_gate = build_walk_forward_research_gate(
        selected_oos_gate=selected_oos_gate,
        block_count=len(blocks),
        selected_train_pass_count=selected_train_pass_count,
        selected_oos_pass_count=selected_oos_pass_count,
        selected_oos_metrics=selected_oos_metrics,
        random_oos_summaries=random_oos_summaries,
    )
    return {
        "mode": "walk_forward_research",
        "input": str(input_path),
        "min_train_days": min_train_days,
        "oos_block_days": oos_block_days,
        "candidates": list(RESEARCH_CANDIDATES),
        "deterministic_selections": list(DETERMINISTIC_SELECTIONS),
        "random_baseline_seeds": random_seeds,
        "block_count": len(blocks),
        "selected_train_pass_count": selected_train_pass_count,
        "selected_oos_pass_count": selected_oos_pass_count,
        "selected_oos": _metrics_dict(selected_oos_metrics),
        "selected_oos_gate": selected_oos_gate,
        "research_gate": research_gate,
        "selected_oos_walk_forward": selected_oos_walk_forward,
        "random_oos_summaries": random_oos_summaries,
        "blocks": blocks,
    }


def _build_research_runs(
    *,
    rows: list[OhlcvRow],
    capital: float,
    min_avg_turnover: float,
    selections: tuple[SelectionMode, ...],
    random_seeds: list[int] | None = None,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    seeds = [None] if random_seeds is None else random_seeds
    for candidate in RESEARCH_CANDIDATES:
        params = params_for_candidate(candidate, capital, min_avg_turnover)
        prepared = prepare_bars(rows, params)
        for selection in selections:
            for seed in seeds:
                if selection != "random" and seed is not None:
                    continue
                random_seed = 1 if seed is None else seed
                label = (
                    f"{candidate}:{selection}"
                    if selection != "random"
                    else f"{candidate}:random_seed_{random_seed}"
                )
                runs.append(
                    {
                        "label": label,
                        "candidate": candidate,
                        "selection": selection,
                        "random_seed": None if selection != "random" else random_seed,
                        "params": params,
                        "trades": simulate(
                            prepared,
                            params,
                            selection=selection,
                            random_seed=random_seed,
                        ),
                    }
                )
    return runs


def _select_walk_forward_run(
    runs: list[dict[str, Any]],
    oos_start: date,
) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    for run in runs:
        train_trades = _train_trades_before(run["trades"], oos_start)
        train_metrics = calculate_metrics(train_trades)
        train_gate = check_gate(train_metrics, run["params"], label="train")
        evaluated.append(
            {
                **run,
                "train_metrics": train_metrics,
                "train_gate": train_gate,
            }
        )

    train_passes = [row for row in evaluated if row["train_gate"]["status"] == "PASS"]
    if train_passes:
        selected = max(train_passes, key=_walk_forward_selection_key)
        return {**selected, "selection_reason": "best_train_gate_pass"}
    selected = max(evaluated, key=_walk_forward_selection_key)
    return {**selected, "selection_reason": "forced_best_train_no_gate_pass"}


def _walk_forward_selection_key(row: dict[str, Any]) -> tuple[float, float, float]:
    metrics: Metrics = row["train_metrics"]
    profit_factor = -1.0 if metrics.profit_factor is None else metrics.profit_factor
    return (
        metrics.total_net_pnl,
        profit_factor,
        -metrics.max_drawdown,
    )


def _train_trades_before(trades: list[Trade], oos_start: date) -> list[Trade]:
    return [trade for trade in trades if trade.exit_date < oos_start]


def _oos_block_trades(
    trades: list[Trade],
    *,
    oos_start: date,
    oos_end: date,
) -> list[Trade]:
    return [
        trade for trade in trades if trade.entry_date >= oos_start and trade.exit_date <= oos_end
    ]


def build_random_oos_summaries(
    random_runs: list[dict[str, Any]],
    oos_ranges: list[tuple[date, date]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in random_runs:
        trades = [
            trade
            for oos_start, oos_end in oos_ranges
            for trade in _oos_block_trades(run["trades"], oos_start=oos_start, oos_end=oos_end)
        ]
        rows.append(
            {
                "label": run["label"],
                "candidate": run["candidate"],
                "selection": run["selection"],
                "random_seed": run["random_seed"],
                "oos": _metrics_dict(calculate_metrics(trades)),
            }
        )
    rows.sort(key=lambda row: -float(row["oos"]["total_net_pnl"]))
    return rows


def build_walk_forward_research_gate(
    *,
    selected_oos_gate: dict[str, Any],
    block_count: int,
    selected_train_pass_count: int,
    selected_oos_pass_count: int,
    selected_oos_metrics: Metrics,
    random_oos_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    failures = list(selected_oos_gate["failures"])
    if selected_train_pass_count < block_count:
        failures.append(f"selected_train_pass_count {selected_train_pass_count} < {block_count}")
    min_oos_pass_count = max(1, (block_count * 2 + 2) // 3)
    if selected_oos_pass_count < min_oos_pass_count:
        failures.append(f"selected_oos_pass_count {selected_oos_pass_count} < {min_oos_pass_count}")
    if random_oos_summaries:
        best_random_net = max(row["oos"]["total_net_pnl"] for row in random_oos_summaries)
        if selected_oos_metrics.total_net_pnl <= best_random_net:
            failures.append(
                "selected_oos_total_net_pnl "
                f"{selected_oos_metrics.total_net_pnl:.3f} <= "
                f"best_random_oos {best_random_net:.3f}"
            )
    return {"status": "FAIL" if failures else "PASS", "failures": failures}


def build_random_baselines(
    *,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    validation_start: date,
    seeds: list[int],
    fold_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        trades = simulate(prepared, params, selection="random", random_seed=seed)
        train_trades = [trade for trade in trades if trade.exit_date < validation_start]
        validation_trades = [trade for trade in trades if trade.exit_date >= validation_start]
        rows.append(
            {
                "seed": seed,
                "train": _metrics_dict(calculate_metrics(train_trades)),
                "validation": _metrics_dict(calculate_metrics(validation_trades)),
                "validation_walk_forward": build_walk_forward_summary(
                    validation_trades,
                    fold_count=fold_count,
                ),
            }
        )
    return rows


def parse_seed_list(raw: str) -> list[int]:
    if not raw.strip():
        return []
    return [int(item) for item in raw.split(",") if item.strip()]


def parse_date_list(raw: str) -> list[date]:
    if not raw.strip():
        return []
    return [date.fromisoformat(item.strip()) for item in raw.split(",") if item.strip()]


def build_walk_forward_summary(trades: list[Trade], *, fold_count: int) -> dict[str, Any]:
    if fold_count <= 0:
        raise ValueError("fold_count must be positive")
    if not trades:
        return {
            "fold_count": 0,
            "positive_fold_count": 0,
            "negative_fold_count": 0,
            "folds": [],
        }

    dates = sorted({trade.exit_date for trade in trades})
    effective_fold_count = min(fold_count, len(dates))
    folds: list[dict[str, Any]] = []
    for idx in range(effective_fold_count):
        start_idx = (idx * len(dates)) // effective_fold_count
        end_idx = ((idx + 1) * len(dates)) // effective_fold_count
        fold_dates = set(dates[start_idx:end_idx])
        fold_trades = [trade for trade in trades if trade.exit_date in fold_dates]
        metrics = calculate_metrics(fold_trades)
        folds.append(
            {
                "fold": idx + 1,
                "start_date": dates[start_idx].isoformat(),
                "end_date": dates[end_idx - 1].isoformat(),
                "metrics": _metrics_dict(metrics),
            }
        )

    return {
        "fold_count": effective_fold_count,
        "positive_fold_count": sum(1 for fold in folds if fold["metrics"]["total_net_pnl"] > 0),
        "negative_fold_count": sum(1 for fold in folds if fold["metrics"]["total_net_pnl"] < 0),
        "folds": folds,
    }


def apply_walk_forward_gate(
    gate: dict[str, Any],
    summary: dict[str, Any],
    *,
    label: str = "validation",
) -> None:
    fold_count = int(summary["fold_count"])
    if fold_count <= 1:
        return
    min_positive_folds = max(1, (fold_count * 2 + 2) // 3)
    positive_fold_count = int(summary["positive_fold_count"])
    if positive_fold_count < min_positive_folds:
        gate["failures"].append(
            f"{label}_positive_fold_count {positive_fold_count} < {min_positive_folds}"
        )
        gate["status"] = "FAIL"


def _positive_fold_ratio(summary: dict[str, Any]) -> float:
    fold_count = int(summary["fold_count"])
    if fold_count == 0:
        return 0.0
    return int(summary["positive_fold_count"]) / fold_count


def _round(value: float, digits: int = 3) -> float:
    return round(value, digits)


def _true_ratio(values: list[bool]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value) / len(values)


def _row_expectancy(row: dict[str, Any]) -> float:
    return _round(float(row["net_pnl"]) / int(row["trade_count"]))


def _same_sign(left: float, right: float) -> bool:
    return (left < 0 and right < 0) or (left > 0 and right > 0) or (left == 0 and right == 0)


def build_diagnostics(trades: list[Trade]) -> dict[str, Any]:
    return {
        "monthly": _grouped_trade_summary(
            trades, key_fn=lambda trade: trade.exit_date.isoformat()[:7]
        ),
        "exit_reasons": _grouped_trade_summary(trades, key_fn=lambda trade: trade.exit_reason),
        "entry_gap_pct_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _pct_bin(trade.entry_gap_pct, ENTRY_GAP_BINS)
        ),
        "signal_atr_pct_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _pct_bin(trade.signal_atr_pct, ATR_PCT_BINS)
        ),
        "signal_return_20d_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _pct_bin(trade.signal_return_20d, RETURN_20D_BINS)
        ),
        "distance_above_sma20_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _pct_bin(
                (trade.signal_close / trade.signal_sma_short) - 1.0,
                DISTANCE_ABOVE_SMA_BINS,
            ),
        ),
        "signal_avg_turnover_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _turnover_bin(trade.signal_avg_turnover)
        ),
        "entry_score_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _score_bin(trade.entry_score)
        ),
        "ranked_position_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _rank_bin(trade.ranked_position)
        ),
        "candidate_count_bins": _grouped_trade_summary(
            trades, key_fn=lambda trade: _candidate_count_bin(trade.candidate_count)
        ),
        "market_close_above_sma20_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_close_above_sma20_ratio,
                (0.35, 0.45, 0.55, 0.65),
            ),
        ),
        "market_trend_breadth_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_trend_breadth_ratio,
                (0.25, 0.35, 0.45, 0.55),
            ),
        ),
        "market_positive_return_5d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_positive_return_5d_ratio,
                (0.35, 0.45, 0.55, 0.65),
            ),
        ),
        "market_avg_return_5d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_avg_return_5d,
                (-0.03, -0.01, 0.0, 0.01, 0.03),
            ),
        ),
        "market_positive_return_20d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_positive_return_20d_ratio,
                (0.35, 0.45, 0.55, 0.65),
            ),
        ),
        "market_avg_return_20d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_avg_return_20d,
                (-0.03, -0.01, 0.0, 0.01, 0.03),
            ),
        ),
        "market_positive_return_60d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_positive_return_60d_ratio,
                (0.35, 0.45, 0.55, 0.65),
            ),
        ),
        "market_avg_return_60d_bins": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: _optional_pct_bin(
                trade.market_avg_return_60d,
                (-0.06, -0.03, 0.0, 0.03, 0.06),
            ),
        ),
        "worst_symbols": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: trade.symbol,
            limit=12,
            sort_by="net_pnl_asc",
        ),
        "best_symbols": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: trade.symbol,
            limit=12,
            sort_by="net_pnl_desc",
        ),
        "worst_trades": [
            _trade_dict(trade) for trade in sorted(trades, key=lambda item: item.net_pnl)[:12]
        ],
        "max_drawdown_period": _drawdown_period_dict(_max_drawdown_period(trades)),
    }


def build_bucket_stability_report(
    train_diagnostics: dict[str, Any],
    validation_diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for diagnostic_key in BUCKET_DIAGNOSTIC_KEYS:
        train_rows = {str(row["key"]): row for row in train_diagnostics.get(diagnostic_key, [])}
        validation_rows = {
            str(row["key"]): row for row in validation_diagnostics.get(diagnostic_key, [])
        }
        for bucket_key in sorted(train_rows.keys() & validation_rows.keys()):
            train_row = train_rows[bucket_key]
            validation_row = validation_rows[bucket_key]
            train_expectancy = _row_expectancy(train_row)
            validation_expectancy = _row_expectancy(validation_row)
            rows.append(
                {
                    "diagnostic": diagnostic_key,
                    "bucket": bucket_key,
                    "train_trade_count": train_row["trade_count"],
                    "validation_trade_count": validation_row["trade_count"],
                    "train_net_pnl": train_row["net_pnl"],
                    "validation_net_pnl": validation_row["net_pnl"],
                    "train_expectancy": train_expectancy,
                    "validation_expectancy": validation_expectancy,
                    "combined_net_pnl": _round(
                        float(train_row["net_pnl"]) + float(validation_row["net_pnl"])
                    ),
                    "same_sign": _same_sign(train_expectancy, validation_expectancy),
                }
            )

    rows.sort(
        key=lambda row: (
            not bool(row["same_sign"]),
            float(row["combined_net_pnl"]),
            -int(row["train_trade_count"]) - int(row["validation_trade_count"]),
        )
    )
    return rows


def write_trades(trades: list[Trade], path: Path) -> None:
    fieldnames = [
        "symbol",
        "signal_date",
        "signal_close",
        "signal_sma_short",
        "signal_atr_pct",
        "signal_return_20d",
        "signal_avg_turnover",
        "entry_gap_pct",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "quantity",
        "exit_reason",
        "gross_pnl",
        "costs",
        "net_pnl",
        "entry_score",
        "ranked_position",
        "candidate_count",
        "market_close_above_sma20_ratio",
        "market_trend_breadth_ratio",
        "market_positive_return_5d_ratio",
        "market_avg_return_5d",
        "market_positive_return_20d_ratio",
        "market_avg_return_20d",
        "market_positive_return_60d_ratio",
        "market_avg_return_60d",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            row = asdict(trade)
            row["entry_date"] = trade.entry_date.isoformat()
            row["exit_date"] = trade.exit_date.isoformat()
            writer.writerow(row)


def default_validation_start(rows: list[OhlcvRow]) -> date:
    dates = sorted({row.date for row in rows})
    if not dates:
        raise ValueError("input has no rows")
    return dates[int(len(dates) * 0.70)]


def _position_size(
    *,
    entry_price: float,
    stop_price: float,
    equity: float,
    params: SwingParams,
) -> int:
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return 0
    risk_qty = int((equity * params.risk_per_trade_pct) // risk_per_share)
    notional_qty = int((equity * params.max_notional_per_position_pct) // entry_price)
    quantity = min(risk_qty, notional_qty)
    return (quantity // params.lot_size) * params.lot_size


def _entry_score(bar: PreparedBar) -> float:
    assert bar.return_20d is not None
    assert bar.sma_short is not None
    distance_above_sma = max((bar.close / bar.sma_short) - 1.0, 0.0)
    turnover_score = 0.0 if bar.avg_turnover is None else min(bar.avg_turnover / 1e9, 2.0)
    return bar.return_20d - distance_above_sma + turnover_score * 0.05


def _nth_symbol_date(bars: list[PreparedBar], start_date: date, n: int) -> date:
    dates = [bar.date for bar in bars]
    try:
        start_idx = dates.index(start_date)
    except ValueError as exc:
        raise ValueError(f"start_date not found: {start_date}") from exc
    return dates[min(start_idx + n, len(dates) - 1)]


def _sma(values: list[float], idx: int, period: int) -> float | None:
    if idx < 0 or idx + 1 < period:
        return None
    window = values[idx + 1 - period : idx + 1]
    return sum(window) / period


def _atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    idx: int,
    period: int,
) -> float | None:
    if idx < period:
        return None
    ranges: list[float] = []
    for item_idx in range(idx + 1 - period, idx + 1):
        previous_close = closes[item_idx - 1]
        ranges.append(
            max(
                highs[item_idx] - lows[item_idx],
                abs(highs[item_idx] - previous_close),
                abs(lows[item_idx] - previous_close),
            )
        )
    return sum(ranges) / period


def _return(values: list[float], idx: int, period: int) -> float | None:
    if idx < period:
        return None
    base = values[idx - period]
    if base <= 0:
        return None
    return (values[idx] / base) - 1.0


def _touched_sma_recently(
    *,
    lows: list[float],
    closes: list[float],
    idx: int,
    period: int,
    lookback: int,
    tolerance: float,
) -> bool:
    start = max(0, idx + 1 - lookback)
    for item_idx in range(start, idx + 1):
        sma = _sma(closes, item_idx, period)
        if sma is not None and lows[item_idx] <= sma * (1.0 + tolerance):
            return True
    return False


def _max_drawdown(trades: list[Trade]) -> float:
    return _max_drawdown_period(trades).amount


def _max_drawdown_period(trades: list[Trade]) -> DrawdownPeriod:
    equity = 0.0
    peak = 0.0
    peak_date: date | None = None
    max_drawdown = 0.0
    max_peak = 0.0
    max_trough = 0.0
    max_peak_date: date | None = None
    max_trough_date: date | None = None
    for trade in sorted(trades, key=lambda item: (item.exit_date, item.symbol)):
        equity += trade.net_pnl
        if equity > peak:
            peak = equity
            peak_date = trade.exit_date
        drawdown = peak - equity
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_peak = peak
            max_trough = equity
            max_peak_date = peak_date
            max_trough_date = trade.exit_date
    return DrawdownPeriod(
        amount=max_drawdown,
        peak_date=max_peak_date,
        trough_date=max_trough_date,
        peak_equity=max_peak,
        trough_equity=max_trough,
    )


def _grouped_trade_summary(
    trades: list[Trade],
    *,
    key_fn: Callable[[Trade], str],
    limit: int | None = None,
    sort_by: str = "key_asc",
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[str(key_fn(trade))].append(trade)
    rows = [
        {
            "key": key,
            "trade_count": len(group),
            "net_pnl": round(sum(trade.net_pnl for trade in group), 3),
            "gross_pnl": round(sum(trade.gross_pnl for trade in group), 3),
            "costs": round(sum(trade.costs for trade in group), 3),
            "win_rate": round(
                sum(1 for trade in group if trade.net_pnl > 0) / len(group),
                4,
            ),
        }
        for key, group in grouped.items()
    ]
    if sort_by == "net_pnl_asc":
        rows.sort(key=lambda row: (row["net_pnl"], row["key"]))
    elif sort_by == "net_pnl_desc":
        rows.sort(key=lambda row: (-row["net_pnl"], row["key"]))
    else:
        rows.sort(key=lambda row: row["key"])
    return rows if limit is None else rows[:limit]


def _pct_bin(value: float, cutoffs: tuple[float, ...]) -> str:
    lower: float | None = None
    for cutoff in cutoffs:
        if value < cutoff:
            return f"{_pct_label(lower)}..{_pct_label(cutoff)}"
        lower = cutoff
    return f"{_pct_label(lower)}..inf"


def _optional_pct_bin(value: float | None, cutoffs: tuple[float, ...]) -> str:
    if value is None:
        return "missing"
    return _pct_bin(value, cutoffs)


def _pct_label(value: float | None) -> str:
    if value is None:
        return "-inf"
    return f"{value * 100:.1f}%"


def _turnover_bin(value: float) -> str:
    if value < 300_000_000:
        return "0.2B..0.3B"
    if value < 500_000_000:
        return "0.3B..0.5B"
    if value < 1_000_000_000:
        return "0.5B..1.0B"
    if value < 3_000_000_000:
        return "1.0B..3.0B"
    return "3.0B..inf"


def _score_bin(value: float) -> str:
    if value < 0.10:
        return "-inf..0.10"
    if value < 0.15:
        return "0.10..0.15"
    if value < 0.20:
        return "0.15..0.20"
    if value < 0.30:
        return "0.20..0.30"
    return "0.30..inf"


def _rank_bin(value: int) -> str:
    if value <= 1:
        return "1"
    if value <= 3:
        return "2..3"
    if value <= 5:
        return "4..5"
    return "6..inf"


def _candidate_count_bin(value: int) -> str:
    if value <= 1:
        return "1"
    if value <= 3:
        return "2..3"
    if value <= 5:
        return "4..5"
    return "6..inf"


def _metrics_dict(metrics: Metrics) -> dict[str, Any]:
    return {
        "trade_count": metrics.trade_count,
        "total_net_pnl": round(metrics.total_net_pnl, 3),
        "win_rate": None if metrics.win_rate is None else round(metrics.win_rate, 4),
        "profit_factor": (
            None if metrics.profit_factor is None else round(metrics.profit_factor, 4)
        ),
        "max_drawdown": round(metrics.max_drawdown, 3),
        "expectancy": None if metrics.expectancy is None else round(metrics.expectancy, 3),
        "positive_month_ratio": (
            None if metrics.positive_month_ratio is None else round(metrics.positive_month_ratio, 4)
        ),
        "worst_month_net_pnl": (
            None if metrics.worst_month_net_pnl is None else round(metrics.worst_month_net_pnl, 3)
        ),
    }


def _drawdown_period_dict(period: DrawdownPeriod) -> dict[str, Any]:
    return {
        "amount": round(period.amount, 3),
        "peak_date": None if period.peak_date is None else period.peak_date.isoformat(),
        "trough_date": None if period.trough_date is None else period.trough_date.isoformat(),
        "peak_equity": round(period.peak_equity, 3),
        "trough_equity": round(period.trough_equity, 3),
    }


def _trade_dict(trade: Trade) -> dict[str, Any]:
    row = asdict(trade)
    row["signal_date"] = trade.signal_date.isoformat()
    row["entry_date"] = trade.entry_date.isoformat()
    row["exit_date"] = trade.exit_date.isoformat()
    for key in (
        "signal_close",
        "signal_sma_short",
        "signal_atr_pct",
        "signal_return_20d",
        "signal_avg_turnover",
        "entry_gap_pct",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "costs",
        "net_pnl",
        "entry_score",
        "market_close_above_sma20_ratio",
        "market_trend_breadth_ratio",
        "market_positive_return_5d_ratio",
        "market_avg_return_5d",
        "market_positive_return_20d_ratio",
        "market_avg_return_20d",
        "market_positive_return_60d_ratio",
        "market_avg_return_60d",
    ):
        row[key] = None if row[key] is None else round(row[key], 3)
    return row


def _float_field(raw: dict[str, Any], key: str) -> float:
    value = raw.get(key)
    if value in (None, ""):
        raise ValueError(f"missing {key}: {raw}")
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
