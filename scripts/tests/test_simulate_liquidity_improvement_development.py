from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "simulate-liquidity-improvement-development.py"
    spec = importlib.util.spec_from_file_location(
        "simulate_liquidity_improvement_development",
        path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sim = _load_module()
D = Decimal


def _dates(count: int = 25, *, start: date = date(2022, 1, 4)) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def _params(
    *,
    capital: str = "2000000",
    max_positions: int = 5,
    max_new: int = 5,
    cost: str = "0.00149",
) -> object:
    return sim.SimulationParams(
        capital=D(capital),
        lot_size=100,
        maximum_positions=max_positions,
        maximum_new_positions_per_signal_date=max_new,
        maximum_position_fraction=D("0.2"),
        maximum_turnover_participation=D("0.01"),
        catastrophic_stop_fraction=D("0.1"),
        holding_sessions_including_entry=20,
        cost_per_side=D(cost),
    )


def _candidate(
    dates: list[date],
    *,
    code: str = "10000",
    entry_index: int = 0,
    exit_index: int = 19,
    rank: int = 1,
    turnover: str = "100000000",
) -> object:
    return sim.Candidate(
        signal_date=dates[max(0, entry_index - 1)],
        entry_date=dates[entry_index],
        scheduled_exit_date=dates[exit_index],
        code=code,
        selection_rank=rank,
        median_turnover_jpy=D(turnover),
    )


def _bars(
    dates: list[date],
    codes: tuple[str, ...] = ("10000",),
    *,
    price: str = "1000",
) -> dict[tuple[date, str], object]:
    result = {}
    for current_date in dates:
        for code in codes:
            result[(current_date, code)] = sim.Bar(
                trading_date=current_date,
                code=code,
                open_price=D(price),
                low_price=D(price) * D("0.95"),
                close_price=D(price),
                adjustment_factor=D("1"),
            )
    return result


def _run(
    dates: list[date],
    candidates: list[object],
    bars: dict[tuple[date, str], object],
    *,
    params: object | None = None,
):
    return sim.simulate_portfolio(
        candidates=candidates,
        trading_dates=dates,
        bars=bars,
        params=params or _params(),
        split_start=dates[0],
        split_end=dates[-1],
    )


def test_size_uses_lot_turnover_and_starting_capital_caps() -> None:
    params = _params()
    assert (
        sim.size_quantity(
            entry_price=D("1000"),
            median_turnover_jpy=D("10000000"),
            cash=D("2000000"),
            params=params,
        )
        == 100
    )
    assert (
        sim.size_quantity(
            entry_price=D("1000"),
            median_turnover_jpy=D("1000000000"),
            cash=D("2000000"),
            params=params,
        )
        == 400
    )


def test_prepare_candidates_counts_holding_session_including_entry() -> None:
    dates = _dates(22)
    features = pl.DataFrame(
        {
            "signal_date": [dates[0]],
            "code": ["10000"],
            "current_20_median_turnover_jpy": [100_000_000.0],
            "selection_rank": [1],
        }
    )
    candidates, incomplete = sim.prepare_candidates(
        features=features,
        trading_dates=dates,
        split_end=dates[-1],
        holding_sessions=20,
    )
    assert incomplete == 0
    assert candidates[0].entry_date == dates[1]
    assert candidates[0].scheduled_exit_date == dates[20]


def test_missing_entry_open_backfills_next_rank() -> None:
    dates = _dates()
    bars = _bars(dates, ("10000", "10010"))
    bars[(dates[0], "10000")] = sim.Bar(dates[0], "10000", None, None, None, D("1"))
    result = _run(
        dates,
        [
            _candidate(dates, code="10000", rank=1),
            _candidate(dates, code="10010", rank=2),
        ],
        bars,
        params=_params(max_positions=1, max_new=1),
    )
    assert [trade.code for trade in result.trades] == ["10010"]
    assert result.skip_counts["missing_entry_open"] == 1


def test_gap_and_intraday_stops_use_registered_fill_order() -> None:
    dates = _dates()
    gap_bars = _bars(dates)
    gap_bars[(dates[1], "10000")] = sim.Bar(dates[1], "10000", D("850"), D("840"), D("850"), D("1"))
    gap = _run(dates, [_candidate(dates)], gap_bars)
    assert gap.trades[0].exit_reason == "GAP_STOP"
    assert gap.trades[0].exit_price == "850"

    intraday_bars = _bars(dates)
    intraday_bars[(dates[1], "10000")] = sim.Bar(
        dates[1], "10000", D("950"), D("890"), D("930"), D("1")
    )
    intraday = _run(dates, [_candidate(dates)], intraday_bars)
    assert intraday.trades[0].exit_reason == "INTRADAY_STOP"
    assert intraday.trades[0].exit_price == "900.0"


def test_entry_day_stop_is_evaluated() -> None:
    dates = _dates()
    bars = _bars(dates)
    bars[(dates[0], "10000")] = sim.Bar(dates[0], "10000", D("1000"), D("850"), D("900"), D("1"))
    result = _run(dates, [_candidate(dates)], bars)
    assert result.trades[0].exit_date == dates[0].isoformat()
    assert result.trades[0].exit_reason == "INTRADAY_STOP"


def test_split_adjusts_shares_stop_and_carried_mark_without_changing_value() -> None:
    dates = _dates()
    candidate = _candidate(dates)
    position = sim.Position(
        candidate=candidate,
        entry_price=D("1000"),
        original_quantity=400,
        quantity=400,
        entry_notional=D("400000"),
        entry_cost=D("596"),
        stop_price=D("900"),
        last_mark=D("1100"),
    )
    sim.apply_corporate_action(position, D("0.5"))
    assert position.quantity == 800
    assert position.stop_price == D("450.0")
    assert position.last_mark == D("550.0")
    assert position.quantity * position.last_mark == D("440000")


def test_repeating_decimal_split_factor_is_tolerated_as_source_float_noise() -> None:
    dates = _dates()
    candidate = _candidate(dates)
    position = sim.Position(
        candidate, D("1000"), 100, 100, D("100000"), D("149"), D("900"), D("1000")
    )
    sim.apply_corporate_action(position, D("0.3333333333333333"))
    assert position.quantity == 300


def test_fractional_share_corporate_action_fails_closed() -> None:
    dates = _dates()
    candidate = _candidate(dates)
    position = sim.Position(
        candidate, D("1000"), 100, 100, D("100000"), D("149"), D("900"), D("1000")
    )
    with pytest.raises(sim.DevelopmentSimulationError, match="fractional shares"):
        sim.apply_corporate_action(position, D("3"))


def test_missing_interim_close_carries_split_adjusted_mark() -> None:
    dates = _dates()
    bars = _bars(dates)
    bars[(dates[1], "10000")] = sim.Bar(dates[1], "10000", None, None, None, D("0.5"))
    for index in range(2, len(dates)):
        bars[(dates[index], "10000")] = sim.Bar(
            dates[index], "10000", D("500"), D("490"), D("500"), D("1")
        )
    result = _run(dates, [_candidate(dates)], bars)
    entry_equity = D(result.equity[0].equity)
    missing_close_equity = D(result.equity[1].equity)
    assert missing_close_equity == entry_equity
    assert result.trades[0].exit_quantity == 800


def test_missing_scheduled_close_fails_closed() -> None:
    dates = _dates()
    bars = _bars(dates)
    bars[(dates[19], "10000")] = sim.Bar(dates[19], "10000", D("1000"), D("950"), None, D("1"))
    with pytest.raises(sim.DevelopmentSimulationError, match="scheduled exit close missing"):
        _run(dates, [_candidate(dates)], bars)


def test_same_day_exit_cash_and_position_slot_are_not_reused_at_open() -> None:
    dates = _dates(40)
    bars = _bars(dates, ("10000", "10010"))
    first = _candidate(dates, code="10000", entry_index=0, exit_index=19)
    second = _candidate(dates, code="10010", entry_index=19, exit_index=38)
    result = _run(
        dates,
        [first, second],
        bars,
        params=_params(max_positions=1, max_new=1),
    )
    assert [trade.code for trade in result.trades] == ["10000"]
    assert result.skip_counts["position_cap"] == 1


def test_same_symbol_overlap_is_rejected_and_next_rank_backfills() -> None:
    dates = _dates(40)
    bars = _bars(dates, ("10000", "10010"))
    candidates = [
        _candidate(dates, code="10000", entry_index=0, exit_index=25),
        _candidate(dates, code="10000", entry_index=19, exit_index=38, rank=1),
        _candidate(dates, code="10010", entry_index=19, exit_index=38, rank=2),
    ]
    result = _run(dates, candidates, bars, params=_params(max_positions=2, max_new=1))
    assert [trade.code for trade in result.trades] == ["10000", "10010"]
    assert result.skip_counts["same_symbol_overlap"] == 1


def test_daily_mark_to_market_drawdown_includes_open_loss_and_costs() -> None:
    dates = _dates()
    bars = _bars(dates)
    bars[(dates[0], "10000")] = sim.Bar(dates[0], "10000", D("1000"), D("910"), D("910"), D("1"))
    result = _run(dates, [_candidate(dates)], bars)
    assert D(result.metrics["maximum_drawdown_jpy"]) > D("36000")
    assert D(result.trades[0].net_pnl) < 0


def test_stress_cost_reduces_net_pnl() -> None:
    dates = _dates()
    bars = _bars(dates)
    base = _run(dates, [_candidate(dates)], bars, params=_params(cost="0.00149"))
    stress = _run(dates, [_candidate(dates)], bars, params=_params(cost="0.0025"))
    assert D(stress.metrics["net_pnl_jpy"]) < D(base.metrics["net_pnl_jpy"])


def test_gate_comparisons_follow_strict_and_inclusive_registration() -> None:
    base = {
        "opened_trades": 100,
        "profit_factor": "1.2",
        "profit_factor_state": "FINITE",
        "maximum_drawdown_fraction": "0.099",
        "positive_calendar_year_fraction": "0.75",
    }
    stress = {"profit_factor": "1.01", "profit_factor_state": "FINITE"}
    thresholds = {
        "minimum_opened_trades": 100,
        "minimum_profit_factor_exclusive": 1.2,
        "maximum_drawdown_fraction_exclusive": 0.1,
        "minimum_stress_profit_factor_exclusive": 1.0,
        "minimum_positive_calendar_year_fraction": 0.75,
    }
    result = sim.evaluate_development_gates(
        base_metrics=base,
        stress_metrics=stress,
        gates=thresholds,
    )
    assert result["checks"]["minimum_opened_trades"] is True
    assert result["checks"]["minimum_profit_factor_exclusive"] is False
    assert result["checks"]["minimum_positive_calendar_year_fraction"] is True
    assert result["all_passed"] is False


def test_outcome_like_feature_column_is_rejected_before_split_read(tmp_path: Path) -> None:
    path = tmp_path / "features.parquet"
    pl.DataFrame(
        {
            "signal_date": [date(2022, 1, 1)],
            "code": ["10000"],
            "current_20_median_turnover_jpy": [100_000_000.0],
            "selection_rank": [1],
            "top20_candidate": [True],
            "research_split": ["development"],
            "forward_return_20d": [0.1],
        }
    ).write_parquet(path)
    with pytest.raises(sim.DevelopmentSimulationError, match="outcome-like"):
        sim.load_development_features(path)


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "result"
    output.mkdir()
    sentinel = output / "owned.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing rerun"):
        sim.ensure_new_output_paths(
            output_dir=output,
            temporary_dir=tmp_path / "result.tmp",
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"
