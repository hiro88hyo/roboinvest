#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    ROUND_TRIP_COST_RATE,
    OhlcvRow,
    fundamental_rule_allows,
    read_jsonl,
    read_ohlcv_csv,
    select_observations_for_split,
    technical_veto_allows,
)
from trade_contracts.event_research import EventType, ObservationRecord

FORECAST_FIXED5_CANDIDATE_ID = "event_forecast_revision_fair_value_tech_fixed5_v0_research"
DIVIDEND_FIXED2_CANDIDATE_ID = "event_dividend_increase_yield3_fixed2_v0_research"
CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID = (
    "event_cluster_earnings_dividend_increase_fixed20_stop_v0_research"
)
CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID = (
    "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
)
CANDIDATE_ID = FORECAST_FIXED5_CANDIDATE_ID
CANDIDATE_IDS = (
    FORECAST_FIXED5_CANDIDATE_ID,
    DIVIDEND_FIXED2_CANDIDATE_ID,
    CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID,
    CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID,
)
COST_PER_SIDE_RATE = ROUND_TRIP_COST_RATE / Decimal("2")
SELECTION_ORDERS = (
    "feature_time_symbol",
    "feature_time_symbol_reverse",
    "symbol_asc",
    "symbol_desc",
    "entry_price_asc",
    "entry_price_desc",
)


@dataclass(frozen=True, slots=True)
class PortfolioParams:
    capital: Decimal = Decimal("1000000")
    max_positions: int = 5
    max_new_positions_per_day: int | None = None
    max_notional_per_position_pct: Decimal = Decimal("0.20")
    lot_size: int = 100
    entry_additional_slippage_bps: Decimal = Decimal("0")
    exit_additional_slippage_bps: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    exit_horizon: int
    catastrophic_stop: bool = False


@dataclass(frozen=True, slots=True)
class Position:
    observation_id: str
    event_id: str
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    entry_cost: Decimal

    @property
    def entry_notional(self) -> Decimal:
        return self.entry_price * Decimal(self.quantity)


@dataclass(frozen=True, slots=True)
class PortfolioCandidate:
    observation_id: str
    event_id: str
    symbol: str
    signal_date: date
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    sort_key: str


