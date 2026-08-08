#!/usr/bin/env python3
"""Run the single authorized development simulation for liquidity V0.

This executable is deliberately development-only. It cannot select validation or
locked-OOS feature rows and it refuses to run without a preregistered, hash-bound
execution record.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import polars as pl

CANDIDATE_ID = "liqimp1m_logdiff_v0_research"
CONFIG_STATUS = "REGISTERED_BEFORE_FACTOR_AND_OUTCOME_COMPUTATION"
FEATURE_SCHEMA_VERSION = "liqimp1m_logdiff_feature_cohort_v1"
NORMALIZED_SCHEMA_VERSION = "jquants_liquidity_research_normalized_v1"
RUN_REGISTRATION_STATUS = "REGISTERED_BEFORE_DEVELOPMENT_OUTCOME_COMPUTATION"
RESULT_SCHEMA_VERSION = "liqimp1m_logdiff_development_result_v1"
FEATURE_MANIFEST_FILENAME = "feature-manifest.json"
FEATURE_FILENAME = "feature-cohort.parquet"
NORMALIZED_MANIFEST_FILENAME = "normalized-manifest.json"
FORBIDDEN_FEATURE_COLUMN_PARTS = (
    "forward_return",
    "future_return",
    "entry_price",
    "exit_price",
    "trade_pnl",
    "profit_factor",
    "drawdown",
)
SHARE_INTEGRAL_TOLERANCE = Decimal("0.00000001")


class DevelopmentSimulationError(ValueError):
    """Raised when a registered input or execution invariant is violated."""


@dataclass(frozen=True, slots=True)
class Bar:
    trading_date: date
    code: str
    open_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None
    adjustment_factor: Decimal


@dataclass(frozen=True, slots=True)
class Candidate:
    signal_date: date
    entry_date: date
    scheduled_exit_date: date
    code: str
    selection_rank: int
    median_turnover_jpy: Decimal


@dataclass(slots=True)
class Position:
    candidate: Candidate
    entry_price: Decimal
    original_quantity: int
    quantity: int
    entry_notional: Decimal
    entry_cost: Decimal
    stop_price: Decimal
    last_mark: Decimal
    cumulative_adjustment_factor: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class SimulationParams:
    capital: Decimal
    lot_size: int
    maximum_positions: int
    maximum_new_positions_per_signal_date: int
    maximum_position_fraction: Decimal
    maximum_turnover_participation: Decimal
    catastrophic_stop_fraction: Decimal
    holding_sessions_including_entry: int
    cost_per_side: Decimal


@dataclass(frozen=True, slots=True)
class Trade:
    signal_date: str
    code: str
    selection_rank: int
    entry_date: str
    scheduled_exit_date: str
    exit_date: str
    exit_reason: str
    entry_price: str
    exit_price: str
    original_quantity: int
    exit_quantity: int
    cumulative_adjustment_factor: str
    entry_notional: str
    exit_notional: str
    entry_cost: str
    exit_cost: str
    gross_pnl: str
    net_pnl: str
    net_return_fraction: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    trading_date: str
    cash: str
    positions_value: str
    equity: str
    running_peak: str
    drawdown_jpy: str
    drawdown_fraction: str
    open_positions: int


@dataclass(frozen=True, slots=True)
class SimulationResult:
    metrics: dict[str, Any]
    skip_counts: dict[str, int]
    yearly_net_pnl_jpy: dict[str, str]
    trades: list[Trade]
    equity: list[EquityPoint]


def main() -> int:
    args = build_parser().parse_args()
    run_development(
        normalized_dir=args.normalized_dir,
        feature_dir=args.feature_dir,
        config_path=args.config,
        run_registration_path=args.run_registration,
        output_dir=args.output_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-dir", required=True, type=Path)
    parser.add_argument("--feature-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-registration", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def run_development(
    *,
    normalized_dir: Path,
    feature_dir: Path,
    config_path: Path,
    run_registration_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_and_validate_config(config_path)
    feature_manifest_path = feature_dir / FEATURE_MANIFEST_FILENAME
    normalized_manifest_path = normalized_dir / NORMALIZED_MANIFEST_FILENAME
    feature_manifest = verify_feature_artifact(
        feature_dir=feature_dir,
        feature_manifest_path=feature_manifest_path,
        config_path=config_path,
        normalized_manifest_path=normalized_manifest_path,
    )
    normalized_manifest = verify_normalized_bars(
        normalized_dir=normalized_dir,
        normalized_manifest_path=normalized_manifest_path,
        split_end=date.fromisoformat(config["splits"]["development"]["signal_date_end"]),
    )
    registration = verify_run_registration(
        path=run_registration_path,
        config_path=config_path,
        feature_manifest_path=feature_manifest_path,
        normalized_manifest_path=normalized_manifest_path,
        feature_path=feature_dir / FEATURE_FILENAME,
        output_dir=output_dir,
    )
    temporary_dir = output_dir.with_name(output_dir.name + ".tmp")
    ensure_new_output_paths(output_dir=output_dir, temporary_dir=temporary_dir)

    split_start = date.fromisoformat(config["splits"]["development"]["signal_date_start"])
    split_end = date.fromisoformat(config["splits"]["development"]["signal_date_end"])
    feature_frame = load_development_features(feature_dir / FEATURE_FILENAME)
    trading_dates, bars = load_development_bars(
        normalized_dir=normalized_dir,
        normalized_manifest=normalized_manifest,
        split_start=split_start,
        split_end=split_end,
        candidate_codes=set(feature_frame.get_column("code").to_list()),
    )
    candidates, incomplete_boundary_count = prepare_candidates(
        features=feature_frame,
        trading_dates=trading_dates,
        split_end=split_end,
        holding_sessions=int(config["execution"]["holding_sessions_including_entry"]),
    )
    base_params = params_from_config(config, stress=False)
    stress_params = params_from_config(config, stress=True)
    base = simulate_portfolio(
        candidates=candidates,
        trading_dates=trading_dates,
        bars=bars,
        params=base_params,
        split_start=split_start,
        split_end=split_end,
    )
    stress = simulate_portfolio(
        candidates=candidates,
        trading_dates=trading_dates,
        bars=bars,
        params=stress_params,
        split_start=split_start,
        split_end=split_end,
    )
    gates = evaluate_development_gates(
        base_metrics=base.metrics,
        stress_metrics=stress.metrics,
        gates=config["gates"]["development"],
    )
    decision = (
        "DEVELOPMENT_PASS_VALIDATION_REQUIRES_SEPARATE_REGISTRATION"
        if gates["all_passed"]
        else "DEVELOPMENT_FAIL_CANDIDATE_FROZEN_VALIDATION_PROHIBITED"
    )
    result = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "evidence_class": config["evidence_class"],
        "research_only": True,
        "paper_live_enabled": False,
        "counts_as_2026_09_30_kill_switch_evidence": False,
        "split": "development",
        "split_start": split_start.isoformat(),
        "split_end": split_end.isoformat(),
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "eligible_top20_candidate_rows": feature_frame.height,
        "boundary_incomplete_candidate_rows": incomplete_boundary_count,
        "executable_boundary_candidate_rows": len(candidates),
        "base": summary_without_rows(base),
        "stress": summary_without_rows(stress),
        "gates": gates,
        "decision": decision,
    }

    temporary_dir.mkdir(parents=True)
    write_json(temporary_dir / "development-result.json", result)
    write_dataclass_csv(temporary_dir / "development-trades-base.csv", base.trades)
    write_dataclass_csv(temporary_dir / "development-equity-base.csv", base.equity)
    write_dataclass_csv(temporary_dir / "development-trades-stress.csv", stress.trades)
    write_dataclass_csv(temporary_dir / "development-equity-stress.csv", stress.equity)
    manifest = build_result_manifest(
        temporary_dir=temporary_dir,
        registration_path=run_registration_path,
        registration=registration,
        feature_manifest=feature_manifest,
        result=result,
    )
    write_json(temporary_dir / "run-manifest.json", manifest)
    temporary_dir.rename(output_dir)
    return result


def load_and_validate_config(path: Path) -> dict[str, Any]:
    config = load_json_object(path, label="research config")
    if config.get("candidate_id") != CANDIDATE_ID or config.get("status") != CONFIG_STATUS:
        raise DevelopmentSimulationError("unexpected candidate or registration status")
    if config.get("evidence_class") != "PAPER_INSPIRED_NOT_REPLICATION":
        raise DevelopmentSimulationError("research evidence boundary drifted")
    execution = required_mapping(config, "execution")
    expected_execution = {
        "capital_jpy": 2_000_000,
        "lot_size": 100,
        "maximum_positions": 5,
        "maximum_position_fraction_of_starting_capital": 0.2,
        "maximum_median_turnover_participation": 0.01,
        "holding_sessions_including_entry": 20,
        "catastrophic_stop_fraction": 0.1,
        "same_day_exit_cash_reuse": False,
        "round_trip_cost_fraction": 0.00298,
        "stress_round_trip_cost_fraction": 0.005,
    }
    for key, expected in expected_execution.items():
        if execution.get(key) != expected:
            raise DevelopmentSimulationError(f"registered execution parameter drifted: {key}")
    selection = required_mapping(config, "selection")
    if selection.get("maximum_new_positions_per_signal_date") != 5:
        raise DevelopmentSimulationError("maximum new positions drifted")
    if selection.get("backfill_after_non_executable_candidate") is not True:
        raise DevelopmentSimulationError("candidate backfill must remain enabled")
    if selection.get("same_symbol_overlap") is not False:
        raise DevelopmentSimulationError("same-symbol overlap must remain disabled")
    decision = required_mapping(config, "decision_contract")
    if (
        decision.get("counts_as_2026_09_30_kill_switch_evidence") is not False
        or decision.get("paper_or_live_activation_authorized") is not False
    ):
        raise DevelopmentSimulationError("research-only decision boundary is open")
    return config


def verify_feature_artifact(
    *,
    feature_dir: Path,
    feature_manifest_path: Path,
    config_path: Path,
    normalized_manifest_path: Path,
) -> dict[str, Any]:
    manifest = load_json_object(feature_manifest_path, label="feature manifest")
    if manifest.get("output_schema_version") != FEATURE_SCHEMA_VERSION:
        raise DevelopmentSimulationError("unexpected feature schema")
    if manifest.get("candidate_id") != CANDIDATE_ID:
        raise DevelopmentSimulationError("feature candidate mismatch")
    if (
        manifest.get("research_only") is not True
        or manifest.get("paper_live_enabled") is not False
        or manifest.get("forward_returns_computed") is not False
        or manifest.get("outcomes_computed") is not False
        or manifest.get("locked_oos_outcomes_inspected") is not False
    ):
        raise DevelopmentSimulationError("feature artifact is not outcome-blind")
    inputs = required_mapping(manifest, "inputs")
    if inputs.get("config_sha256") != file_sha256(config_path):
        raise DevelopmentSimulationError("feature/config hash mismatch")
    if inputs.get("normalized_manifest_sha256") != file_sha256(normalized_manifest_path):
        raise DevelopmentSimulationError("feature/normalized-manifest hash mismatch")
    feature_record = required_mapping(manifest, "feature_file")
    feature_path = feature_dir / str(feature_record.get("path"))
    if feature_path != feature_dir / FEATURE_FILENAME or not feature_path.is_file():
        raise DevelopmentSimulationError("registered feature file missing")
    if feature_record.get("sha256") != file_sha256(feature_path):
        raise DevelopmentSimulationError("feature cohort hash mismatch")
    return manifest


def verify_normalized_bars(
    *,
    normalized_dir: Path,
    normalized_manifest_path: Path,
    split_end: date,
) -> dict[str, Any]:
    manifest = load_json_object(normalized_manifest_path, label="normalized manifest")
    if manifest.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
        raise DevelopmentSimulationError("unexpected normalized schema")
    if (
        manifest.get("research_only") is not True
        or manifest.get("paper_live_enabled") is not False
        or manifest.get("factor_or_outcome_computed") is not False
    ):
        raise DevelopmentSimulationError("normalized archive boundary is open")
    datasets = required_mapping(manifest, "datasets")
    bars = required_mapping(datasets, "equities_bars_daily")
    partitions = bars.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise DevelopmentSimulationError("normalized bar partitions missing")
    for record in partitions:
        if not isinstance(record, dict):
            raise DevelopmentSimulationError("invalid normalized partition record")
        if date.fromisoformat(str(record["first_date"])) > split_end:
            continue
        partition_path = normalized_dir / str(record["path"])
        if record.get("sha256") != file_sha256(partition_path):
            raise DevelopmentSimulationError(
                f"normalized partition hash mismatch: {partition_path}"
            )
    return manifest


def verify_run_registration(
    *,
    path: Path,
    config_path: Path,
    feature_manifest_path: Path,
    normalized_manifest_path: Path,
    feature_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    registration = load_json_object(path, label="run registration")
    if registration.get("registration_status") != RUN_REGISTRATION_STATUS:
        raise DevelopmentSimulationError("development run is not preregistered")
    if registration.get("candidate_id") != CANDIDATE_ID:
        raise DevelopmentSimulationError("run-registration candidate mismatch")
    if registration.get("authorized_split") != "development":
        raise DevelopmentSimulationError("only development is authorized")
    if registration.get("authorized_run_count") != 1:
        raise DevelopmentSimulationError("registration must authorize exactly one run")
    if registration.get("validation_outcomes_authorized") is not False:
        raise DevelopmentSimulationError("validation outcomes must remain prohibited")
    if registration.get("locked_oos_outcomes_authorized") is not False:
        raise DevelopmentSimulationError("locked OOS outcomes must remain prohibited")
    expected_output = registration.get("expected_output_dir")
    if not isinstance(expected_output, str) or (
        output_dir.resolve() != Path(expected_output).resolve()
    ):
        raise DevelopmentSimulationError("output directory does not match run registration")
    hashes = required_mapping(registration, "sha256")
    expected_hashes = {
        "config": file_sha256(config_path),
        "feature_manifest": file_sha256(feature_manifest_path),
        "feature_cohort": file_sha256(feature_path),
        "normalized_manifest": file_sha256(normalized_manifest_path),
        "simulator": file_sha256(Path(__file__).resolve()),
    }
    for key, actual in expected_hashes.items():
        if hashes.get(key) != actual:
            raise DevelopmentSimulationError(f"run-registration hash mismatch: {key}")
    return registration


def load_development_features(path: Path) -> pl.DataFrame:
    schema_names = pl.read_parquet_schema(path).names()
    forbidden = [
        column
        for column in schema_names
        if any(part in column.lower() for part in FORBIDDEN_FEATURE_COLUMN_PARTS)
    ]
    if forbidden:
        raise DevelopmentSimulationError(f"feature cohort has outcome-like columns: {forbidden}")
    required = {
        "signal_date",
        "code",
        "current_20_median_turnover_jpy",
        "selection_rank",
        "top20_candidate",
        "research_split",
    }
    missing = required - set(schema_names)
    if missing:
        raise DevelopmentSimulationError(f"feature columns missing: {sorted(missing)}")
    frame = (
        pl.scan_parquet(path)
        .filter(
            (pl.col("research_split") == "development") & pl.col("top20_candidate").fill_null(False)
        )
        .select(
            "signal_date",
            "code",
            "current_20_median_turnover_jpy",
            "selection_rank",
        )
        .collect()
        .sort("signal_date", "selection_rank", "code")
    )
    if frame.is_empty():
        raise DevelopmentSimulationError("development candidate pool is empty")
    if frame.select(pl.struct(pl.all()).is_duplicated().any()).item():
        raise DevelopmentSimulationError("duplicate development candidate rows")
    return frame


def load_development_bars(
    *,
    normalized_dir: Path,
    normalized_manifest: Mapping[str, Any],
    split_start: date,
    split_end: date,
    candidate_codes: set[str],
) -> tuple[list[date], dict[tuple[date, str], Bar]]:
    datasets = required_mapping(normalized_manifest, "datasets")
    dataset = required_mapping(datasets, "equities_bars_daily")
    partition_records = dataset["partitions"]
    paths = [
        normalized_dir / str(record["path"])
        for record in partition_records
        if date.fromisoformat(str(record["first_date"])) <= split_end
        and date.fromisoformat(str(record["last_date"])) >= split_start
    ]
    raw = (
        pl.scan_parquet(paths)
        .filter(pl.col("date").is_between(split_start, split_end, closed="both"))
        .select("date", "code", "open", "low", "close", "adjustment_factor")
        .collect()
    )
    trading_dates = sorted(raw.get_column("date").unique().to_list())
    selected = raw.filter(pl.col("code").is_in(candidate_codes))
    if selected.select(pl.struct("date", "code").is_duplicated().any()).item():
        raise DevelopmentSimulationError("duplicate normalized date/code bars")
    bars: dict[tuple[date, str], Bar] = {}
    for row in selected.iter_rows(named=True):
        current_date = row["date"]
        code = str(row["code"])
        bars[(current_date, code)] = Bar(
            trading_date=current_date,
            code=code,
            open_price=optional_positive_decimal(row["open"]),
            low_price=optional_positive_decimal(row["low"]),
            close_price=optional_positive_decimal(row["close"]),
            adjustment_factor=positive_decimal_or_error(
                row["adjustment_factor"],
                label=f"adjustment factor {current_date}/{code}",
            ),
        )
    return trading_dates, bars


def prepare_candidates(
    *,
    features: pl.DataFrame,
    trading_dates: Sequence[date],
    split_end: date,
    holding_sessions: int,
) -> tuple[list[Candidate], int]:
    if holding_sessions < 1:
        raise DevelopmentSimulationError("holding sessions must be positive")
    date_index = {current_date: index for index, current_date in enumerate(trading_dates)}
    candidates: list[Candidate] = []
    incomplete = 0
    for row in features.iter_rows(named=True):
        signal_date = row["signal_date"]
        signal_index = date_index.get(signal_date)
        if signal_index is None or signal_index + holding_sessions >= len(trading_dates):
            incomplete += 1
            continue
        entry_date = trading_dates[signal_index + 1]
        exit_date = trading_dates[signal_index + holding_sessions]
        if exit_date > split_end:
            incomplete += 1
            continue
        rank = row["selection_rank"]
        if rank is None:
            raise DevelopmentSimulationError("selected candidate is missing rank")
        candidates.append(
            Candidate(
                signal_date=signal_date,
                entry_date=entry_date,
                scheduled_exit_date=exit_date,
                code=str(row["code"]),
                selection_rank=int(rank),
                median_turnover_jpy=positive_decimal_or_error(
                    row["current_20_median_turnover_jpy"],
                    label=f"median turnover {signal_date}/{row['code']}",
                ),
            )
        )
    candidates.sort(key=lambda item: (item.entry_date, item.selection_rank, item.code))
    return candidates, incomplete


def params_from_config(config: Mapping[str, Any], *, stress: bool) -> SimulationParams:
    execution = required_mapping(config, "execution")
    selection = required_mapping(config, "selection")
    round_trip_key = "stress_round_trip_cost_fraction" if stress else "round_trip_cost_fraction"
    return SimulationParams(
        capital=Decimal(str(execution["capital_jpy"])),
        lot_size=int(execution["lot_size"]),
        maximum_positions=int(execution["maximum_positions"]),
        maximum_new_positions_per_signal_date=int(
            selection["maximum_new_positions_per_signal_date"]
        ),
        maximum_position_fraction=Decimal(
            str(execution["maximum_position_fraction_of_starting_capital"])
        ),
        maximum_turnover_participation=Decimal(
            str(execution["maximum_median_turnover_participation"])
        ),
        catastrophic_stop_fraction=Decimal(str(execution["catastrophic_stop_fraction"])),
        holding_sessions_including_entry=int(execution["holding_sessions_including_entry"]),
        cost_per_side=Decimal(str(execution[round_trip_key])) / Decimal("2"),
    )


def simulate_portfolio(
    *,
    candidates: Sequence[Candidate],
    trading_dates: Sequence[date],
    bars: Mapping[tuple[date, str], Bar],
    params: SimulationParams,
    split_start: date,
    split_end: date,
) -> SimulationResult:
    validate_params(params)
    candidates_by_entry: dict[date, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_entry[candidate.entry_date].append(candidate)

    cash = params.capital
    peak = params.capital
    maximum_drawdown = Decimal("0")
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_points: list[EquityPoint] = []
    skip_counts: dict[str, int] = defaultdict(int)

    for current_date in trading_dates:
        if current_date < split_start or current_date > split_end:
            continue

        opened_today = 0
        entry_candidates = candidates_by_entry.get(current_date, [])
        for candidate_index, candidate in enumerate(entry_candidates):
            if opened_today >= params.maximum_new_positions_per_signal_date:
                skip_counts["new_position_cap"] += len(entry_candidates) - candidate_index
                break
            if len(positions) >= params.maximum_positions:
                skip_counts["position_cap"] += len(entry_candidates) - candidate_index
                break
            if candidate.code in positions:
                skip_counts["same_symbol_overlap"] += 1
                continue
            bar = bars.get((current_date, candidate.code))
            if bar is None or bar.open_price is None:
                skip_counts["missing_entry_open"] += 1
                continue
            quantity = size_quantity(
                entry_price=bar.open_price,
                median_turnover_jpy=candidate.median_turnover_jpy,
                cash=cash,
                params=params,
            )
            if quantity == 0:
                skip_counts["zero_lot"] += 1
                continue
            entry_notional = bar.open_price * Decimal(quantity)
            entry_cost = entry_notional * params.cost_per_side
            required_cash = entry_notional + entry_cost
            if required_cash > cash:
                raise DevelopmentSimulationError("sizing exceeded available cash")
            cash -= required_cash
            positions[candidate.code] = Position(
                candidate=candidate,
                entry_price=bar.open_price,
                original_quantity=quantity,
                quantity=quantity,
                entry_notional=entry_notional,
                entry_cost=entry_cost,
                stop_price=bar.open_price * (Decimal("1") - params.catastrophic_stop_fraction),
                last_mark=bar.open_price,
            )
            opened_today += 1

        for code in list(positions):
            position = positions[code]
            bar = bars.get((current_date, code))
            if current_date > position.candidate.entry_date and bar is not None:
                apply_corporate_action(position, bar.adjustment_factor)

            exit_price: Decimal | None = None
            exit_reason: str | None = None
            if bar is not None and bar.open_price is not None and bar.low_price is not None:
                if bar.open_price <= position.stop_price:
                    exit_price = bar.open_price
                    exit_reason = "GAP_STOP"
                elif bar.low_price <= position.stop_price:
                    exit_price = position.stop_price
                    exit_reason = "INTRADAY_STOP"

            if exit_price is None and current_date == position.candidate.scheduled_exit_date:
                if bar is None or bar.close_price is None:
                    raise DevelopmentSimulationError(
                        f"scheduled exit close missing: {current_date}/{code}"
                    )
                exit_price = bar.close_price
                exit_reason = "SCHEDULED_CLOSE"

            if exit_price is not None and exit_reason is not None:
                trade, proceeds = close_position(
                    position=position,
                    exit_date=current_date,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    cost_per_side=params.cost_per_side,
                )
                cash += proceeds
                trades.append(trade)
                del positions[code]
            elif bar is not None and bar.close_price is not None:
                position.last_mark = bar.close_price

        positions_value = sum(
            (position.last_mark * Decimal(position.quantity) for position in positions.values()),
            start=Decimal("0"),
        )
        equity = cash + positions_value
        peak = max(peak, equity)
        drawdown = peak - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        equity_points.append(
            EquityPoint(
                trading_date=current_date.isoformat(),
                cash=decimal_text(cash),
                positions_value=decimal_text(positions_value),
                equity=decimal_text(equity),
                running_peak=decimal_text(peak),
                drawdown_jpy=decimal_text(drawdown),
                drawdown_fraction=decimal_text(drawdown / peak if peak else Decimal("0")),
                open_positions=len(positions),
            )
        )

    if positions:
        raise DevelopmentSimulationError("positions remain open after development boundary")
    yearly_pnl: dict[int, Decimal] = {
        year: Decimal("0") for year in range(split_start.year, split_end.year + 1)
    }
    net_pnls = [Decimal(trade.net_pnl) for trade in trades]
    for trade in trades:
        yearly_pnl[date.fromisoformat(trade.exit_date).year] += Decimal(trade.net_pnl)
    positive_year_fraction = Decimal(sum(value > 0 for value in yearly_pnl.values())) / Decimal(
        len(yearly_pnl)
    )
    profit_factor, profit_factor_state = calculate_profit_factor(net_pnls)
    ending_equity = cash
    metrics = {
        "opened_trades": len(trades),
        "net_pnl_jpy": decimal_text(sum(net_pnls, start=Decimal("0"))),
        "ending_equity_jpy": decimal_text(ending_equity),
        "profit_factor": decimal_text(profit_factor) if profit_factor is not None else None,
        "profit_factor_state": profit_factor_state,
        "maximum_drawdown_jpy": decimal_text(maximum_drawdown),
        "maximum_drawdown_fraction": decimal_text(maximum_drawdown / params.capital),
        "positive_calendar_year_fraction": decimal_text(positive_year_fraction),
        "positive_calendar_years": sum(value > 0 for value in yearly_pnl.values()),
        "calendar_year_count": len(yearly_pnl),
        "cost_per_side_fraction": decimal_text(params.cost_per_side),
    }
    return SimulationResult(
        metrics=metrics,
        skip_counts=dict(sorted(skip_counts.items())),
        yearly_net_pnl_jpy={str(year): decimal_text(value) for year, value in yearly_pnl.items()},
        trades=trades,
        equity=equity_points,
    )


def size_quantity(
    *,
    entry_price: Decimal,
    median_turnover_jpy: Decimal,
    cash: Decimal,
    params: SimulationParams,
) -> int:
    if entry_price <= 0 or median_turnover_jpy <= 0 or cash <= 0:
        return 0
    notional_cap = min(
        params.capital * params.maximum_position_fraction,
        median_turnover_jpy * params.maximum_turnover_participation,
        cash / (Decimal("1") + params.cost_per_side),
    )
    lots = (notional_cap / entry_price / Decimal(params.lot_size)).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return int(lots) * params.lot_size


def apply_corporate_action(position: Position, adjustment_factor: Decimal) -> None:
    if adjustment_factor <= 0:
        raise DevelopmentSimulationError("nonpositive corporate-action factor")
    if adjustment_factor == 1:
        return
    adjusted_quantity = Decimal(position.quantity) / adjustment_factor
    integral_quantity = adjusted_quantity.to_integral_value()
    if abs(adjusted_quantity - integral_quantity) > SHARE_INTEGRAL_TOLERANCE:
        raise DevelopmentSimulationError(
            f"corporate action produced fractional shares: {position.candidate.code}"
        )
    position.quantity = int(integral_quantity)
    position.stop_price *= adjustment_factor
    position.last_mark *= adjustment_factor
    position.cumulative_adjustment_factor *= adjustment_factor


def close_position(
    *,
    position: Position,
    exit_date: date,
    exit_price: Decimal,
    exit_reason: str,
    cost_per_side: Decimal,
) -> tuple[Trade, Decimal]:
    exit_notional = exit_price * Decimal(position.quantity)
    exit_cost = exit_notional * cost_per_side
    gross_pnl = exit_notional - position.entry_notional
    net_pnl = gross_pnl - position.entry_cost - exit_cost
    trade = Trade(
        signal_date=position.candidate.signal_date.isoformat(),
        code=position.candidate.code,
        selection_rank=position.candidate.selection_rank,
        entry_date=position.candidate.entry_date.isoformat(),
        scheduled_exit_date=position.candidate.scheduled_exit_date.isoformat(),
        exit_date=exit_date.isoformat(),
        exit_reason=exit_reason,
        entry_price=decimal_text(position.entry_price),
        exit_price=decimal_text(exit_price),
        original_quantity=position.original_quantity,
        exit_quantity=position.quantity,
        cumulative_adjustment_factor=decimal_text(position.cumulative_adjustment_factor),
        entry_notional=decimal_text(position.entry_notional),
        exit_notional=decimal_text(exit_notional),
        entry_cost=decimal_text(position.entry_cost),
        exit_cost=decimal_text(exit_cost),
        gross_pnl=decimal_text(gross_pnl),
        net_pnl=decimal_text(net_pnl),
        net_return_fraction=decimal_text(net_pnl / position.entry_notional),
    )
    return trade, exit_notional - exit_cost


def calculate_profit_factor(values: Sequence[Decimal]) -> tuple[Decimal | None, str]:
    positive = sum((value for value in values if value > 0), start=Decimal("0"))
    negative = abs(sum((value for value in values if value < 0), start=Decimal("0")))
    if negative == 0:
        if positive > 0:
            return None, "POSITIVE_WITHOUT_LOSSES"
        return None, "UNDEFINED_NO_LOSSES_OR_PROFITS"
    return positive / negative, "FINITE"


def evaluate_development_gates(
    *,
    base_metrics: Mapping[str, Any],
    stress_metrics: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    checks = {
        "minimum_opened_trades": int(base_metrics["opened_trades"])
        >= int(gates["minimum_opened_trades"]),
        "minimum_profit_factor_exclusive": profit_factor_exceeds(
            base_metrics,
            Decimal(str(gates["minimum_profit_factor_exclusive"])),
        ),
        "maximum_drawdown_fraction_exclusive": Decimal(
            str(base_metrics["maximum_drawdown_fraction"])
        )
        < Decimal(str(gates["maximum_drawdown_fraction_exclusive"])),
        "minimum_stress_profit_factor_exclusive": profit_factor_exceeds(
            stress_metrics,
            Decimal(str(gates["minimum_stress_profit_factor_exclusive"])),
        ),
        "minimum_positive_calendar_year_fraction": Decimal(
            str(base_metrics["positive_calendar_year_fraction"])
        )
        >= Decimal(str(gates["minimum_positive_calendar_year_fraction"])),
    }
    return {
        "thresholds": dict(gates),
        "checks": checks,
        "all_passed": all(checks.values()),
    }


def profit_factor_exceeds(metrics: Mapping[str, Any], threshold: Decimal) -> bool:
    state = metrics.get("profit_factor_state")
    if state == "POSITIVE_WITHOUT_LOSSES":
        return True
    value = metrics.get("profit_factor")
    return value is not None and Decimal(str(value)) > threshold


def validate_params(params: SimulationParams) -> None:
    if params.capital <= 0:
        raise DevelopmentSimulationError("capital must be positive")
    if params.lot_size < 1 or params.maximum_positions < 1:
        raise DevelopmentSimulationError("lot and position limits must be positive")
    if params.maximum_new_positions_per_signal_date < 1:
        raise DevelopmentSimulationError("new-position limit must be positive")
    if not Decimal("0") <= params.catastrophic_stop_fraction < Decimal("1"):
        raise DevelopmentSimulationError("invalid stop fraction")
    if params.cost_per_side < 0:
        raise DevelopmentSimulationError("cost cannot be negative")


def summary_without_rows(result: SimulationResult) -> dict[str, Any]:
    return {
        "metrics": result.metrics,
        "skip_counts": result.skip_counts,
        "yearly_net_pnl_jpy": result.yearly_net_pnl_jpy,
    }


def build_result_manifest(
    *,
    temporary_dir: Path,
    registration_path: Path,
    registration: Mapping[str, Any],
    feature_manifest: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    filenames = (
        "development-result.json",
        "development-trades-base.csv",
        "development-equity-base.csv",
        "development-trades-stress.csv",
        "development-equity-stress.csv",
    )
    return {
        "manifest_version": 1,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "candidate_id": CANDIDATE_ID,
        "research_only": True,
        "paper_live_enabled": False,
        "split": "development",
        "validation_outcomes_inspected": False,
        "locked_oos_outcomes_inspected": False,
        "run_registration": {
            "path": repository_relative_or_absolute(registration_path),
            "sha256": file_sha256(registration_path),
            "registration_status": registration["registration_status"],
        },
        "feature_cohort_sha256": feature_manifest["feature_file"]["sha256"],
        "decision": result["decision"],
        "files": [
            {
                "path": filename,
                "byte_size": (temporary_dir / filename).stat().st_size,
                "sha256": file_sha256(temporary_dir / filename),
            }
            for filename in filenames
        ],
    }


def ensure_new_output_paths(*, output_dir: Path, temporary_dir: Path) -> None:
    for path in (output_dir, temporary_dir):
        if path.exists():
            raise FileExistsError(f"output path already exists; refusing rerun: {path}")


def write_dataclass_csv(path: Path, rows: Sequence[Any]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    records = [asdict(row) for row in rows]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentSimulationError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DevelopmentSimulationError(f"{label} must be a JSON object")
    return value


def required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise DevelopmentSimulationError(f"missing object: {key}")
    return child


def optional_positive_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        return None
    return result


def positive_decimal_or_error(value: Any, *, label: str) -> Decimal:
    result = optional_positive_decimal(value)
    if result is None:
        raise DevelopmentSimulationError(f"missing or nonpositive {label}")
    return result


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise DevelopmentSimulationError("non-finite decimal result")
    return format(value, "f")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def repository_relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
