#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from event_research_common import (
    EVALUATION_SPLITS,
    read_jsonl,
    read_ohlcv_csv,
    select_observations_for_split,
)
from trade_contracts.event_research import ObservationRecord


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize low-frequency portfolio block stability for the fixed "
            "earnings+dividend-increase event cluster candidate. Research-only."
        )
    )
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--ohlcv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--candidate-id",
        help="Filled after loading simulate-event-portfolio.py.",
    )
    parser.add_argument("--split", choices=EVALUATION_SPLITS, default="development")
    parser.add_argument("--include-locked-oos", action="store_true")
    parser.add_argument("--block-trading-days", type=int, default=60)
    parser.add_argument("--capital", action="append", default=[])
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--max-new-positions-per-day", type=int)
    parser.add_argument("--max-notional-per-position-pct", type=Decimal, default=Decimal("0.20"))
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--entry-additional-slippage-bps", type=Decimal, default=Decimal("0"))
    parser.add_argument("--exit-additional-slippage-bps", type=Decimal, default=Decimal("0"))
    parser.add_argument("--random-seeds", type=int, default=300)
    args = parser.parse_args()

    if args.split in {"locked-oos", "all"} and not args.include_locked_oos:
        parser.error("--include-locked-oos is required when --split is locked-oos or all")
    if args.block_trading_days < 1:
        parser.error("--block-trading-days must be >= 1")
    if args.random_seeds < 1:
        parser.error("--random-seeds must be >= 1")
    if args.max_positions < 1:
        parser.error("--max-positions must be >= 1")
    if args.max_new_positions_per_day is not None and args.max_new_positions_per_day < 1:
        parser.error("--max-new-positions-per-day must be >= 1")
    if args.entry_additional_slippage_bps < 0:
        parser.error("--entry-additional-slippage-bps must be >= 0")
    if args.exit_additional_slippage_bps < 0:
        parser.error("--exit-additional-slippage-bps must be >= 0")

    sim = _load_portfolio_module()
    if args.candidate_id is not None and args.candidate_id not in sim.CANDIDATE_IDS:
        parser.error(f"unsupported --candidate-id: {args.candidate_id}")
    candidate_id = args.candidate_id or sim.CLUSTER_EARNINGS_DIVIDEND_FIXED20_STOP_CANDIDATE_ID
    spec = sim.candidate_spec(candidate_id)
    observations = [ObservationRecord.model_validate(row) for row in read_jsonl(args.observations)]
    split_observations, split_info = select_observations_for_split(observations, split=args.split)
    selected_observations = sim.selected_observations_for_candidate(split_observations, spec)
    selected_candidates = [
        candidate
        for obs in selected_observations
        if (candidate := sim.portfolio_candidate_from_observation(obs, spec=spec)) is not None
    ]
    trading_dates = sorted({row.date for row in read_ohlcv_csv(args.ohlcv)})
    blocks = _build_blocks(
        trading_dates,
        start=min((candidate.signal_date for candidate in selected_candidates), default=None),
        end=max((candidate.signal_date for candidate in selected_candidates), default=None),
        block_trading_days=args.block_trading_days,
    )
    params_by_capital = [
        sim.PortfolioParams(
            capital=capital,
            max_positions=args.max_positions,
            max_new_positions_per_day=args.max_new_positions_per_day,
            max_notional_per_position_pct=args.max_notional_per_position_pct,
            lot_size=args.lot_size,
            entry_additional_slippage_bps=args.entry_additional_slippage_bps,
            exit_additional_slippage_bps=args.exit_additional_slippage_bps,
        )
        for capital in ([Decimal(value) for value in args.capital] or [Decimal("1000000")])
    ]
    ohlcv_rows = read_ohlcv_csv(args.ohlcv)
    rows = _block_rows(
        sim,
        spec,
        blocks=blocks,
        selected_candidates=selected_candidates,
        selected_observations=selected_observations,
        params_by_capital=params_by_capital,
        ohlcv_rows=ohlcv_rows,
        random_seeds=args.random_seeds,
    )
    payload = {
        "candidate_id": spec.candidate_id,
        "research_only": True,
        "paper_live_enabled": False,
        "diagnostic": (
            "Independent block-level portfolio stability. Capital is reset per block; "
            "aggregate deployment gate is evaluated separately."
        ),
        "block_trading_days": args.block_trading_days,
        "evaluation_split": split_info,
        "selected_candidate_count": len(selected_candidates),
        "rows": rows,
        "summary": _summary(rows),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    _write_csv(args.output_csv, rows)
    print(
        "event_cluster_portfolio_stability "
        f"split={args.split} blocks={len(blocks)} candidates={len(selected_candidates)} "
        f"output={args.output_json}"
    )
    return 0


def _load_portfolio_module():
    path = Path(__file__).resolve().parent / "simulate-event-portfolio.py"
    spec = importlib.util.spec_from_file_location("simulate_event_portfolio_for_stability", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_blocks(
    trading_dates: list[date],
    *,
    start: date | None,
    end: date | None,
    block_trading_days: int,
) -> list[dict[str, Any]]:
    if start is None or end is None:
        return []
    active_dates = [item for item in trading_dates if start <= item <= end]
    blocks = []
    for start_idx in range(0, len(active_dates), block_trading_days):
        block_dates = active_dates[start_idx : start_idx + block_trading_days]
        if not block_dates:
            continue
        blocks.append(
            {
                "block_id": f"block_{len(blocks):03d}",
                "start": block_dates[0],
                "end": block_dates[-1],
                "dates": set(block_dates),
            }
        )
    return blocks


def _block_rows(
    sim,
    spec,
    *,
    blocks: list[dict[str, Any]],
    selected_candidates: list[Any],
    selected_observations: list[ObservationRecord],
    params_by_capital: list[Any],
    ohlcv_rows: list[Any],
    random_seeds: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        block_candidates = [
            candidate
            for candidate in selected_candidates
            if candidate.signal_date in block["dates"]
        ]
        block_observations = [
            obs
            for obs in selected_observations
            if date.fromisoformat(obs.signal_date) in block["dates"]
        ]
        random_baselines = None
        if block_candidates:
            random_baselines = sim.portfolio_random_baselines(
                block_candidates,
                event_observations=block_observations,
                ohlcv_rows=ohlcv_rows,
                params_by_capital=params_by_capital,
                seed_count=random_seeds,
                selection_order="feature_time_symbol",
                spec=spec,
            )
        for params in params_by_capital:
            result = sim.simulate_portfolio(
                block_candidates,
                params=params,
                selection_order="feature_time_symbol",
                spec=spec,
            )
            random_row = None
            if random_baselines is not None:
                random_row = random_baselines["by_capital"].get(str(params.capital))
            rows.append(
                {
                    "block_id": block["block_id"],
                    "block_start": block["start"].isoformat(),
                    "block_end": block["end"].isoformat(),
                    "capital": float(params.capital),
                    "candidate_count": result.candidate_count,
                    "opened_trade_count": result.opened_trade_count,
                    "total_pnl": result.total_pnl,
                    "profit_factor": result.profit_factor,
                    "max_drawdown": result.max_drawdown,
                    "positive_trade_ratio": result.positive_trade_ratio,
                    "random_selected_percentile": None
                    if random_row is None
                    else random_row["selected_percentile"],
                    "random_median": None if random_row is None else random_row["random_median"],
                    "random_p75": None if random_row is None else random_row["random_p75"],
                    "random_p90": None if random_row is None else random_row["random_p90"],
                    "random_seed_count": 0 if random_row is None else random_row["random_count"],
                    "random_fallback_rate": None
                    if random_baselines is None
                    else random_baselines["coverage"]["fallback_rate"],
                }
            )
    return rows


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_capital: dict[float, list[dict[str, Any]]] = {}
    for row in rows:
        by_capital.setdefault(float(row["capital"]), []).append(row)
    out = []
    for capital, group in sorted(by_capital.items()):
        active = [row for row in group if int(row["opened_trade_count"]) > 0]
        pnls = [float(row["total_pnl"]) for row in active]
        percentiles = [
            float(row["random_selected_percentile"])
            for row in active
            if row["random_selected_percentile"] is not None
        ]
        out.append(
            {
                "capital": capital,
                "block_count": len(group),
                "active_block_count": len(active),
                "positive_block_ratio": None
                if not pnls
                else sum(1 for pnl in pnls if pnl > 0) / len(pnls),
                "worst_block_pnl": None if not pnls else min(pnls),
                "median_block_pnl": None if not pnls else _median(pnls),
                "worst_block_dd": None
                if not active
                else max(float(row["max_drawdown"]) for row in active),
                "median_random_selected_percentile": None
                if not percentiles
                else _median(percentiles),
                "total_opened_trades": sum(int(row["opened_trade_count"]) for row in active),
            }
        )
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


if __name__ == "__main__":
    raise SystemExit(main())