@dataclass(frozen=True, slots=True)
class PortfolioTrade:
    observation_id: str
    event_id: str
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: str
    exit_price: str
    quantity: int
    entry_notional: str
    exit_notional: str
    entry_cost: str
    exit_cost: str
    pnl: str
    return_pct: float


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    candidate_id: str
    params: dict[str, Any]
    candidate_count: int
    opened_trade_count: int
    skipped_same_symbol_count: int
    skipped_position_cap_count: int
    skipped_daily_entry_cap_count: int
    skipped_cash_count: int
    skipped_lot_count: int
    skipped_missing_label_count: int
    total_pnl: float
    profit_factor: float | None
    max_drawdown: float
    ending_cash: float
    positive_trade_ratio: float | None
    trades: list[PortfolioTrade]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate the preregistered event forecast-revision fixed5 research candidate "
            "with simple portfolio constraints. Research-only; no paper/live route."
        )
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=EVALUATION_SPLITS,
        default="development",
        help="Evaluation split. Default excludes locked OOS details.",
    )
    parser.add_argument(
        "--include-locked-oos",
        action="store_true",
        help="Required when --split is locked-oos or all.",
    )
    parser.add_argument(
        "--capital",
        action="append",
        default=[],
        help="Starting capital. May be repeated. Defaults to 1000000.",
    )
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--max-new-positions-per-day", type=int)
    parser.add_argument("--max-notional-per-position-pct", type=Decimal, default=Decimal("0.20"))
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument(
        "--entry-additional-slippage-bps",
        type=Decimal,
        default=Decimal("0"),
        help="Additional adverse entry slippage in basis points for execution stress.",
    )
    parser.add_argument(
        "--exit-additional-slippage-bps",
        type=Decimal,
        default=Decimal("0"),
        help="Additional adverse exit slippage in basis points for execution stress.",
    )
    parser.add_argument(
        "--candidate-id",
        choices=CANDIDATE_IDS,
        default=FORECAST_FIXED5_CANDIDATE_ID,
    )
    parser.add_argument(
        "--selection-order",
        choices=SELECTION_ORDERS,
        default="feature_time_symbol",
        help="Same-day candidate ordering for the primary simulation.",
    )
    parser.add_argument(
        "--include-selection-order-stress",
        action="store_true",
        help="Run all same-day ordering variants as diagnostics.",
    )
    parser.add_argument(
        "--ohlcv",
        type=Path,
        help="Daily OHLCV CSV for portfolio-level same-symbol random-date baselines.",
    )
    parser.add_argument("--random-seeds", type=int, default=300)
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.max_positions < 1:
        parser.error("--max-positions must be >= 1")
    if args.max_new_positions_per_day is not None and args.max_new_positions_per_day < 1:
        parser.error("--max-new-positions-per-day must be >= 1")
    if args.max_notional_per_position_pct <= 0:
        parser.error("--max-notional-per-position-pct must be positive")
    if args.lot_size < 1:
        parser.error("--lot-size must be >= 1")
    if args.entry_additional_slippage_bps < 0:
        parser.error("--entry-additional-slippage-bps must be >= 0")
    if args.exit_additional_slippage_bps < 0:
        parser.error("--exit-additional-slippage-bps must be >= 0")
    if args.random_seeds < 1:
        parser.error("--random-seeds must be >= 1")

    all_observations = [
        ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)
    ]
    split_observations, split_info = select_observations_for_split(
        all_observations,
        split=args.split,
    )
    spec = candidate_spec(args.candidate_id)
    candidates = selected_observations_for_candidate(split_observations, spec)
    portfolio_candidates = [
        candidate
        for obs in candidates
        if (candidate := portfolio_candidate_from_observation(obs, spec=spec)) is not None
    ]
    capitals = [Decimal(value) for value in args.capital] or [Decimal("1000000")]
    results = []
    params_by_capital = []
    for capital in capitals:
        params = PortfolioParams(
            capital=capital,
            max_positions=args.max_positions,
            max_new_positions_per_day=args.max_new_positions_per_day,
            max_notional_per_position_pct=args.max_notional_per_position_pct,
            lot_size=args.lot_size,
            entry_additional_slippage_bps=args.entry_additional_slippage_bps,
            exit_additional_slippage_bps=args.exit_additional_slippage_bps,
        )
        params_by_capital.append(params)
        results.append(
            simulate_portfolio(
                portfolio_candidates,
                params=params,
                selection_order=args.selection_order,
                spec=spec,
            )
        )

    random_baselines = {"enabled": False, "reason": "provide --ohlcv to compute this diagnostic"}
    if args.ohlcv is not None:
        random_baselines = portfolio_random_baselines(
            portfolio_candidates,
            event_observations=candidates,
            ohlcv_rows=read_ohlcv_csv(args.ohlcv),
            params_by_capital=params_by_capital,
            seed_count=args.random_seeds,
            selection_order=args.selection_order,
            spec=spec,
        )

    selection_order_stress = {"enabled": False}
    if args.include_selection_order_stress:
        selection_order_stress = {
            "enabled": True,
            "orders": {
                order: [
                    result_summary(
                        simulate_portfolio(
                            portfolio_candidates,
                            params=params,
                            selection_order=order,
                            spec=spec,
                        )
                    )
                    for params in params_by_capital
                ]
                for order in SELECTION_ORDERS
            },
        }

    payload = {
        "candidate_id": spec.candidate_id,
        "research_only": True,
        "paper_live_enabled": False,
        "execution_assumptions": {
            "entry": "next_open_unconditional",
            "exit": f"fixed_{spec.exit_horizon}d_close_exit",
            "same_day_exit_cash_reuse": False,
            "same_symbol_overlap": False,
            "cost_per_side_rate": str(COST_PER_SIDE_RATE),
            "round_trip_cost_rate": str(ROUND_TRIP_COST_RATE),
            "lot_size": args.lot_size,
            "selection_order": args.selection_order,
            "entry_additional_slippage_bps": str(args.entry_additional_slippage_bps),
            "exit_additional_slippage_bps": str(args.exit_additional_slippage_bps),
        },
        "evaluation_split": split_info,
        "results": [result_to_json(result) for result in results],
        "portfolio_random_baselines": random_baselines,
        "selection_order_stress": selection_order_stress,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    write_trades_csv(args.output_csv, results)
    print(
        "event_portfolio_simulation "
        f"split={args.split} candidates={len(candidates)} capitals={len(capitals)} "
        f"output={args.output_json}"
    )
    return 0


def candidate_spec(candidate_id: str) -> CandidateSpec:
    if candidate_id == FORECAST_FIXED5_CANDIDATE_ID:
        return CandidateSpec(candidate_id=candidate_id, exit_horizon=5)
    if candidate_id == DIVIDEND_FIXED2_CANDIDATE_ID:
        return CandidateSpec(candidate_id=candidate_id, exit_horizon=2)
    if candidate_id == CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID:
        return CandidateSpec(candidate_id=candidate_id, exit_horizon=20, catastrophic_stop=True)
    if candidate_id == CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID:
        return CandidateSpec(candidate_id=candidate_id, exit_horizon=20, catastrophic_stop=True)
    raise ValueError(f"unsupported candidate_id: {candidate_id}")


def selected_observations_for_candidate(
    observations: list[ObservationRecord],
    spec: CandidateSpec,
) -> list[ObservationRecord]:
    if spec.candidate_id in {
        CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID,
        CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID,
    }:
        selected: list[ObservationRecord] = []
        clusters: dict[str, list[ObservationRecord]] = defaultdict(list)
        for obs in observations:
            key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
            clusters[key].append(obs)
        for items in clusters.values():
            if cluster_rule_allows(items, spec=spec):
                selected.extend(cluster_trade_representatives(items))
        return selected
    return cluster_trade_representatives(
        [obs for obs in observations if candidate_allows(obs, spec)]
    )


def candidate_allows(obs: ObservationRecord, spec: CandidateSpec) -> bool:
    if spec.candidate_id == FORECAST_FIXED5_CANDIDATE_ID:
        return (
            obs.event_type == EventType.FORECAST_REVISION
            and fundamental_rule_allows(obs)
            and _forecast_per_valid_and_fair(obs)
            and technical_veto_allows(obs)
        )
    if spec.candidate_id == DIVIDEND_FIXED2_CANDIDATE_ID:
        dividend_yield = _dividend_yield(obs)
        return (
            obs.event_type == EventType.DIVIDEND_REVISION
            and obs.event_subtype == "increase"
            and dividend_yield is not None
            and dividend_yield >= Decimal("0.03")
        )
    if spec.candidate_id in {
        CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID,
        CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID,
    }:
        return False
    raise ValueError(f"unsupported candidate_id: {spec.candidate_id}")


def cluster_rule_allows(items: list[ObservationRecord], *, spec: CandidateSpec) -> bool:
    if not cluster_earnings_dividend_increase_allows(items):
        return False
    if spec.candidate_id == CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID:
        return True
    if spec.candidate_id == CLUSTER_EARNINGS_DIVIDEND_VALUE_GUARD_FIXED20_STOP_CANDIDATE_ID:
        return cluster_forecast_per_missing_or_lte(items, Decimal("15"))
    raise ValueError(f"unsupported cluster candidate_id: {spec.candidate_id}")


def cluster_earnings_dividend_increase_allows(items: list[ObservationRecord]) -> bool:
    has_earnings = any(obs.event_type == EventType.EARNINGS_RESULT for obs in items)
    has_dividend_increase = any(
        obs.event_type == EventType.DIVIDEND_REVISION and obs.event_subtype == "increase"
        for obs in items
    )
    return has_earnings and has_dividend_increase


def cluster_forecast_per_missing_or_lte(
    items: list[ObservationRecord],
    threshold: Decimal,
) -> bool:
    values = [
        value
        for obs in items
        if (value := _as_decimal(obs.valuation_features_v0.forecast_per.value)) is not None
    ]
    return not values or min(values) <= threshold


def cluster_trade_representatives(
    observations: list[ObservationRecord],
) -> list[ObservationRecord]:
    by_trade: dict[str, ObservationRecord] = {}
    for obs in sorted(
        observations,
        key=lambda item: (
            item.entry_date,
            item.feature_cutoff_at.isoformat(),
            item.symbol,
            item.event_id,
        ),
    ):
        key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
        by_trade.setdefault(key, obs)
    return list(by_trade.values())


def simulate_portfolio(
    observations: list[ObservationRecord | PortfolioCandidate],
    *,
    params: PortfolioParams,
    selection_order: str = "feature_time_symbol",
    spec: CandidateSpec | None = None,
) -> PortfolioResult:
    spec = candidate_spec(FORECAST_FIXED5_CANDIDATE_ID) if spec is None else spec
    candidate_count = len(observations)
    missing_candidate_count = 0
    portfolio_candidates: list[PortfolioCandidate] = []
    for obs in observations:
        candidate = (
            obs
            if isinstance(obs, PortfolioCandidate)
            else portfolio_candidate_from_observation(obs, spec=spec)
        )
        if candidate is None:
            missing_candidate_count += 1
            continue
        portfolio_candidates.append(candidate)

    candidates_by_entry: dict[date, list[PortfolioCandidate]] = defaultdict(list)
    for candidate in portfolio_candidates:
        candidates_by_entry[candidate.entry_date].append(candidate)
    trading_dates = sorted(
        set(candidates_by_entry) | {candidate.exit_date for candidate in portfolio_candidates}
    )

    cash = params.capital
    positions: dict[str, Position] = {}
    trades: list[PortfolioTrade] = []
    realized_pnls: list[Decimal] = []
    skipped_same_symbol = 0
    skipped_position_cap = 0
    skipped_daily_entry_cap = 0
    skipped_cash = 0
    skipped_lot = 0
    skipped_missing_label = 0

    for current_date in trading_dates:
        opened_today = 0
        for candidate in sort_candidates(
            candidates_by_entry.get(current_date, []),
            order=selection_order,
        ):
            if candidate.symbol in positions:
                skipped_same_symbol += 1
                continue
            if len(positions) >= params.max_positions:
                skipped_position_cap += 1
                continue
            if (
                params.max_new_positions_per_day is not None
                and opened_today >= params.max_new_positions_per_day
            ):
                skipped_daily_entry_cap += 1
                continue
            position = build_position(candidate, params=params, cash=cash)
            if position is None:
                skipped_lot += 1
                continue
            required_cash = position.entry_notional + position.entry_cost
            if required_cash > cash:
                skipped_cash += 1
                continue
            cash -= required_cash
            positions[position.symbol] = position
            opened_today += 1

        # Fixed-horizon exits are close exits. Cash is therefore released after
        # same-day entry decisions and cannot fund new entries on this date.
        for symbol, position in list(positions.items()):
            if position.exit_date != current_date:
                continue
            trade, cash_delta, pnl = close_position(position)
            cash += cash_delta
            trades.append(trade)
            realized_pnls.append(pnl)
            del positions[symbol]

    # Defensive close for malformed inputs; normal point-in-time observations
    # should already include an exit label and therefore exit in the loop above.
    for symbol, position in list(positions.items()):
        trade, cash_delta, pnl = close_position(position)
        cash += cash_delta
        trades.append(trade)
        realized_pnls.append(pnl)
        del positions[symbol]

    wins = [pnl for pnl in realized_pnls if pnl > 0]
    losses = [pnl for pnl in realized_pnls if pnl < 0]
    gross_win = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    total_pnl = sum(realized_pnls, Decimal("0"))
    return PortfolioResult(
        candidate_id=spec.candidate_id,
        params={
            "capital": float(params.capital),
            "max_positions": params.max_positions,
            "max_new_positions_per_day": params.max_new_positions_per_day,
            "max_notional_per_position_pct": float(params.max_notional_per_position_pct),
            "lot_size": params.lot_size,
            "entry_additional_slippage_bps": float(params.entry_additional_slippage_bps),
            "exit_additional_slippage_bps": float(params.exit_additional_slippage_bps),
        },
        candidate_count=candidate_count,
        opened_trade_count=len(trades),
        skipped_same_symbol_count=skipped_same_symbol,
        skipped_position_cap_count=skipped_position_cap,
        skipped_daily_entry_cap_count=skipped_daily_entry_cap,
        skipped_cash_count=skipped_cash,
        skipped_lot_count=skipped_lot,
        skipped_missing_label_count=skipped_missing_label + missing_candidate_count,
        total_pnl=float(total_pnl),
        profit_factor=None if gross_loss == 0 else float(gross_win / gross_loss),
        max_drawdown=float(max_drawdown(realized_pnls)),
        ending_cash=float(cash),
        positive_trade_ratio=None
        if not trades
        else sum(1 for pnl in realized_pnls if pnl > 0) / len(realized_pnls),
        trades=trades,
    )


def build_position(
    candidate: PortfolioCandidate,
    *,
    params: PortfolioParams,
    cash: Decimal,
) -> Position | None:
    entry_price = apply_adverse_slippage(
        candidate.entry_price,
        bps=params.entry_additional_slippage_bps,
        side="entry",
    )
    exit_price = apply_adverse_slippage(
        candidate.exit_price,
        bps=params.exit_additional_slippage_bps,
        side="exit",
    )
    if entry_price <= 0:
        return None
    max_position_notional = params.capital * params.max_notional_per_position_pct
    cash_limited_notional = cash / (Decimal("1") + COST_PER_SIDE_RATE)
    target_notional = min(max_position_notional, cash_limited_notional)
    raw_quantity = (target_notional / entry_price).to_integral_value(rounding=ROUND_FLOOR)
    quantity = int(raw_quantity) // params.lot_size * params.lot_size
    if quantity <= 0:
        return None
    entry_notional = entry_price * Decimal(quantity)
    return Position(
        observation_id=candidate.observation_id,
        event_id=candidate.event_id,
        symbol=candidate.symbol,
        entry_date=candidate.entry_date,
        exit_date=candidate.exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        entry_cost=entry_notional * COST_PER_SIDE_RATE,
    )


def apply_adverse_slippage(price: Decimal, *, bps: Decimal, side: str) -> Decimal:
    if bps == 0:
        return price
    rate = bps / Decimal("10000")
    if side == "entry":
        return price * (Decimal("1") + rate)
    if side == "exit":
        return price * (Decimal("1") - rate)
    raise ValueError(f"unsupported side: {side}")


def close_position(position: Position) -> tuple[PortfolioTrade, Decimal, Decimal]:
    exit_notional = position.exit_price * Decimal(position.quantity)
    exit_cost = exit_notional * COST_PER_SIDE_RATE
    pnl = exit_notional - position.entry_notional - position.entry_cost - exit_cost
    trade = PortfolioTrade(
        observation_id=position.observation_id,
        event_id=position.event_id,
        symbol=position.symbol,
        entry_date=position.entry_date.isoformat(),
        exit_date=position.exit_date.isoformat(),
        entry_price=str(position.entry_price),
        exit_price=str(position.exit_price),
        quantity=position.quantity,
        entry_notional=str(position.entry_notional),
        exit_notional=str(exit_notional),
        entry_cost=str(position.entry_cost),
        exit_cost=str(exit_cost),
        pnl=str(pnl),
        return_pct=float(pnl / position.entry_notional) if position.entry_notional > 0 else 0.0,
    )
    return trade, exit_notional - exit_cost, pnl


def max_drawdown(pnls: list[Decimal]) -> Decimal:
    peak = Decimal("0")
    equity = Decimal("0")
    max_dd = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def portfolio_candidate_from_observation(
    obs: ObservationRecord,
    *,
    spec: CandidateSpec | None = None,
) -> PortfolioCandidate | None:
    spec = candidate_spec(FORECAST_FIXED5_CANDIDATE_ID) if spec is None else spec
    exit_date = _label_exit_date(obs, spec=spec)
    exit_price = _label_exit_price(obs, spec=spec)
    if exit_date is None or exit_price is None:
        return None
    entry_price = _as_decimal(obs.entry_price)
    if entry_price is None:
        return None
    return PortfolioCandidate(
        observation_id=obs.observation_id,
        event_id=obs.event_id,
        symbol=obs.symbol,
        signal_date=date.fromisoformat(obs.signal_date),
        entry_date=date.fromisoformat(obs.entry_date),
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        sort_key=obs.feature_cutoff_at.isoformat(),
    )


def random_portfolio_candidate_from_bars(
    *,
    symbol: str,
    signal_idx: int,
    bars: list[OhlcvRow],
    spec: CandidateSpec,
) -> PortfolioCandidate | None:
    entry_idx = signal_idx + 1
    exit_idx = entry_idx + spec.exit_horizon
    if exit_idx >= len(bars):
        return None
    signal_bar = bars[signal_idx]
    entry_bar = bars[entry_idx]
    exit_bar = bars[exit_idx]
    exit_date = exit_bar.date
    exit_price = exit_bar.close
    if spec.catastrophic_stop:
        stop = catastrophic_stop_from_bars(
            bars[entry_idx : exit_idx + 1],
            entry_price=entry_bar.open,
            fixed_exit_date=exit_bar.date,
            fixed_exit_price=exit_bar.close,
        )
        exit_date = stop[0]
        exit_price = stop[1]
    return PortfolioCandidate(
        observation_id=f"random:{symbol}:{signal_bar.date.isoformat()}",
        event_id=f"random:{symbol}:{signal_bar.date.isoformat()}",
        symbol=symbol,
        signal_date=signal_bar.date,
        entry_date=entry_bar.date,
        exit_date=exit_date,
        entry_price=entry_bar.open,
        exit_price=exit_price,
        sort_key=signal_bar.date.isoformat(),
    )


def catastrophic_stop_from_bars(
    bars: list[OhlcvRow],
    *,
    entry_price: Decimal,
    fixed_exit_date: date,
    fixed_exit_price: Decimal,
) -> tuple[date, Decimal]:
    stop_price = entry_price * Decimal("0.92")
    for bar in bars:
        if bar.open <= stop_price:
            return bar.date, bar.open
        if bar.low <= stop_price:
            return bar.date, stop_price
    return fixed_exit_date, fixed_exit_price


def sort_candidates(
    candidates: list[PortfolioCandidate],
    *,
    order: str,
) -> list[PortfolioCandidate]:
    if order == "feature_time_symbol":
        return sorted(candidates, key=lambda item: (item.sort_key, item.symbol, item.event_id))
    if order == "feature_time_symbol_reverse":
        return sorted(
            candidates,
            key=lambda item: (item.sort_key, item.symbol, item.event_id),
            reverse=True,
        )
    if order == "symbol_asc":
        return sorted(candidates, key=lambda item: (item.symbol, item.sort_key, item.event_id))
    if order == "symbol_desc":
        return sorted(
            candidates,
            key=lambda item: (item.symbol, item.sort_key, item.event_id),
            reverse=True,
        )
    if order == "entry_price_asc":
        return sorted(candidates, key=lambda item: (item.entry_price, item.symbol, item.event_id))
    if order == "entry_price_desc":
        return sorted(
            candidates,
            key=lambda item: (item.entry_price, item.symbol, item.event_id),
            reverse=True,
        )
    raise ValueError(f"unsupported selection order: {order}")


def portfolio_random_baselines(
    selected: list[PortfolioCandidate],
    *,
    event_observations: list[ObservationRecord],
    ohlcv_rows: list[OhlcvRow],
    params_by_capital: list[PortfolioParams],
    seed_count: int,
    selection_order: str,
    spec: CandidateSpec,
) -> dict[str, Any]:
    pools, coverage = random_candidate_pools(
        selected,
        event_observations=event_observations,
        ohlcv_rows=ohlcv_rows,
        spec=spec,
    )
    by_capital: dict[str, list[Decimal]] = {str(params.capital): [] for params in params_by_capital}
    for seed in range(1, seed_count + 1):
        rng = random.Random(seed)
        sampled = [
            rng.choice(pool) if pool else candidate
            for candidate, pool in zip(selected, pools, strict=True)
        ]
        for params in params_by_capital:
            result = simulate_portfolio(
                sampled,
                params=params,
                selection_order=selection_order,
                spec=spec,
            )
            by_capital[str(params.capital)].append(Decimal(str(result.total_pnl)))

    selected_by_capital = {
        str(params.capital): Decimal(
            str(
                simulate_portfolio(
                    selected,
                    params=params,
                    selection_order=selection_order,
                    spec=spec,
                ).total_pnl
            )
        )
        for params in params_by_capital
    }
    return {
        "enabled": True,
        "baseline": "same_symbol_random_date",
        "seed_count": seed_count,
        "coverage": coverage,
        "by_capital": {
            capital: random_summary(values, selected_by_capital[capital])
            for capital, values in by_capital.items()
        },
    }


def random_candidate_pools(
    selected: list[PortfolioCandidate],
    *,
    event_observations: list[ObservationRecord],
    ohlcv_rows: list[OhlcvRow],
    spec: CandidateSpec,
) -> tuple[list[list[PortfolioCandidate]], dict[str, Any]]:
    by_symbol: dict[str, list[OhlcvRow]] = defaultdict(list)
    for row in sorted(ohlcv_rows, key=lambda item: (item.symbol, item.date)):
        by_symbol[row.symbol].append(row)
    event_dates_by_symbol: dict[str, set[date]] = defaultdict(set)
    for obs in event_observations:
        event_dates_by_symbol[obs.symbol].add(date.fromisoformat(obs.signal_date))

    pools: list[list[PortfolioCandidate]] = []
    for candidate in selected:
        bars = by_symbol.get(candidate.symbol, [])
        pool = [
            random_candidate
            for idx, bar in enumerate(bars)
            if bar.date not in event_dates_by_symbol[candidate.symbol]
            if (
                random_candidate := random_portfolio_candidate_from_bars(
                    symbol=candidate.symbol,
                    signal_idx=idx,
                    bars=bars,
                    spec=spec,
                )
            )
            is not None
        ]
        pools.append(pool)

    pool_sizes = [len(pool) for pool in pools]
    matched = sum(1 for pool in pools if pool)
    fallback = len(pools) - matched
    return pools, {
        "matched": matched,
        "unmatched": 0,
        "fallback": fallback,
        "candidate_pool_size_min": min(pool_sizes) if pool_sizes else 0,
        "candidate_pool_size_median": float(_median([Decimal(size) for size in pool_sizes]))
        if pool_sizes
        else 0,
        "candidate_pool_size_max": max(pool_sizes) if pool_sizes else 0,
        "fallback_rate": None if not pools else fallback / len(pools),
    }


def random_summary(values: list[Decimal], selected: Decimal) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "random_count": len(values),
        "selected_net_pnl": float(selected),
        "random_mean": float(sum(values, Decimal("0")) / len(values)) if values else None,
        "random_median": float(_quantile(ordered, Decimal("0.50"))) if values else None,
        "random_p75": float(_quantile(ordered, Decimal("0.75"))) if values else None,
        "random_p90": float(_quantile(ordered, Decimal("0.90"))) if values else None,
        "random_p95": float(_quantile(ordered, Decimal("0.95"))) if values else None,
        "random_max": float(max(values)) if values else None,
        "selected_percentile": percentile(values, selected),
    }


