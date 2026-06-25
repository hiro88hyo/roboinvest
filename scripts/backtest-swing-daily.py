#!/usr/bin/env python3
"""Backtest preregistered daily OHLCV swing strategy candidates.

The first candidate is intentionally fixed as ``daily_trend_pullback_v0``.
Do not use this script as a parameter optimizer; change parameters only after
documenting a new candidate in docs/features/swing-rebuild-plan.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

SelectionMode = Literal[
    "ranked",
    "random",
    "score_ascending",
    "score_middle",
    "rank_2_3_first",
    "stable_hash",
]
EntryMode = Literal["trend_pullback", "breakout_continuation"]
ExitMode = Literal["target_stop_max_hold", "fixed_hold"]

CandidateName = Literal[
    "daily_trend_pullback_v0",
    "daily_trend_pullback_v1",
    "daily_trend_pullback_v2",
    "daily_trend_pullback_v3",
    "daily_trend_pullback_v4",
    "daily_trend_pullback_v5",
    "daily_trend_pullback_exit_fixed10_v0",
    "daily_trend_pullback_fixed10_hash_v0",
    "daily_breakout_continuation_v0",
]
BaselineKind = Literal[
    "strategy",
    "signal_set_random",
    "universe_date_matched_random",
    "symbol_matched_random_date",
]
MarketRegime = Literal[
    "broad_uptrend",
    "narrow_leadership",
    "transition_chop",
    "broad_downtrend",
]

RESEARCH_CANDIDATES: tuple[CandidateName, ...] = (
    "daily_trend_pullback_v0",
    "daily_trend_pullback_v1",
    "daily_trend_pullback_v2",
    "daily_trend_pullback_v3",
    "daily_trend_pullback_v4",
    "daily_trend_pullback_v5",
    "daily_trend_pullback_exit_fixed10_v0",
    "daily_trend_pullback_fixed10_hash_v0",
    "daily_breakout_continuation_v0",
)
DETERMINISTIC_SELECTIONS: tuple[SelectionMode, ...] = (
    "ranked",
    "score_ascending",
    "score_middle",
    "rank_2_3_first",
)
FIXED_EXIT_SELECTIONS: tuple[SelectionMode, ...] = ("ranked", "rank_2_3_first")
HASH_BASKET_SELECTIONS: tuple[SelectionMode, ...] = ("stable_hash",)
RANDOM_BASELINE_KINDS: tuple[BaselineKind, ...] = (
    "signal_set_random",
    "universe_date_matched_random",
    "symbol_matched_random_date",
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
    entry_mode: EntryMode = "trend_pullback"
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
    min_return_60d: float | None = None
    pullback_lookback: int = 5
    pullback_sma_tolerance: float = 0.01
    max_distance_above_sma_short: float = 0.04
    min_atr_pct: float = 0.015
    max_atr_pct: float = 0.08
    breakout_lookback: int = 60
    breakout_buffer_pct: float = 0.0
    min_turnover_multiple: float | None = None
    max_prior_range_20d_pct: float | None = None
    min_entry_gap_pct: float | None = None
    max_entry_gap_pct: float | None = None
    blocked_market_positive_return_20d_min: float | None = None
    blocked_market_positive_return_20d_max: float | None = None
    stop_atr_multiple: float = 1.5
    target_r_multiple: float = 2.0
    max_hold_days: int = 10
    exit_mode: ExitMode = "target_stop_max_hold"
    starting_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 0.01
    max_notional_per_position_pct: float = 0.20
    max_positions: int = 5
    max_new_positions_per_day: int | None = None
    lot_size: int = 100
    commission_rate: float = 0.00099
    slippage_rate: float = 0.0005


@dataclass(frozen=True, slots=True)
class ExecutionStress:
    exit_before_entry_at_open: bool = False
    limit_down_unfillable: bool = False
    limit_down_threshold_pct: float = 0.15
    gap_stop_additional_slippage_rate: float = 0.0


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
    prior_high_breakout: float | None = None
    prior_range_20d_pct: float | None = None


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
    market_regime: MarketRegime | None = None


@dataclass(frozen=True, slots=True)
class EntryTemplate:
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
    score: float
    market_close_above_sma20_ratio: float | None = None
    market_trend_breadth_ratio: float | None = None
    market_positive_return_5d_ratio: float | None = None
    market_avg_return_5d: float | None = None
    market_positive_return_20d_ratio: float | None = None
    market_avg_return_20d: float | None = None
    market_positive_return_60d_ratio: float | None = None
    market_avg_return_60d: float | None = None
    market_regime: MarketRegime | None = None


@dataclass(frozen=True, slots=True)
class CandidatePools:
    signal_by_date: dict[date, list[EntryTemplate]]
    tradable_by_date: dict[date, list[EntryTemplate]]
    tradable_dates_by_symbol: dict[str, list[date]]


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
    market_regime: MarketRegime | None = None


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
    market_regime: MarketRegime | None = None


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
    regime: MarketRegime = "transition_chop"


@dataclass(frozen=True, slots=True)
class SimulationContext:
    by_date: dict[date, dict[str, PreparedBar]]
    previous_by_symbol: dict[tuple[str, date], PreparedBar]
    market_context_by_date: dict[date, MarketContext]


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
            random_baseline_kinds=parse_baseline_kind_list(args.random_baseline_kinds),
            candidates=parse_candidate_list(args.research_candidates),
            fold_count=args.walk_forward_folds,
            execution_stress=execution_stress_from_args(args),
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
                random_baseline_kinds=parse_baseline_kind_list(args.random_baseline_kinds),
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
                random_baseline_kinds=parse_baseline_kind_list(args.random_baseline_kinds),
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
        execution_stress=execution_stress_from_args(args),
    )
    train_trades = [trade for trade in trades if trade.exit_date < validation_start]
    validation_trades = [trade for trade in trades if trade.exit_date >= validation_start]
    random_baselines = build_random_baselines(
        prepared=prepared,
        params=params,
        validation_start=validation_start,
        seeds=parse_seed_list(args.random_baseline_seeds),
        baseline_kinds=parse_baseline_kind_list(args.random_baseline_kinds),
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
    result["alpha_diagnostics"] = build_alpha_diagnostics(
        prepared=prepared,
        params=params,
        selection=args.selection,
        random_seed=args.random_seed,
        trades=trades,
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
        print(f"random_baseline: runs={len(random_baselines)} best_validation_net={best_random}")
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
            "daily_trend_pullback_exit_fixed10_v0",
            "daily_trend_pullback_fixed10_hash_v0",
            "daily_breakout_continuation_v0",
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
        choices=(
            "ranked",
            "random",
            "score_ascending",
            "score_middle",
            "rank_2_3_first",
            "stable_hash",
        ),
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
        "--random-baseline-kinds",
        default=",".join(RANDOM_BASELINE_KINDS),
        help=(
            "Comma-separated random baseline kinds: signal_set_random, "
            "universe_date_matched_random, symbol_matched_random_date."
        ),
    )
    parser.add_argument(
        "--research-candidates",
        default="",
        help=(
            "Comma-separated candidate names for --walk-forward-research. "
            "Defaults to all registered research candidates."
        ),
    )
    parser.add_argument(
        "--exit-before-entry-at-open",
        action="store_true",
        help="Stress fill model by processing exits before same-day entries.",
    )
    parser.add_argument(
        "--limit-down-unfillable-stress",
        action="store_true",
        help="Stress gap stops by filling severe gap-down exits at same-day close.",
    )
    parser.add_argument(
        "--gap-stop-additional-slippage-rate",
        type=float,
        default=0.0,
        help="Additional one-way slippage applied only to gap_stop exits.",
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
    if candidate == "daily_trend_pullback_exit_fixed10_v0":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
            max_new_positions_per_day=1,
            max_hold_days=10,
            exit_mode="fixed_hold",
        )
    if candidate == "daily_trend_pullback_fixed10_hash_v0":
        return SwingParams(
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.01,
            max_new_positions_per_day=1,
            risk_per_trade_pct=0.0035,
            max_notional_per_position_pct=0.08,
            max_hold_days=10,
            exit_mode="fixed_hold",
        )
    if candidate == "daily_breakout_continuation_v0":
        return SwingParams(
            entry_mode="breakout_continuation",
            starting_capital=capital,
            min_avg_turnover=min_avg_turnover,
            min_return_20d=0.08,
            max_return_20d=0.35,
            min_return_60d=0.10,
            max_distance_above_sma_short=0.18,
            breakout_lookback=60,
            breakout_buffer_pct=0.0,
            min_turnover_multiple=1.20,
            max_prior_range_20d_pct=0.28,
            min_entry_gap_pct=0.0,
            max_entry_gap_pct=0.03,
            max_new_positions_per_day=1,
        )
    raise ValueError(f"unknown candidate: {candidate}")


def deterministic_selections_for_candidate(
    candidate: CandidateName,
) -> tuple[SelectionMode, ...]:
    if candidate == "daily_trend_pullback_fixed10_hash_v0":
        return HASH_BASKET_SELECTIONS
    if candidate == "daily_trend_pullback_exit_fixed10_v0":
        return FIXED_EXIT_SELECTIONS
    return DETERMINISTIC_SELECTIONS


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
            prior_high_breakout = _prior_high(highs, idx, params.breakout_lookback)
            prior_range_20d_pct = _prior_range_pct(highs, lows, closes, idx, 20)
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
                    prior_high_breakout=prior_high_breakout,
                    prior_range_20d_pct=prior_range_20d_pct,
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
        contexts[item_date] = replace(
            contexts[item_date],
            regime=classify_market_regime(contexts[item_date]),
        )
    return contexts


def build_simulation_context(prepared: dict[str, list[PreparedBar]]) -> SimulationContext:
    by_date: dict[date, dict[str, PreparedBar]] = defaultdict(dict)
    previous_by_symbol: dict[tuple[str, date], PreparedBar] = {}
    for symbol, bars in prepared.items():
        for idx, bar in enumerate(bars):
            by_date[bar.date][symbol] = bar
            if idx > 0:
                previous_by_symbol[(symbol, bar.date)] = bars[idx - 1]
    return SimulationContext(
        by_date=dict(by_date),
        previous_by_symbol=previous_by_symbol,
        market_context_by_date=build_market_context_by_date(prepared),
    )


def classify_market_regime(context: MarketContext) -> MarketRegime:
    trend = context.trend_breadth_ratio
    breadth_20d = context.positive_return_20d_ratio
    avg_20d = context.avg_return_20d
    breadth_60d = context.positive_return_60d_ratio
    if trend is None or breadth_20d is None or avg_20d is None:
        return "transition_chop"
    if trend >= 0.55 and breadth_20d >= 0.60 and avg_20d > 0:
        return "broad_uptrend"
    if trend < 0.35 and breadth_20d < 0.45 and avg_20d < 0:
        return "broad_downtrend"
    if (
        trend < 0.45
        and breadth_20d >= 0.50
        and avg_20d > 0
        and (breadth_60d is None or breadth_60d >= 0.45)
    ):
        return "narrow_leadership"
    return "transition_chop"


def build_candidate_pools(
    *,
    context: SimulationContext,
    params: SwingParams,
) -> CandidatePools:
    signal_by_date: dict[date, list[EntryTemplate]] = {}
    tradable_by_date: dict[date, list[EntryTemplate]] = {}
    tradable_dates_by_symbol: dict[str, list[date]] = defaultdict(list)
    for current_date, today in context.by_date.items():
        signal_templates: list[EntryTemplate] = []
        tradable_templates: list[EntryTemplate] = []
        for symbol, bar in sorted(today.items()):
            signal_bar = context.previous_by_symbol.get((symbol, current_date))
            if signal_bar is None:
                continue
            if _is_random_baseline_tradable(signal_bar, params):
                tradable_template = _entry_template(
                    symbol=symbol,
                    bar=bar,
                    signal_bar=signal_bar,
                    market_context=context.market_context_by_date.get(signal_bar.date),
                    params=params,
                    apply_entry_filters=False,
                )
                if tradable_template is not None:
                    tradable_templates.append(tradable_template)
                    tradable_dates_by_symbol[symbol].append(current_date)
            if not is_entry_signal(signal_bar, params):
                continue
            market_context = context.market_context_by_date.get(signal_bar.date)
            if not is_market_context_allowed(market_context, params):
                continue
            signal_template = _entry_template(
                symbol=symbol,
                bar=bar,
                signal_bar=signal_bar,
                market_context=market_context,
                params=params,
                apply_entry_filters=True,
            )
            if signal_template is not None:
                signal_templates.append(signal_template)
        signal_by_date[current_date] = sorted(
            signal_templates,
            key=lambda item: (-item.score, item.symbol),
        )
        tradable_by_date[current_date] = tradable_templates
    return CandidatePools(
        signal_by_date=signal_by_date,
        tradable_by_date=tradable_by_date,
        tradable_dates_by_symbol=dict(tradable_dates_by_symbol),
    )


def _entry_template(
    *,
    symbol: str,
    bar: PreparedBar,
    signal_bar: PreparedBar,
    market_context: MarketContext | None,
    params: SwingParams,
    apply_entry_filters: bool,
) -> EntryTemplate | None:
    if signal_bar.atr is None or signal_bar.atr <= 0:
        return None
    entry_gap_pct = (bar.open / signal_bar.close) - 1.0
    if apply_entry_filters:
        if params.min_entry_gap_pct is not None and entry_gap_pct < params.min_entry_gap_pct:
            return None
        if params.max_entry_gap_pct is not None and entry_gap_pct >= params.max_entry_gap_pct:
            return None
    stop_distance = signal_bar.atr * params.stop_atr_multiple
    stop_price = bar.open - stop_distance
    if stop_price <= 0:
        return None
    assert signal_bar.sma_short is not None
    assert signal_bar.return_20d is not None
    assert signal_bar.avg_turnover is not None
    return EntryTemplate(
        symbol=symbol,
        signal_date=signal_bar.date,
        signal_close=signal_bar.close,
        signal_sma_short=signal_bar.sma_short,
        signal_atr_pct=signal_bar.atr / signal_bar.close,
        signal_return_20d=signal_bar.return_20d,
        signal_avg_turnover=signal_bar.avg_turnover,
        entry_gap_pct=entry_gap_pct,
        entry_date=bar.date,
        entry_price=bar.open,
        stop_price=stop_price,
        target_price=bar.open + stop_distance * params.target_r_multiple,
        score=(
            _entry_score(signal_bar, params)
            if apply_entry_filters
            else _random_baseline_score(signal_bar)
        ),
        market_close_above_sma20_ratio=(
            None if market_context is None else market_context.close_above_sma20_ratio
        ),
        market_trend_breadth_ratio=(
            None if market_context is None else market_context.trend_breadth_ratio
        ),
        market_positive_return_5d_ratio=(
            None if market_context is None else market_context.positive_return_5d_ratio
        ),
        market_avg_return_5d=None if market_context is None else market_context.avg_return_5d,
        market_positive_return_20d_ratio=(
            None if market_context is None else market_context.positive_return_20d_ratio
        ),
        market_avg_return_20d=None if market_context is None else market_context.avg_return_20d,
        market_positive_return_60d_ratio=(
            None if market_context is None else market_context.positive_return_60d_ratio
        ),
        market_avg_return_60d=None if market_context is None else market_context.avg_return_60d,
        market_regime=None if market_context is None else market_context.regime,
    )


def _materialize_entry(
    template: EntryTemplate,
    *,
    equity: float,
    params: SwingParams,
    ranked_position: int,
    candidate_count: int,
) -> EntryCandidate | None:
    quantity = _position_size(
        entry_price=template.entry_price,
        stop_price=template.stop_price,
        equity=equity,
        params=params,
    )
    if quantity <= 0:
        return None
    return EntryCandidate(
        symbol=template.symbol,
        signal_date=template.signal_date,
        signal_close=template.signal_close,
        signal_sma_short=template.signal_sma_short,
        signal_atr_pct=template.signal_atr_pct,
        signal_return_20d=template.signal_return_20d,
        signal_avg_turnover=template.signal_avg_turnover,
        entry_gap_pct=template.entry_gap_pct,
        entry_date=template.entry_date,
        entry_price=template.entry_price,
        stop_price=template.stop_price,
        target_price=template.target_price,
        quantity=quantity,
        score=template.score,
        ranked_position=ranked_position,
        candidate_count=candidate_count,
        market_close_above_sma20_ratio=template.market_close_above_sma20_ratio,
        market_trend_breadth_ratio=template.market_trend_breadth_ratio,
        market_positive_return_5d_ratio=template.market_positive_return_5d_ratio,
        market_avg_return_5d=template.market_avg_return_5d,
        market_positive_return_20d_ratio=template.market_positive_return_20d_ratio,
        market_avg_return_20d=template.market_avg_return_20d,
        market_positive_return_60d_ratio=template.market_positive_return_60d_ratio,
        market_avg_return_60d=template.market_avg_return_60d,
        market_regime=template.market_regime,
    )


def simulate(
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    *,
    selection: SelectionMode = "ranked",
    random_seed: int = 1,
    baseline_kind: BaselineKind = "strategy",
    execution_stress: ExecutionStress | None = None,
    simulation_context: SimulationContext | None = None,
    candidate_pools: CandidatePools | None = None,
) -> list[Trade]:
    rng = random.Random(random_seed)
    stress = ExecutionStress() if execution_stress is None else execution_stress
    context = (
        build_simulation_context(prepared) if simulation_context is None else simulation_context
    )
    by_date = context.by_date
    pools = (
        build_candidate_pools(context=context, params=params)
        if candidate_pools is None
        else candidate_pools
    )
    symbol_random_date_schedule = (
        _build_symbol_random_date_schedule(
            candidate_pools=pools,
            params=params,
            random_seed=random_seed,
        )
        if baseline_kind == "symbol_matched_random_date"
        else {}
    )

    equity = params.starting_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    for current_date in sorted(by_date):
        today = by_date[current_date]
        if stress.exit_before_entry_at_open:
            equity = _process_exits(
                today=today,
                positions=positions,
                trades=trades,
                equity=equity,
                params=params,
                execution_stress=stress,
            )

        signal_entries = _signal_candidates_from_pool(
            pools.signal_by_date.get(current_date, []),
            equity=equity,
            positions=positions,
            params=params,
        )
        if baseline_kind in ("strategy", "signal_set_random"):
            entries = signal_entries
        elif baseline_kind == "universe_date_matched_random":
            entries = _universe_date_matched_random_candidates_from_pool(
                pools.tradable_by_date.get(current_date, []),
                equity=equity,
                positions=positions,
                params=params,
                desired_count=len(signal_entries),
                rng=rng,
            )
        elif baseline_kind == "symbol_matched_random_date":
            entries = _scheduled_symbol_random_date_candidates_from_pool(
                pools.tradable_by_date.get(current_date, []),
                equity=equity,
                positions=positions,
                params=params,
                scheduled_symbols=symbol_random_date_schedule.get(current_date, []),
            )
        else:
            raise ValueError(f"unknown baseline_kind: {baseline_kind}")
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
                market_regime=candidate.market_regime,
            )
            opened_today += 1

        if not stress.exit_before_entry_at_open:
            equity = _process_exits(
                today=today,
                positions=positions,
                trades=trades,
                equity=equity,
                params=params,
                execution_stress=stress,
            )

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
                score=_entry_score(signal_bar, params),
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
                market_regime=None if market_context is None else market_context.regime,
            )
        )
    ranked = sorted(candidates, key=lambda item: (-item.score, item.symbol))
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(ranked))
        for idx, candidate in enumerate(ranked, start=1)
    ]


def _signal_candidates_from_pool(
    templates: list[EntryTemplate],
    *,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
) -> list[EntryCandidate]:
    available = [template for template in templates if template.symbol not in positions]
    candidates: list[EntryCandidate] = []
    for idx, template in enumerate(available, start=1):
        candidate = _materialize_entry(
            template,
            equity=equity,
            params=params,
            ranked_position=idx,
            candidate_count=len(available),
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _universe_date_matched_random_candidates_from_pool(
    templates: list[EntryTemplate],
    *,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
    desired_count: int,
    rng: random.Random,
) -> list[EntryCandidate]:
    if desired_count <= 0:
        return []
    candidates = [
        candidate
        for template in templates
        if template.symbol not in positions
        if (
            candidate := _materialize_entry(
                template,
                equity=equity,
                params=params,
                ranked_position=0,
                candidate_count=0,
            )
        )
        is not None
    ]
    rng.shuffle(candidates)
    selected = candidates[:desired_count]
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(selected))
        for idx, candidate in enumerate(selected, start=1)
    ]


def _scheduled_symbol_random_date_candidates_from_pool(
    templates: list[EntryTemplate],
    *,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
    scheduled_symbols: list[str],
) -> list[EntryCandidate]:
    if not scheduled_symbols:
        return []
    scheduled_counts = Counter(scheduled_symbols)
    candidates: list[EntryCandidate] = []
    for template in templates:
        remaining = scheduled_counts.get(template.symbol, 0)
        if remaining <= 0 or template.symbol in positions:
            continue
        for _ in range(remaining):
            candidate = _materialize_entry(
                template,
                equity=equity,
                params=params,
                ranked_position=0,
                candidate_count=0,
            )
            if candidate is not None:
                candidates.append(candidate)
        scheduled_counts[template.symbol] = 0
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(candidates))
        for idx, candidate in enumerate(sorted(candidates, key=lambda item: item.symbol), start=1)
    ]


def _process_exits(
    *,
    today: dict[str, PreparedBar],
    positions: dict[str, Position],
    trades: list[Trade],
    equity: float,
    params: SwingParams,
    execution_stress: ExecutionStress,
) -> float:
    for symbol, position in list(positions.items()):
        bar = today.get(symbol)
        if bar is None:
            continue
        trade = _exit_on_bar(position, bar, params, execution_stress=execution_stress)
        if trade is None:
            continue
        trades.append(trade)
        equity += trade.net_pnl
        del positions[symbol]
    return equity


def _universe_date_matched_random_candidates(
    *,
    today: dict[str, PreparedBar],
    previous_by_symbol: dict[tuple[str, date], PreparedBar],
    market_context_by_date: dict[date, MarketContext],
    current_date: date,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
    desired_count: int,
    rng: random.Random,
) -> list[EntryCandidate]:
    if desired_count <= 0:
        return []
    candidates = [
        candidate
        for symbol, bar in sorted(today.items())
        if (
            candidate := _random_baseline_candidate(
                symbol=symbol,
                bar=bar,
                previous_by_symbol=previous_by_symbol,
                market_context_by_date=market_context_by_date,
                current_date=current_date,
                equity=equity,
                positions=positions,
                params=params,
            )
        )
        is not None
    ]
    rng.shuffle(candidates)
    selected = candidates[:desired_count]
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(selected))
        for idx, candidate in enumerate(selected, start=1)
    ]


def _scheduled_symbol_random_date_candidates(
    *,
    today: dict[str, PreparedBar],
    previous_by_symbol: dict[tuple[str, date], PreparedBar],
    market_context_by_date: dict[date, MarketContext],
    current_date: date,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
    scheduled_symbols: list[str],
) -> list[EntryCandidate]:
    candidates: list[EntryCandidate] = []
    for symbol in scheduled_symbols:
        bar = today.get(symbol)
        if bar is None:
            continue
        candidate = _random_baseline_candidate(
            symbol=symbol,
            bar=bar,
            previous_by_symbol=previous_by_symbol,
            market_context_by_date=market_context_by_date,
            current_date=current_date,
            equity=equity,
            positions=positions,
            params=params,
        )
        if candidate is not None:
            candidates.append(candidate)
    return [
        replace(candidate, ranked_position=idx, candidate_count=len(candidates))
        for idx, candidate in enumerate(sorted(candidates, key=lambda item: item.symbol), start=1)
    ]


def _random_baseline_candidate(
    *,
    symbol: str,
    bar: PreparedBar,
    previous_by_symbol: dict[tuple[str, date], PreparedBar],
    market_context_by_date: dict[date, MarketContext],
    current_date: date,
    equity: float,
    positions: dict[str, Position],
    params: SwingParams,
) -> EntryCandidate | None:
    if symbol in positions:
        return None
    signal_bar = previous_by_symbol.get((symbol, current_date))
    if signal_bar is None:
        return None
    if not _is_random_baseline_tradable(signal_bar, params):
        return None
    assert signal_bar.atr is not None
    assert signal_bar.sma_short is not None
    assert signal_bar.return_20d is not None
    assert signal_bar.avg_turnover is not None
    stop_distance = signal_bar.atr * params.stop_atr_multiple
    stop_price = bar.open - stop_distance
    if stop_price <= 0:
        return None
    quantity = _position_size(
        entry_price=bar.open,
        stop_price=stop_price,
        equity=equity,
        params=params,
    )
    if quantity <= 0:
        return None
    market_context = market_context_by_date.get(signal_bar.date)
    return EntryCandidate(
        symbol=symbol,
        signal_date=signal_bar.date,
        signal_close=signal_bar.close,
        signal_sma_short=signal_bar.sma_short,
        signal_atr_pct=signal_bar.atr / signal_bar.close,
        signal_return_20d=signal_bar.return_20d,
        signal_avg_turnover=signal_bar.avg_turnover,
        entry_gap_pct=(bar.open / signal_bar.close) - 1.0,
        entry_date=current_date,
        entry_price=bar.open,
        stop_price=stop_price,
        target_price=bar.open + stop_distance * params.target_r_multiple,
        quantity=quantity,
        score=_random_baseline_score(signal_bar),
        market_close_above_sma20_ratio=(
            None if market_context is None else market_context.close_above_sma20_ratio
        ),
        market_trend_breadth_ratio=(
            None if market_context is None else market_context.trend_breadth_ratio
        ),
        market_positive_return_5d_ratio=(
            None if market_context is None else market_context.positive_return_5d_ratio
        ),
        market_avg_return_5d=None if market_context is None else market_context.avg_return_5d,
        market_positive_return_20d_ratio=(
            None if market_context is None else market_context.positive_return_20d_ratio
        ),
        market_avg_return_20d=None if market_context is None else market_context.avg_return_20d,
        market_positive_return_60d_ratio=(
            None if market_context is None else market_context.positive_return_60d_ratio
        ),
        market_avg_return_60d=None if market_context is None else market_context.avg_return_60d,
        market_regime=None if market_context is None else market_context.regime,
    )


def _is_random_baseline_tradable(bar: PreparedBar | None, params: SwingParams) -> bool:
    if bar is None:
        return False
    return (
        params.min_price <= bar.close <= params.max_price
        and bar.atr is not None
        and bar.atr > 0
        and bar.sma_short is not None
        and bar.return_20d is not None
        and bar.avg_turnover is not None
        and bar.avg_turnover >= params.min_avg_turnover
        and (params.max_avg_turnover is None or bar.avg_turnover < params.max_avg_turnover)
    )


def _build_symbol_random_date_schedule(
    *,
    candidate_pools: CandidatePools,
    params: SwingParams,
    random_seed: int,
) -> dict[date, list[str]]:
    rng = random.Random(random_seed)
    signal_counts_by_symbol: dict[str, int] = defaultdict(int)
    for templates in candidate_pools.signal_by_date.values():
        for template in templates:
            if (
                _materialize_entry(
                    template,
                    equity=params.starting_capital,
                    params=params,
                    ranked_position=0,
                    candidate_count=0,
                )
                is not None
            ):
                signal_counts_by_symbol[template.symbol] += 1

    schedule: dict[date, list[str]] = defaultdict(list)
    for symbol, count in signal_counts_by_symbol.items():
        valid_dates = candidate_pools.tradable_dates_by_symbol.get(symbol, [])
        for _ in range(count):
            if valid_dates:
                schedule[rng.choice(valid_dates)].append(symbol)
    return schedule


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
    if selection == "stable_hash":
        return sorted(candidates, key=lambda item: (_stable_hash_key(item), item.symbol))
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


def _stable_hash_key(candidate: EntryCandidate) -> str:
    raw = f"fixed10_hash_v0:{candidate.signal_date.isoformat()}:{candidate.symbol}"
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def is_entry_signal(bar: PreparedBar, params: SwingParams) -> bool:
    if params.entry_mode == "breakout_continuation":
        return is_breakout_continuation_entry_signal(bar, params)
    return is_trend_pullback_entry_signal(bar, params)


def is_trend_pullback_entry_signal(bar: PreparedBar, params: SwingParams) -> bool:
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


def is_breakout_continuation_entry_signal(bar: PreparedBar, params: SwingParams) -> bool:
    required = (
        bar.sma_short,
        bar.sma_long,
        bar.sma_long_past,
        bar.atr,
        bar.avg_turnover,
        bar.return_20d,
        bar.return_60d,
        bar.prior_high_breakout,
        bar.prior_range_20d_pct,
    )
    if any(value is None for value in required):
        return False
    assert bar.sma_short is not None
    assert bar.sma_long is not None
    assert bar.sma_long_past is not None
    assert bar.atr is not None
    assert bar.avg_turnover is not None
    assert bar.return_20d is not None
    assert bar.return_60d is not None
    assert bar.prior_high_breakout is not None
    assert bar.prior_range_20d_pct is not None

    atr_pct = bar.atr / bar.close
    distance_above_sma = (bar.close / bar.sma_short) - 1.0
    turnover_multiple = bar.turnover / bar.avg_turnover if bar.avg_turnover > 0 else 0.0
    return (
        params.min_price <= bar.close <= params.max_price
        and bar.avg_turnover >= params.min_avg_turnover
        and (params.max_avg_turnover is None or bar.avg_turnover < params.max_avg_turnover)
        and bar.sma_short > bar.sma_long
        and bar.close > bar.sma_short
        and bar.close > bar.sma_long
        and bar.sma_long > bar.sma_long_past
        and bar.return_20d >= params.min_return_20d
        and (params.max_return_20d is None or bar.return_20d < params.max_return_20d)
        and (params.min_return_60d is None or bar.return_60d >= params.min_return_60d)
        and bar.close >= bar.prior_high_breakout * (1.0 + params.breakout_buffer_pct)
        and distance_above_sma <= params.max_distance_above_sma_short
        and params.min_atr_pct <= atr_pct <= params.max_atr_pct
        and (
            params.min_turnover_multiple is None
            or turnover_multiple >= params.min_turnover_multiple
        )
        and (
            params.max_prior_range_20d_pct is None
            or bar.prior_range_20d_pct <= params.max_prior_range_20d_pct
        )
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


def _exit_on_bar(
    position: Position,
    bar: PreparedBar,
    params: SwingParams,
    *,
    execution_stress: ExecutionStress | None = None,
) -> Trade | None:
    stress = ExecutionStress() if execution_stress is None else execution_stress
    if params.exit_mode == "fixed_hold":
        if bar.date >= position.max_exit_date:
            return _close_trade(position, bar.date, bar.close, "fixed_hold", params)
        return None
    if bar.open <= position.stop_price:
        if stress.limit_down_unfillable and bar.open <= position.entry_price * (
            1.0 - stress.limit_down_threshold_pct
        ):
            return _close_trade(
                position,
                bar.date,
                min(bar.open, bar.close),
                "limit_down_unfillable_gap_stop",
                params,
            )
        gap_stop_price = bar.open * (1.0 - stress.gap_stop_additional_slippage_rate)
        return _close_trade(position, bar.date, gap_stop_price, "gap_stop", params)
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
        market_regime=position.market_regime,
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
    random_baseline_kinds: list[BaselineKind] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    effective_baseline_kinds = (
        list(RANDOM_BASELINE_KINDS) if random_baseline_kinds is None else random_baseline_kinds
    )
    specs: list[tuple[str, SelectionMode, int | None, BaselineKind]] = [
        ("ranked", "ranked", None, "strategy"),
        ("score_ascending", "score_ascending", None, "strategy"),
        ("score_middle", "score_middle", None, "strategy"),
        ("rank_2_3_first", "rank_2_3_first", None, "strategy"),
    ]
    specs.extend(
        (f"{baseline_kind}:seed_{seed}", "random", seed, baseline_kind)
        for baseline_kind in effective_baseline_kinds
        for seed in random_seeds
    )

    for label, selection, seed, baseline_kind in specs:
        trades = simulate(
            prepared,
            params,
            selection=selection,
            random_seed=1 if seed is None else seed,
            baseline_kind=baseline_kind,
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
                "baseline_kind": baseline_kind,
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
    random_baseline_kinds: list[BaselineKind] | None = None,
) -> dict[str, Any]:
    effective_baseline_kinds = (
        list(RANDOM_BASELINE_KINDS) if random_baseline_kinds is None else random_baseline_kinds
    )
    split_comparisons = [
        build_selection_comparison(
            prepared=prepared,
            params=params,
            candidate=candidate,
            input_path=input_path,
            validation_start=validation_start,
            fold_count=fold_count,
            random_seeds=random_seeds,
            random_baseline_kinds=effective_baseline_kinds,
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
                "baseline_kind": rows[0]["baseline_kind"],
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
    random_baseline_kinds: list[BaselineKind],
    candidates: tuple[CandidateName, ...] = RESEARCH_CANDIDATES,
    fold_count: int,
    execution_stress: ExecutionStress | None = None,
) -> dict[str, Any]:
    if min_train_days <= 0:
        raise ValueError("min_train_days must be positive")
    if oos_block_days <= 0:
        raise ValueError("oos_block_days must be positive")

    dates = sorted({row.date for row in rows})
    if len(dates) <= min_train_days:
        raise ValueError("not enough dates for walk-forward research")
    stress = ExecutionStress() if execution_stress is None else execution_stress

    research_artifacts = _build_research_artifacts(
        rows=rows,
        capital=capital,
        min_avg_turnover=min_avg_turnover,
        random_seeds=random_seeds,
        random_baseline_kinds=random_baseline_kinds,
        candidates=candidates,
        fold_count=fold_count,
        execution_stress=stress,
    )
    deterministic_runs = research_artifacts["deterministic_runs"]
    random_runs = research_artifacts["random_runs"]
    research_alpha_diagnostics = research_artifacts["research_alpha_diagnostics"]

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
        selected_train_trades = _train_trades_before(selected["trades"], oos_start)
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
                "baseline_kind": run["baseline_kind"],
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
                "selected_train_regime": build_diagnostics(selected_train_trades)["market_regime"],
                "selected_oos": _metrics_dict(selected_oos_metrics),
                "selected_oos_gate": selected_oos_gate,
                "selected_oos_regime": build_diagnostics(selected_oos_trades_for_block)[
                    "market_regime"
                ],
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
    random_comparison = build_random_comparison_diagnostics(
        selected_oos_metrics=selected_oos_metrics,
        random_oos_summaries=random_oos_summaries,
        params=SwingParams(starting_capital=capital, min_avg_turnover=min_avg_turnover),
    )
    research_gate = build_walk_forward_research_gate(
        selected_oos_gate=selected_oos_gate,
        block_count=len(blocks),
        selected_train_pass_count=selected_train_pass_count,
        selected_oos_pass_count=selected_oos_pass_count,
        selected_oos_metrics=selected_oos_metrics,
        random_oos_summaries=random_oos_summaries,
    )
    selected_oos_block_stability = build_oos_block_stability(blocks)
    low_frequency_research_gate = build_low_frequency_research_gate(
        selected_oos_gate=selected_oos_gate,
        selected_oos_block_stability=selected_oos_block_stability,
        random_comparison=random_comparison,
        selected_train_pass_count=selected_train_pass_count,
        block_count=len(blocks),
        params=SwingParams(starting_capital=capital, min_avg_turnover=min_avg_turnover),
    )
    return {
        "mode": "walk_forward_research",
        "input": str(input_path),
        "min_train_days": min_train_days,
        "oos_block_days": oos_block_days,
        "candidates": list(candidates),
        "deterministic_selections": list(DETERMINISTIC_SELECTIONS),
        "random_baseline_seeds": random_seeds,
        "random_baseline_kinds": random_baseline_kinds,
        "execution_stress": asdict(stress),
        "block_count": len(blocks),
        "selected_train_pass_count": selected_train_pass_count,
        "selected_oos_pass_count": selected_oos_pass_count,
        "selected_oos": _metrics_dict(selected_oos_metrics),
        "selected_oos_gate": selected_oos_gate,
        "selected_oos_regime": build_diagnostics(selected_oos_trades)["market_regime"],
        "selected_oos_block_stability": selected_oos_block_stability,
        "random_comparison": random_comparison,
        "low_frequency_research_gate": low_frequency_research_gate,
        "research_gate": research_gate,
        "selected_oos_walk_forward": selected_oos_walk_forward,
        "random_oos_summaries": random_oos_summaries,
        "research_alpha_diagnostics": research_alpha_diagnostics,
        "blocks": blocks,
    }


def _build_research_artifacts(
    *,
    rows: list[OhlcvRow],
    capital: float,
    min_avg_turnover: float,
    random_seeds: list[int],
    random_baseline_kinds: list[BaselineKind],
    candidates: tuple[CandidateName, ...],
    fold_count: int,
    execution_stress: ExecutionStress,
) -> dict[str, list[dict[str, Any]]]:
    deterministic_runs: list[dict[str, Any]] = []
    random_runs: list[dict[str, Any]] = []
    research_alpha_diagnostics: list[dict[str, Any]] = []
    for candidate in candidates:
        params = params_for_candidate(candidate, capital, min_avg_turnover)
        prepared = prepare_bars(rows, params)
        simulation_context = build_simulation_context(prepared)
        candidate_pools = build_candidate_pools(context=simulation_context, params=params)
        deterministic_selections = deterministic_selections_for_candidate(candidate)
        deterministic_runs.extend(
            _build_research_runs_for_candidate(
                candidate=candidate,
                prepared=prepared,
                params=params,
                simulation_context=simulation_context,
                candidate_pools=candidate_pools,
                selections=deterministic_selections,
                random_seeds=[None],
                baseline_kinds=["strategy"],
                execution_stress=execution_stress,
            )
        )
        random_runs.extend(
            _build_research_runs_for_candidate(
                candidate=candidate,
                prepared=prepared,
                params=params,
                simulation_context=simulation_context,
                candidate_pools=candidate_pools,
                selections=("random",),
                random_seeds=[cast(int | None, seed) for seed in random_seeds],
                baseline_kinds=random_baseline_kinds,
                execution_stress=execution_stress,
            )
        )
        research_alpha_diagnostics.append(
            _build_candidate_alpha_diagnostic(
                candidate=candidate,
                prepared=prepared,
                params=params,
                simulation_context=simulation_context,
                candidate_pools=candidate_pools,
                selections=deterministic_selections,
                fold_count=fold_count,
                execution_stress=execution_stress,
            )
        )
    return {
        "deterministic_runs": deterministic_runs,
        "random_runs": random_runs,
        "research_alpha_diagnostics": research_alpha_diagnostics,
    }


def _build_research_runs_for_candidate(
    *,
    candidate: CandidateName,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    simulation_context: SimulationContext,
    candidate_pools: CandidatePools,
    selections: tuple[SelectionMode, ...],
    random_seeds: list[int | None],
    baseline_kinds: list[BaselineKind],
    execution_stress: ExecutionStress,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for selection in selections:
        for seed in random_seeds:
            if selection != "random" and seed is not None:
                continue
            for baseline_kind in baseline_kinds:
                if selection != "random" and baseline_kind != "strategy":
                    continue
                random_seed = 1 if seed is None else seed
                label = (
                    f"{candidate}:{selection}"
                    if selection != "random"
                    else f"{candidate}:{baseline_kind}:seed_{random_seed}"
                )
                print(f"research_run: {label}", flush=True)
                runs.append(
                    {
                        "label": label,
                        "candidate": candidate,
                        "selection": selection,
                        "baseline_kind": baseline_kind,
                        "random_seed": None if selection != "random" else random_seed,
                        "params": params,
                        "trades": simulate(
                            prepared,
                            params,
                            selection=selection,
                            random_seed=random_seed,
                            baseline_kind=baseline_kind,
                            execution_stress=execution_stress,
                            simulation_context=simulation_context,
                            candidate_pools=candidate_pools,
                        ),
                    }
                )
    return runs


def _build_research_runs(
    *,
    rows: list[OhlcvRow],
    capital: float,
    min_avg_turnover: float,
    selections: tuple[SelectionMode, ...],
    random_seeds: list[int] | None = None,
    baseline_kinds: list[BaselineKind] | None = None,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    seeds = [None] if random_seeds is None else random_seeds
    kinds: list[BaselineKind] = ["strategy"] if baseline_kinds is None else baseline_kinds
    for candidate in RESEARCH_CANDIDATES:
        params = params_for_candidate(candidate, capital, min_avg_turnover)
        prepared = prepare_bars(rows, params)
        simulation_context = build_simulation_context(prepared)
        candidate_pools = build_candidate_pools(context=simulation_context, params=params)
        for selection in selections:
            for seed in seeds:
                if selection != "random" and seed is not None:
                    continue
                for baseline_kind in kinds:
                    if selection != "random" and baseline_kind != "strategy":
                        continue
                    random_seed = 1 if seed is None else seed
                    label = (
                        f"{candidate}:{selection}"
                        if selection != "random"
                        else f"{candidate}:{baseline_kind}:seed_{random_seed}"
                    )
                    print(f"research_run: {label}", flush=True)
                    runs.append(
                        {
                            "label": label,
                            "candidate": candidate,
                            "selection": selection,
                            "baseline_kind": baseline_kind,
                            "random_seed": None if selection != "random" else random_seed,
                            "params": params,
                            "trades": simulate(
                                prepared,
                                params,
                                selection=selection,
                                random_seed=random_seed,
                                baseline_kind=baseline_kind,
                                simulation_context=simulation_context,
                                candidate_pools=candidate_pools,
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
                "baseline_kind": run["baseline_kind"],
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


def build_oos_block_stability(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    if not blocks:
        return {
            "block_count": 0,
            "positive_block_count": 0,
            "positive_block_ratio": None,
            "min_trade_count": None,
            "median_trade_count": None,
            "min_net_pnl": None,
            "worst_block": None,
        }
    metrics = [block["selected_oos"] for block in blocks]
    trade_counts = sorted(int(row["trade_count"]) for row in metrics)
    positive_count = sum(1 for row in metrics if float(row["total_net_pnl"]) > 0)
    worst_block = min(blocks, key=lambda block: float(block["selected_oos"]["total_net_pnl"]))
    return {
        "block_count": len(blocks),
        "positive_block_count": positive_count,
        "positive_block_ratio": _round(positive_count / len(blocks)),
        "min_trade_count": min(trade_counts),
        "median_trade_count": _median([float(value) for value in trade_counts]),
        "min_net_pnl": _round(float(worst_block["selected_oos"]["total_net_pnl"])),
        "worst_block": {
            "block": worst_block["block"],
            "oos_start": worst_block["oos_start"],
            "oos_end": worst_block["oos_end"],
            "trade_count": worst_block["selected_oos"]["trade_count"],
            "total_net_pnl": worst_block["selected_oos"]["total_net_pnl"],
            "profit_factor": worst_block["selected_oos"]["profit_factor"],
            "max_drawdown": worst_block["selected_oos"]["max_drawdown"],
        },
    }


def build_random_comparison_diagnostics(
    *,
    selected_oos_metrics: Metrics,
    random_oos_summaries: list[dict[str, Any]],
    params: SwingParams,
) -> dict[str, Any]:
    if not random_oos_summaries:
        return {
            "random_count": 0,
            "selected_rank_by_net": None,
            "selected_net_percentile": None,
            "best_random": None,
            "random_gate_like_pass_count": 0,
        }
    selected_net = selected_oos_metrics.total_net_pnl
    better_net_count = sum(
        1 for row in random_oos_summaries if float(row["oos"]["total_net_pnl"]) > selected_net
    )
    random_count = len(random_oos_summaries)
    net_rank = better_net_count + 1
    best_random = max(random_oos_summaries, key=lambda row: float(row["oos"]["total_net_pnl"]))
    gate_like_pass_count = sum(
        1 for row in random_oos_summaries if _summary_passes_gate_like(row["oos"], params)
    )
    return {
        "random_count": random_count,
        "selected_rank_by_net": net_rank,
        "selected_net_percentile": _round((random_count - better_net_count) / (random_count + 1)),
        "best_random": best_random,
        "random_gate_like_pass_count": gate_like_pass_count,
    }


def build_low_frequency_research_gate(
    *,
    selected_oos_gate: dict[str, Any],
    selected_oos_block_stability: dict[str, Any],
    random_comparison: dict[str, Any],
    selected_train_pass_count: int,
    block_count: int,
    params: SwingParams,
) -> dict[str, Any]:
    failures = [f"aggregate_{failure}" for failure in selected_oos_gate["failures"]]
    if block_count <= 0:
        failures.append("block_count 0 <= 0")
    min_train_pass_count = (block_count + 1) // 2
    if selected_train_pass_count < min_train_pass_count:
        failures.append(
            f"selected_train_pass_count {selected_train_pass_count} < {min_train_pass_count}"
        )

    positive_block_ratio = selected_oos_block_stability.get("positive_block_ratio")
    if positive_block_ratio is None or float(positive_block_ratio) < 2 / 3:
        failures.append(f"positive_block_ratio {positive_block_ratio} < 0.6667")

    median_trade_count = selected_oos_block_stability.get("median_trade_count")
    if median_trade_count is None or float(median_trade_count) < 15:
        failures.append(f"median_trade_count {median_trade_count} < 15")

    min_net_pnl = selected_oos_block_stability.get("min_net_pnl")
    min_block_net_pnl = -(params.starting_capital * 0.05)
    if min_net_pnl is None or float(min_net_pnl) < min_block_net_pnl:
        failures.append(f"min_block_net_pnl {min_net_pnl} < {min_block_net_pnl:.3f}")

    random_count = int(random_comparison.get("random_count") or 0)
    if random_count < 30:
        failures.append(f"random_count {random_count} < 30")

    selected_net_percentile = random_comparison.get("selected_net_percentile")
    if selected_net_percentile is None or float(selected_net_percentile) < 0.75:
        failures.append(f"selected_net_percentile {selected_net_percentile} < 0.75")

    return {"status": "FAIL" if failures else "PASS", "failures": failures}


def _summary_passes_gate_like(summary: dict[str, Any], params: SwingParams) -> bool:
    profit_factor = summary.get("profit_factor")
    positive_month_ratio = summary.get("positive_month_ratio")
    worst_month_net_pnl = summary.get("worst_month_net_pnl")
    return (
        float(summary["total_net_pnl"]) > 0
        and profit_factor is not None
        and float(profit_factor) > 1.2
        and float(summary["max_drawdown"]) < params.starting_capital * 0.10
        and int(summary["trade_count"]) >= 30
        and positive_month_ratio is not None
        and float(positive_month_ratio) >= 0.55
        and worst_month_net_pnl is not None
        and float(worst_month_net_pnl) >= -(params.starting_capital * 0.05)
    )


def build_random_baselines(
    *,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    validation_start: date,
    seeds: list[int],
    baseline_kinds: list[BaselineKind],
    fold_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    simulation_context = build_simulation_context(prepared)
    candidate_pools = build_candidate_pools(context=simulation_context, params=params)
    for baseline_kind in baseline_kinds:
        for seed in seeds:
            trades = simulate(
                prepared,
                params,
                selection="random",
                random_seed=seed,
                baseline_kind=baseline_kind,
                simulation_context=simulation_context,
                candidate_pools=candidate_pools,
            )
            train_trades = [trade for trade in trades if trade.exit_date < validation_start]
            validation_trades = [trade for trade in trades if trade.exit_date >= validation_start]
            rows.append(
                {
                    "seed": seed,
                    "baseline_kind": baseline_kind,
                    "train": _metrics_dict(calculate_metrics(train_trades)),
                    "validation": _metrics_dict(calculate_metrics(validation_trades)),
                    "validation_walk_forward": build_walk_forward_summary(
                        validation_trades,
                        fold_count=fold_count,
                    ),
                }
            )
    return rows


def build_alpha_diagnostics(
    *,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    selection: SelectionMode,
    random_seed: int,
    trades: list[Trade],
    fold_count: int,
) -> dict[str, Any]:
    simulation_context = build_simulation_context(prepared)
    candidate_pools = build_candidate_pools(context=simulation_context, params=params)
    candidates_by_date = _entry_candidates_by_date_from_pool(candidate_pools, params)
    candidates = [candidate for items in candidates_by_date.values() for candidate in items]
    forward_2d = _candidate_forward_returns(candidates, prepared, 2)
    forward_5d = _candidate_forward_returns(candidates, prepared, 5)
    forward_10d = _candidate_forward_returns(candidates, prepared, 10)
    excess_2d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 2)
    excess_5d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 5)
    excess_10d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 10)
    rng = random.Random(random_seed)
    selected_symbols_by_date: dict[date, set[str]] = {}
    daily_pick_limit = params.max_new_positions_per_day or params.max_positions
    for entry_date, daily_candidates in candidates_by_date.items():
        ordered = order_candidates(daily_candidates, selection=selection, rng=rng)
        selected_symbols_by_date[entry_date] = {
            candidate.symbol for candidate in ordered[:daily_pick_limit]
        }

    return {
        "entry_alpha": {
            "forward_2d": _forward_return_summary(forward_2d),
            "forward_5d": _forward_return_summary(forward_5d),
            "forward_10d": _forward_return_summary(forward_10d),
            "excess_vs_tradable_universe_2d": _forward_return_summary(excess_2d),
            "excess_vs_tradable_universe_5d": _forward_return_summary(excess_5d),
            "excess_vs_tradable_universe_10d": _forward_return_summary(excess_10d),
        },
        "selector_alpha": {
            "selected_forward_5d": _forward_return_summary(
                [
                    item
                    for item in forward_5d
                    if item["symbol"] in selected_symbols_by_date.get(item["entry_date"], set())
                ]
            ),
            "unselected_forward_5d": _forward_return_summary(
                [
                    item
                    for item in forward_5d
                    if item["symbol"] not in selected_symbols_by_date.get(item["entry_date"], set())
                ]
            ),
            "selected_excess_forward_5d": _forward_return_summary(
                [
                    item
                    for item in excess_5d
                    if item["symbol"] in selected_symbols_by_date.get(item["entry_date"], set())
                ]
            ),
            "unselected_excess_forward_5d": _forward_return_summary(
                [
                    item
                    for item in excess_5d
                    if item["symbol"] not in selected_symbols_by_date.get(item["entry_date"], set())
                ]
            ),
        },
        "exit_alpha": {
            "fixed_2d": _metrics_dict(
                calculate_metrics(rebuild_trades_with_fixed_exit(prepared, trades, params, 2))
            ),
            "fixed_5d": _metrics_dict(
                calculate_metrics(rebuild_trades_with_fixed_exit(prepared, trades, params, 5))
            ),
            "fixed_10d": _metrics_dict(
                calculate_metrics(rebuild_trades_with_fixed_exit(prepared, trades, params, 10))
            ),
            "configured_exit_mode": params.exit_mode,
            "configured_exit": _metrics_dict(calculate_metrics(trades)),
            "target_stop_max_hold": _metrics_dict(calculate_metrics(trades)),
        },
        "score_folds": build_score_fold_diagnostics(candidates, prepared, fold_count=fold_count),
    }


def build_research_alpha_diagnostics(
    *,
    rows: list[OhlcvRow],
    capital: float,
    min_avg_turnover: float,
    selections: tuple[SelectionMode, ...],
    fold_count: int,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for candidate in RESEARCH_CANDIDATES:
        params = params_for_candidate(candidate, capital, min_avg_turnover)
        prepared = prepare_bars(rows, params)
        simulation_context = build_simulation_context(prepared)
        candidate_pools = build_candidate_pools(context=simulation_context, params=params)
        diagnostics.append(
            _build_candidate_alpha_diagnostic(
                candidate=candidate,
                prepared=prepared,
                params=params,
                simulation_context=simulation_context,
                candidate_pools=candidate_pools,
                selections=selections,
                fold_count=fold_count,
            )
        )
    return diagnostics


def _build_candidate_alpha_diagnostic(
    *,
    candidate: CandidateName,
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
    simulation_context: SimulationContext,
    candidate_pools: CandidatePools,
    selections: tuple[SelectionMode, ...],
    fold_count: int,
    execution_stress: ExecutionStress | None = None,
) -> dict[str, Any]:
    candidates_by_date = _entry_candidates_by_date_from_pool(candidate_pools, params)
    candidates = [item for daily in candidates_by_date.values() for item in daily]
    forward_2d = _candidate_forward_returns(candidates, prepared, 2)
    forward_5d = _candidate_forward_returns(candidates, prepared, 5)
    forward_10d = _candidate_forward_returns(candidates, prepared, 10)
    excess_2d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 2)
    excess_5d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 5)
    excess_10d = _candidate_excess_forward_returns(candidates, candidate_pools, prepared, 10)
    selector_rows: list[dict[str, Any]] = []
    for selection in selections:
        trades = simulate(
            prepared,
            params,
            selection=selection,
            execution_stress=execution_stress,
            simulation_context=simulation_context,
            candidate_pools=candidate_pools,
        )
        selector_rows.append(
            {
                "selection": selection,
                "selector_alpha": _selector_alpha_summary(
                    candidates_by_date,
                    forward_5d,
                    excess_5d,
                    selection=selection,
                    params=params,
                ),
                "exit_alpha": {
                    "fixed_2d": _metrics_dict(
                        calculate_metrics(
                            rebuild_trades_with_fixed_exit(prepared, trades, params, 2)
                        )
                    ),
                    "fixed_5d": _metrics_dict(
                        calculate_metrics(
                            rebuild_trades_with_fixed_exit(prepared, trades, params, 5)
                        )
                    ),
                    "fixed_10d": _metrics_dict(
                        calculate_metrics(
                            rebuild_trades_with_fixed_exit(prepared, trades, params, 10)
                        )
                    ),
                    "configured_exit_mode": params.exit_mode,
                    "configured_exit": _metrics_dict(calculate_metrics(trades)),
                    "target_stop_max_hold": _metrics_dict(calculate_metrics(trades)),
                },
            }
        )
    return {
        "candidate": candidate,
        "entry_alpha": {
            "forward_2d": _forward_return_summary(forward_2d),
            "forward_5d": _forward_return_summary(forward_5d),
            "forward_10d": _forward_return_summary(forward_10d),
            "excess_vs_tradable_universe_2d": _forward_return_summary(excess_2d),
            "excess_vs_tradable_universe_5d": _forward_return_summary(excess_5d),
            "excess_vs_tradable_universe_10d": _forward_return_summary(excess_10d),
        },
        "score_folds": build_score_fold_diagnostics(
            candidates,
            prepared,
            fold_count=fold_count,
        ),
        "selectors": selector_rows,
    }


def _entry_candidates_by_date_from_pool(
    candidate_pools: CandidatePools,
    params: SwingParams,
) -> dict[date, list[EntryCandidate]]:
    return {
        current_date: _signal_candidates_from_pool(
            templates,
            equity=params.starting_capital,
            positions={},
            params=params,
        )
        for current_date, templates in candidate_pools.signal_by_date.items()
    }


def _selector_alpha_summary(
    candidates_by_date: dict[date, list[EntryCandidate]],
    forward_5d: list[dict[str, Any]],
    excess_5d: list[dict[str, Any]],
    *,
    selection: SelectionMode,
    params: SwingParams,
) -> dict[str, Any]:
    rng = random.Random(1)
    daily_pick_limit = params.max_new_positions_per_day or params.max_positions
    selected_symbols_by_date: dict[date, set[str]] = {}
    for entry_date, daily_candidates in candidates_by_date.items():
        ordered = order_candidates(daily_candidates, selection=selection, rng=rng)
        selected_symbols_by_date[entry_date] = {
            candidate.symbol for candidate in ordered[:daily_pick_limit]
        }
    selected = [
        item
        for item in forward_5d
        if item["symbol"] in selected_symbols_by_date.get(item["entry_date"], set())
    ]
    unselected = [
        item
        for item in forward_5d
        if item["symbol"] not in selected_symbols_by_date.get(item["entry_date"], set())
    ]
    selected_excess = [
        item
        for item in excess_5d
        if item["symbol"] in selected_symbols_by_date.get(item["entry_date"], set())
    ]
    unselected_excess = [
        item
        for item in excess_5d
        if item["symbol"] not in selected_symbols_by_date.get(item["entry_date"], set())
    ]
    return {
        "selected_forward_5d": _forward_return_summary(selected),
        "unselected_forward_5d": _forward_return_summary(unselected),
        "selected_excess_forward_5d": _forward_return_summary(selected_excess),
        "unselected_excess_forward_5d": _forward_return_summary(unselected_excess),
    }


def collect_entry_candidates_by_date(
    prepared: dict[str, list[PreparedBar]],
    params: SwingParams,
) -> dict[date, list[EntryCandidate]]:
    simulation_context = build_simulation_context(prepared)
    candidate_pools = build_candidate_pools(context=simulation_context, params=params)
    return _entry_candidates_by_date_from_pool(candidate_pools, params)


def rebuild_trades_with_fixed_exit(
    prepared: dict[str, list[PreparedBar]],
    trades: list[Trade],
    params: SwingParams,
    hold_days: int,
) -> list[Trade]:
    rebuilt: list[Trade] = []
    bars_by_symbol_date = {
        (bar.symbol, bar.date): bar for bars in prepared.values() for bar in bars
    }
    bars_by_symbol = {symbol: bars for symbol, bars in prepared.items()}
    for trade in trades:
        bars = bars_by_symbol.get(trade.symbol)
        if not bars:
            continue
        exit_date = _nth_symbol_date(bars, trade.entry_date, hold_days)
        exit_bar = bars_by_symbol_date.get((trade.symbol, exit_date))
        if exit_bar is None:
            continue
        rebuilt.append(
            _close_trade(
                position=Position(
                    symbol=trade.symbol,
                    signal_date=trade.signal_date,
                    signal_close=trade.signal_close,
                    signal_sma_short=trade.signal_sma_short,
                    signal_atr_pct=trade.signal_atr_pct,
                    signal_return_20d=trade.signal_return_20d,
                    signal_avg_turnover=trade.signal_avg_turnover,
                    entry_gap_pct=trade.entry_gap_pct,
                    entry_date=trade.entry_date,
                    entry_price=trade.entry_price,
                    stop_price=trade.entry_price,
                    target_price=trade.entry_price,
                    quantity=trade.quantity,
                    max_exit_date=exit_date,
                    entry_score=trade.entry_score,
                    ranked_position=trade.ranked_position,
                    candidate_count=trade.candidate_count,
                    market_close_above_sma20_ratio=trade.market_close_above_sma20_ratio,
                    market_trend_breadth_ratio=trade.market_trend_breadth_ratio,
                    market_positive_return_5d_ratio=trade.market_positive_return_5d_ratio,
                    market_avg_return_5d=trade.market_avg_return_5d,
                    market_positive_return_20d_ratio=trade.market_positive_return_20d_ratio,
                    market_avg_return_20d=trade.market_avg_return_20d,
                    market_positive_return_60d_ratio=trade.market_positive_return_60d_ratio,
                    market_avg_return_60d=trade.market_avg_return_60d,
                    market_regime=trade.market_regime,
                ),
                exit_date=exit_date,
                exit_price=exit_bar.close,
                exit_reason=f"fixed_{hold_days}d",
                params=params,
            )
        )
    return rebuilt


def build_score_fold_diagnostics(
    candidates: list[EntryCandidate],
    prepared: dict[str, list[PreparedBar]],
    *,
    fold_count: int,
) -> dict[str, Any]:
    rows = _candidate_forward_returns(candidates, prepared, 5)
    if fold_count <= 0 or not rows:
        return {"fold_count": 0, "folds": []}
    dates = sorted({row["entry_date"] for row in rows})
    effective_fold_count = min(fold_count, len(dates))
    folds: list[dict[str, Any]] = []
    for idx in range(effective_fold_count):
        start_idx = (idx * len(dates)) // effective_fold_count
        end_idx = ((idx + 1) * len(dates)) // effective_fold_count
        fold_dates = set(dates[start_idx:end_idx])
        fold_rows = [row for row in rows if row["entry_date"] in fold_dates]
        by_score = sorted(fold_rows, key=lambda row: row["score"])
        tail_count = max(1, len(by_score) // 5) if by_score else 0
        low_tail = by_score[:tail_count]
        high_tail = by_score[-tail_count:] if tail_count else []
        folds.append(
            {
                "fold": idx + 1,
                "start_date": dates[start_idx].isoformat(),
                "end_date": dates[end_idx - 1].isoformat(),
                "candidate_count": len(fold_rows),
                "rank_ic_5d": _spearman_rank_ic(
                    [float(row["score"]) for row in fold_rows],
                    [float(row["forward_return"]) for row in fold_rows],
                ),
                "low_score_tail": _forward_return_summary(low_tail),
                "high_score_tail": _forward_return_summary(high_tail),
            }
        )
    rank_ics = [fold["rank_ic_5d"] for fold in folds if fold["rank_ic_5d"] is not None]
    return {
        "fold_count": effective_fold_count,
        "avg_rank_ic_5d": None if not rank_ics else _round(sum(rank_ics) / len(rank_ics), 4),
        "folds": folds,
    }


def _candidate_forward_returns(
    candidates: list[EntryCandidate],
    prepared: dict[str, list[PreparedBar]],
    hold_days: int,
) -> list[dict[str, Any]]:
    bars_by_symbol_date = {
        (bar.symbol, bar.date): bar for bars in prepared.values() for bar in bars
    }
    dates_by_symbol = _dates_by_symbol(prepared)
    date_index_by_symbol = _date_index_by_symbol(dates_by_symbol)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        dates = dates_by_symbol.get(candidate.symbol)
        indexes = date_index_by_symbol.get(candidate.symbol)
        if not dates or not indexes:
            continue
        exit_date = _nth_symbol_date_from_index(dates, indexes, candidate.entry_date, hold_days)
        exit_bar = bars_by_symbol_date.get((candidate.symbol, exit_date))
        if exit_bar is None or candidate.entry_price <= 0:
            continue
        rows.append(
            {
                "symbol": candidate.symbol,
                "entry_date": candidate.entry_date,
                "score": candidate.score,
                "forward_return": (exit_bar.close / candidate.entry_price) - 1.0,
            }
        )
    return rows


def _candidate_excess_forward_returns(
    candidates: list[EntryCandidate],
    candidate_pools: CandidatePools,
    prepared: dict[str, list[PreparedBar]],
    hold_days: int,
) -> list[dict[str, Any]]:
    universe_returns_by_date = _template_forward_returns_by_date(
        candidate_pools.tradable_by_date,
        prepared,
        hold_days,
    )
    candidate_returns = _candidate_forward_returns(candidates, prepared, hold_days)
    rows: list[dict[str, Any]] = []
    for row in candidate_returns:
        date_returns = universe_returns_by_date.get(row["entry_date"])
        if not date_returns:
            continue
        universe_avg = sum(date_returns) / len(date_returns)
        rows.append(
            {
                "symbol": row["symbol"],
                "entry_date": row["entry_date"],
                "score": row["score"],
                "forward_return": float(row["forward_return"]) - universe_avg,
                "absolute_forward_return": row["forward_return"],
                "universe_forward_return": universe_avg,
            }
        )
    return rows


def _template_forward_returns_by_date(
    templates_by_date: dict[date, list[EntryTemplate]],
    prepared: dict[str, list[PreparedBar]],
    hold_days: int,
) -> dict[date, list[float]]:
    bars_by_symbol_date = {
        (bar.symbol, bar.date): bar for bars in prepared.values() for bar in bars
    }
    dates_by_symbol = _dates_by_symbol(prepared)
    date_index_by_symbol = _date_index_by_symbol(dates_by_symbol)
    rows: dict[date, list[float]] = defaultdict(list)
    for entry_date, templates in templates_by_date.items():
        for template in templates:
            dates = dates_by_symbol.get(template.symbol)
            indexes = date_index_by_symbol.get(template.symbol)
            if not dates or not indexes:
                continue
            exit_date = _nth_symbol_date_from_index(
                dates,
                indexes,
                template.entry_date,
                hold_days,
            )
            exit_bar = bars_by_symbol_date.get((template.symbol, exit_date))
            if exit_bar is None or template.entry_price <= 0:
                continue
            rows[entry_date].append((exit_bar.close / template.entry_price) - 1.0)
    return rows


def _forward_return_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = sorted(float(row["forward_return"]) for row in rows)
    if not returns:
        return {
            "count": 0,
            "avg_return": None,
            "median_return": None,
            "win_rate": None,
            "p05_return": None,
            "worst_return": None,
        }
    return {
        "count": len(returns),
        "avg_return": _round(sum(returns) / len(returns), 6),
        "median_return": _round(_median(returns), 6),
        "win_rate": _round(sum(1 for item in returns if item > 0) / len(returns), 4),
        "p05_return": _round(returns[max(0, int(len(returns) * 0.05) - 1)], 6),
        "worst_return": _round(returns[0], 6),
    }


def _spearman_rank_ic(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(ordered):
        end = idx + 1
        while end < len(ordered) and ordered[end][1] == ordered[idx][1]:
            end += 1
        avg_rank = (idx + 1 + end) / 2
        for original_idx, _ in ordered[idx:end]:
            ranks[original_idx] = avg_rank
        idx = end
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        return None
    return _round(num / (den_x * den_y), 4)


def _median(values: list[float]) -> float:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2


def parse_seed_list(raw: str) -> list[int]:
    if not raw.strip():
        return []
    return [int(item) for item in raw.split(",") if item.strip()]


def parse_date_list(raw: str) -> list[date]:
    if not raw.strip():
        return []
    return [date.fromisoformat(item.strip()) for item in raw.split(",") if item.strip()]


def parse_baseline_kind_list(raw: str) -> list[BaselineKind]:
    if not raw.strip():
        return []
    allowed = set(RANDOM_BASELINE_KINDS)
    kinds: list[BaselineKind] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        if value not in allowed:
            raise ValueError(f"unknown random baseline kind: {value}")
        kinds.append(cast(BaselineKind, value))
    return kinds


def parse_candidate_list(raw: str) -> tuple[CandidateName, ...]:
    if not raw.strip():
        return RESEARCH_CANDIDATES
    allowed = set(RESEARCH_CANDIDATES)
    candidates: list[CandidateName] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        if value not in allowed:
            raise ValueError(f"unknown research candidate: {value}")
        candidates.append(cast(CandidateName, value))
    return tuple(candidates)


def execution_stress_from_args(args: argparse.Namespace) -> ExecutionStress:
    return ExecutionStress(
        exit_before_entry_at_open=bool(args.exit_before_entry_at_open),
        limit_down_unfillable=bool(args.limit_down_unfillable_stress),
        gap_stop_additional_slippage_rate=float(args.gap_stop_additional_slippage_rate),
    )


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
        "market_regime": _grouped_trade_summary(
            trades,
            key_fn=lambda trade: "missing" if trade.market_regime is None else trade.market_regime,
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
        "market_regime",
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


def _entry_score(bar: PreparedBar, params: SwingParams | None = None) -> float:
    if params is not None and params.entry_mode == "breakout_continuation":
        return _breakout_entry_score(bar)
    return _trend_pullback_entry_score(bar)


def _random_baseline_score(bar: PreparedBar) -> float:
    return _trend_pullback_entry_score(bar)


def _trend_pullback_entry_score(bar: PreparedBar) -> float:
    assert bar.return_20d is not None
    assert bar.sma_short is not None
    distance_above_sma = max((bar.close / bar.sma_short) - 1.0, 0.0)
    turnover_score = 0.0 if bar.avg_turnover is None else min(bar.avg_turnover / 1e9, 2.0)
    return bar.return_20d - distance_above_sma + turnover_score * 0.05


def _breakout_entry_score(bar: PreparedBar) -> float:
    assert bar.return_20d is not None
    assert bar.return_60d is not None
    assert bar.atr is not None
    assert bar.avg_turnover is not None
    assert bar.prior_high_breakout is not None
    breakout_strength = (bar.close / bar.prior_high_breakout) - 1.0
    turnover_multiple = bar.turnover / bar.avg_turnover if bar.avg_turnover > 0 else 0.0
    atr_pct = bar.atr / bar.close
    return (
        breakout_strength * 3.0
        + bar.return_20d * 0.4
        + bar.return_60d * 0.2
        + min(turnover_multiple, 3.0) * 0.03
        - max(atr_pct - 0.05, 0.0)
    )


def _nth_symbol_date(bars: list[PreparedBar], start_date: date, n: int) -> date:
    dates = [bar.date for bar in bars]
    try:
        start_idx = dates.index(start_date)
    except ValueError as exc:
        raise ValueError(f"start_date not found: {start_date}") from exc
    return dates[min(start_idx + n, len(dates) - 1)]


def _dates_by_symbol(prepared: dict[str, list[PreparedBar]]) -> dict[str, list[date]]:
    return {symbol: [bar.date for bar in bars] for symbol, bars in prepared.items()}


def _date_index_by_symbol(
    dates_by_symbol: dict[str, list[date]],
) -> dict[str, dict[date, int]]:
    return {
        symbol: {item_date: idx for idx, item_date in enumerate(dates)}
        for symbol, dates in dates_by_symbol.items()
    }


def _nth_symbol_date_from_index(
    dates: list[date],
    indexes: dict[date, int],
    start_date: date,
    n: int,
) -> date:
    start_idx = indexes.get(start_date)
    if start_idx is None:
        raise ValueError(f"start_date not found: {start_date}")
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


def _prior_high(values: list[float], idx: int, period: int) -> float | None:
    if idx < period:
        return None
    return max(values[idx - period : idx])


def _prior_range_pct(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    idx: int,
    period: int,
) -> float | None:
    if idx < period or closes[idx] <= 0:
        return None
    return (max(highs[idx - period : idx]) - min(lows[idx - period : idx])) / closes[idx]


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