def percentile(values: list[Decimal], selected: Decimal) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value <= selected) / len(values)


def _quantile(ordered: list[Decimal], q: Decimal) -> Decimal:
    if not ordered:
        return Decimal("0")
    if len(ordered) == 1:
        return ordered[0]
    idx = (Decimal(len(ordered) - 1) * q).to_integral_value(rounding=ROUND_FLOOR)
    return ordered[int(idx)]


def _median(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def result_summary(result: PortfolioResult) -> dict[str, Any]:
    row = result_to_json(result)
    row.pop("trades", None)
    return row


def _forecast_per_valid_and_fair(obs: ObservationRecord) -> bool:
    valuation = obs.valuation_features_v0
    value = _as_decimal(valuation.forecast_per.value)
    return bool(valuation.forecast_per_valid) and value is not None and value <= Decimal("25")


def _label_exit_date(obs: ObservationRecord, *, spec: CandidateSpec) -> date | None:
    key = (
        f"catastrophic_stop_exit_date_{spec.exit_horizon}d"
        if spec.catastrophic_stop
        else f"exit_date_{spec.exit_horizon}d"
    )
    value = obs.labels.get(key)
    if value in (None, "") and spec.catastrophic_stop:
        value = obs.labels.get(f"exit_date_{spec.exit_horizon}d")
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


def _label_exit_price(obs: ObservationRecord, *, spec: CandidateSpec) -> Decimal | None:
    if spec.catastrophic_stop:
        stopped_return = _as_decimal(
            obs.labels.get(f"catastrophic_stop_return_{spec.exit_horizon}d")
        )
        if stopped_return is not None:
            return Decimal(str(obs.entry_price)) * (Decimal("1") + stopped_return)
    value = obs.labels.get(f"exit_price_{spec.exit_horizon}d")
    if value not in (None, ""):
        return _as_decimal(value)
    forward_return = _as_decimal(obs.labels.get(f"forward_return_{spec.exit_horizon}d"))
    if forward_return is None:
        return None
    return Decimal(str(obs.entry_price)) * (Decimal("1") + forward_return)


def _dividend_yield(obs: ObservationRecord) -> Decimal | None:
    valuation = obs.valuation_features_v0
    if not valuation.dividend_yield_valid:
        return None
    return _as_decimal(valuation.forecast_dividend_yield.value)


def _as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def result_to_json(result: PortfolioResult) -> dict[str, Any]:
    row = asdict(result)
    row["trades"] = [asdict(trade) for trade in result.trades]
    return row


def write_trades_csv(path: Path, results: list[PortfolioResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "capital",
        "observation_id",
        "event_id",
        "symbol",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "quantity",
        "entry_notional",
        "exit_notional",
        "entry_cost",
        "exit_cost",
        "pnl",
        "return_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            capital = result.params["capital"]
            for trade in result.trades:
                row = asdict(trade)
                row["capital"] = capital
                writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
