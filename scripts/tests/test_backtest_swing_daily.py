from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "backtest-swing-daily.py"
    spec = importlib.util.spec_from_file_location("backtest_swing_daily", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


swing = _load_module()


def _bar(**overrides):
    defaults = {
        "symbol": "7203",
        "date": date(2026, 1, 30),
        "open": 1020.0,
        "high": 1080.0,
        "low": 990.0,
        "close": 1030.0,
        "volume": 1_000_000,
        "turnover": 1_030_000_000.0,
        "sma_short": 1000.0,
        "sma_long": 900.0,
        "sma_long_past": 850.0,
        "atr": 30.0,
        "avg_turnover": 900_000_000.0,
        "return_5d": 0.02,
        "return_20d": 0.08,
        "return_60d": 0.12,
        "touched_sma_short_recently": True,
    }
    defaults.update(overrides)
    return swing.PreparedBar(**defaults)


def _trade(**overrides):
    defaults = {
        "symbol": "7203",
        "signal_date": date(2026, 1, 4),
        "signal_close": 1000.0,
        "signal_sma_short": 980.0,
        "signal_atr_pct": 0.03,
        "signal_return_20d": 0.08,
        "signal_avg_turnover": 900_000_000.0,
        "entry_gap_pct": 0.01,
        "entry_date": date(2026, 1, 5),
        "exit_date": date(2026, 1, 10),
        "entry_price": 1010.0,
        "exit_price": 1100.0,
        "quantity": 100,
        "exit_reason": "target",
        "gross_pnl": 9_000.0,
        "costs": 300.0,
        "net_pnl": 8_700.0,
        "entry_score": 0.12,
        "ranked_position": 1,
        "candidate_count": 3,
    }
    defaults.update(overrides)
    return swing.Trade(**defaults)


def _candidate(**overrides):
    defaults = {
        "symbol": "7203",
        "signal_date": date(2026, 1, 30),
        "signal_close": 1000.0,
        "signal_sma_short": 980.0,
        "signal_atr_pct": 0.03,
        "signal_return_20d": 0.08,
        "signal_avg_turnover": 900_000_000.0,
        "entry_gap_pct": 0.0,
        "entry_date": date(2026, 1, 31),
        "entry_price": 1010.0,
        "stop_price": 965.0,
        "target_price": 1100.0,
        "quantity": 100,
        "score": 0.12,
        "ranked_position": 1,
        "candidate_count": 3,
    }
    defaults.update(overrides)
    return swing.EntryCandidate(**defaults)


def test_entry_signal_accepts_preregistered_trend_pullback() -> None:
    assert swing.is_entry_signal(_bar(), swing.SwingParams())


def test_entry_signal_rejects_overextended_close() -> None:
    bar = _bar(close=1100.0, atr=30.0)

    assert not swing.is_entry_signal(bar, swing.SwingParams())


def test_position_size_applies_risk_notional_and_lot_size() -> None:
    quantity = swing._position_size(
        entry_price=1000.0,
        stop_price=950.0,
        equity=1_000_000.0,
        params=swing.SwingParams(),
    )

    assert quantity == 200


def test_v1_candidate_uses_risk_throttle_without_entry_rule_changes() -> None:
    v0 = swing.params_for_candidate("daily_trend_pullback_v0", 1_000_000.0, 200_000_000.0)
    v1 = swing.params_for_candidate("daily_trend_pullback_v1", 1_000_000.0, 200_000_000.0)

    assert v1.sma_short_period == v0.sma_short_period
    assert v1.sma_long_period == v0.sma_long_period
    assert v1.stop_atr_multiple == v0.stop_atr_multiple
    assert v1.target_r_multiple == v0.target_r_multiple
    assert v1.risk_per_trade_pct == 0.0075
    assert v1.max_notional_per_position_pct == 0.15
    assert v1.max_positions == 4


def test_v2_candidate_uses_gap_confirmed_entry_without_sizing_change() -> None:
    v0 = swing.params_for_candidate("daily_trend_pullback_v0", 1_000_000.0, 200_000_000.0)
    v2 = swing.params_for_candidate("daily_trend_pullback_v2", 1_000_000.0, 200_000_000.0)

    assert v2.risk_per_trade_pct == v0.risk_per_trade_pct
    assert v2.max_notional_per_position_pct == v0.max_notional_per_position_pct
    assert v2.max_positions == v0.max_positions
    assert v2.min_entry_gap_pct == 0.0
    assert v2.max_entry_gap_pct == 0.01


def test_v3_candidate_adds_daily_cluster_limit_to_gap_confirmed_entry() -> None:
    v3 = swing.params_for_candidate("daily_trend_pullback_v3", 1_000_000.0, 200_000_000.0)

    assert v3.min_entry_gap_pct == 0.0
    assert v3.max_entry_gap_pct == 0.01
    assert v3.max_new_positions_per_day == 1


def test_v4_candidate_adds_weak_momentum_and_crowding_filters() -> None:
    v4 = swing.params_for_candidate("daily_trend_pullback_v4", 1_000_000.0, 200_000_000.0)

    assert v4.min_entry_gap_pct == 0.0
    assert v4.max_entry_gap_pct == 0.01
    assert v4.max_new_positions_per_day == 1
    assert v4.min_return_20d == 0.08
    assert v4.max_avg_turnover == 3_000_000_000.0


def test_v5_candidate_adds_market_return_guard_to_v4() -> None:
    v4 = swing.params_for_candidate("daily_trend_pullback_v4", 1_000_000.0, 200_000_000.0)
    v5 = swing.params_for_candidate("daily_trend_pullback_v5", 1_000_000.0, 200_000_000.0)

    assert v5.max_avg_turnover == v4.max_avg_turnover
    assert v5.min_return_20d == v4.min_return_20d
    assert v5.min_entry_gap_pct == v4.min_entry_gap_pct
    assert v5.max_entry_gap_pct == v4.max_entry_gap_pct
    assert v5.max_new_positions_per_day == v4.max_new_positions_per_day
    assert v5.blocked_market_positive_return_20d_min == 0.45
    assert v5.blocked_market_positive_return_20d_max == 0.55


def test_fixed10_candidate_preregisters_exit_only_hypothesis() -> None:
    v3 = swing.params_for_candidate("daily_trend_pullback_v3", 1_000_000.0, 200_000_000.0)
    fixed10 = swing.params_for_candidate(
        "daily_trend_pullback_exit_fixed10_v0",
        1_000_000.0,
        200_000_000.0,
    )

    assert fixed10.min_entry_gap_pct == v3.min_entry_gap_pct
    assert fixed10.max_entry_gap_pct == v3.max_entry_gap_pct
    assert fixed10.max_new_positions_per_day == v3.max_new_positions_per_day
    assert fixed10.max_hold_days == 10
    assert fixed10.exit_mode == "fixed_hold"
    assert swing.deterministic_selections_for_candidate("daily_trend_pullback_exit_fixed10_v0") == (
        "ranked",
        "rank_2_3_first",
    )


def test_fixed10_hash_candidate_preregisters_risk_scaled_basket() -> None:
    fixed10 = swing.params_for_candidate(
        "daily_trend_pullback_exit_fixed10_v0",
        1_000_000.0,
        200_000_000.0,
    )
    hashed = swing.params_for_candidate(
        "daily_trend_pullback_fixed10_hash_v0",
        1_000_000.0,
        200_000_000.0,
    )

    assert hashed.min_entry_gap_pct == fixed10.min_entry_gap_pct
    assert hashed.max_entry_gap_pct == fixed10.max_entry_gap_pct
    assert hashed.exit_mode == "fixed_hold"
    assert hashed.risk_per_trade_pct == 0.0035
    assert hashed.max_notional_per_position_pct == 0.08
    assert swing.deterministic_selections_for_candidate("daily_trend_pullback_fixed10_hash_v0") == (
        "stable_hash",
    )


def test_stable_hash_selection_is_deterministic_and_not_score_ordered() -> None:
    candidates = [
        _candidate(symbol="7203", score=0.99),
        _candidate(symbol="6758", score=0.01),
        _candidate(symbol="9984", score=0.50),
    ]

    first = swing.order_candidates(candidates, selection="stable_hash", rng=swing.random.Random(1))
    second = swing.order_candidates(
        list(reversed(candidates)),
        selection="stable_hash",
        rng=swing.random.Random(99),
    )

    assert [candidate.symbol for candidate in first] == [candidate.symbol for candidate in second]
    assert [candidate.symbol for candidate in first] != ["7203", "9984", "6758"]


def test_breakout_candidate_preregisters_new_entry_family() -> None:
    params = swing.params_for_candidate(
        "daily_breakout_continuation_v0",
        1_000_000.0,
        200_000_000.0,
    )

    assert params.entry_mode == "breakout_continuation"
    assert params.exit_mode == "target_stop_max_hold"
    assert params.min_return_20d == 0.08
    assert params.max_return_20d == 0.35
    assert params.min_return_60d == 0.10
    assert params.breakout_lookback == 60
    assert params.min_turnover_multiple == 1.20
    assert params.max_prior_range_20d_pct == 0.28
    assert params.min_entry_gap_pct == 0.0
    assert params.max_entry_gap_pct == 0.03
    assert params.max_new_positions_per_day == 1


def test_breakout_entry_signal_accepts_preregistered_continuation() -> None:
    params = swing.params_for_candidate(
        "daily_breakout_continuation_v0",
        1_000_000.0,
        200_000_000.0,
    )
    bar = _bar(
        close=1100.0,
        high=1110.0,
        sma_short=1000.0,
        sma_long=900.0,
        sma_long_past=850.0,
        atr=35.0,
        avg_turnover=800_000_000.0,
        turnover=1_200_000_000.0,
        return_20d=0.12,
        return_60d=0.20,
        touched_sma_short_recently=False,
        prior_high_breakout=1090.0,
        prior_range_20d_pct=0.18,
    )

    assert swing.is_entry_signal(bar, params)


def test_breakout_entry_signal_rejects_stale_high_and_weak_turnover() -> None:
    params = swing.params_for_candidate(
        "daily_breakout_continuation_v0",
        1_000_000.0,
        200_000_000.0,
    )
    stale_high = _bar(
        close=1080.0,
        sma_short=1000.0,
        sma_long=900.0,
        sma_long_past=850.0,
        atr=35.0,
        avg_turnover=800_000_000.0,
        turnover=1_200_000_000.0,
        return_20d=0.12,
        return_60d=0.20,
        prior_high_breakout=1090.0,
        prior_range_20d_pct=0.18,
    )
    weak_turnover = _bar(
        close=1100.0,
        sma_short=1000.0,
        sma_long=900.0,
        sma_long_past=850.0,
        atr=35.0,
        avg_turnover=800_000_000.0,
        turnover=850_000_000.0,
        return_20d=0.12,
        return_60d=0.20,
        prior_high_breakout=1090.0,
        prior_range_20d_pct=0.18,
    )

    assert not swing.is_entry_signal(stale_high, params)
    assert not swing.is_entry_signal(weak_turnover, params)


def test_volatility_contraction_candidate_preregisters_new_entry_family() -> None:
    params = swing.params_for_candidate(
        "daily_volatility_contraction_v0",
        1_000_000.0,
        200_000_000.0,
    )

    assert params.entry_mode == "volatility_contraction"
    assert params.exit_mode == "target_stop_max_hold"
    assert params.min_return_20d == 0.02
    assert params.max_return_20d == 0.18
    assert params.min_return_60d == 0.05
    assert params.max_prior_range_20d_pct == 0.16
    assert params.min_prior_range_20d_position == 0.70
    assert params.min_atr_pct == 0.012
    assert params.max_atr_pct == 0.045
    assert params.min_entry_gap_pct == -0.01
    assert params.max_entry_gap_pct == 0.02
    assert params.max_new_positions_per_day == 1
    assert swing.deterministic_selections_for_candidate("daily_volatility_contraction_v0") == (
        "ranked",
    )


def test_volatility_contraction_entry_signal_accepts_preregistered_setup() -> None:
    params = swing.params_for_candidate(
        "daily_volatility_contraction_v0",
        1_000_000.0,
        200_000_000.0,
    )
    bar = _bar(
        close=1050.0,
        sma_short=1000.0,
        sma_long=940.0,
        sma_long_past=910.0,
        atr=30.0,
        avg_turnover=800_000_000.0,
        return_20d=0.08,
        return_60d=0.12,
        prior_range_20d_pct=0.12,
        prior_range_20d_position=0.78,
    )

    assert swing.is_entry_signal(bar, params)


def test_volatility_contraction_entry_signal_rejects_wide_or_low_range_position() -> None:
    params = swing.params_for_candidate(
        "daily_volatility_contraction_v0",
        1_000_000.0,
        200_000_000.0,
    )
    wide_range = _bar(
        close=1050.0,
        sma_short=1000.0,
        sma_long=940.0,
        sma_long_past=910.0,
        atr=30.0,
        avg_turnover=800_000_000.0,
        return_20d=0.08,
        return_60d=0.12,
        prior_range_20d_pct=0.20,
        prior_range_20d_position=0.78,
    )
    low_position = _bar(
        close=1020.0,
        sma_short=1000.0,
        sma_long=940.0,
        sma_long_past=910.0,
        atr=30.0,
        avg_turnover=800_000_000.0,
        return_20d=0.08,
        return_60d=0.12,
        prior_range_20d_pct=0.12,
        prior_range_20d_position=0.55,
    )

    assert not swing.is_entry_signal(wide_range, params)
    assert not swing.is_entry_signal(low_position, params)


def test_parse_candidate_list_defaults_to_all_research_candidates() -> None:
    assert swing.parse_candidate_list("") == swing.RESEARCH_CANDIDATES


def test_parse_candidate_list_accepts_subset() -> None:
    assert swing.parse_candidate_list(
        "daily_trend_pullback_exit_fixed10_v0,daily_breakout_continuation_v0"
    ) == (
        "daily_trend_pullback_exit_fixed10_v0",
        "daily_breakout_continuation_v0",
    )


def test_parse_candidate_list_rejects_unknown_candidate() -> None:
    try:
        swing.parse_candidate_list("daily_unknown_v0")
    except ValueError as exc:
        assert "unknown research candidate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_oos_block_stability_separates_positive_blocks_from_full_gate() -> None:
    blocks = [
        {
            "block": 1,
            "oos_start": "2026-01-01",
            "oos_end": "2026-03-31",
            "selected_oos": {
                "trade_count": 10,
                "total_net_pnl": 1000.0,
                "profit_factor": 1.5,
                "max_drawdown": 200.0,
            },
        },
        {
            "block": 2,
            "oos_start": "2026-04-01",
            "oos_end": "2026-06-30",
            "selected_oos": {
                "trade_count": 12,
                "total_net_pnl": -500.0,
                "profit_factor": 0.8,
                "max_drawdown": 700.0,
            },
        },
    ]

    stability = swing.build_oos_block_stability(blocks)

    assert stability["positive_block_count"] == 1
    assert stability["positive_block_ratio"] == 0.5
    assert stability["min_trade_count"] == 10
    assert stability["median_trade_count"] == 11.0
    assert stability["worst_block"]["block"] == 2


def test_random_comparison_diagnostics_rank_and_gate_like_count() -> None:
    diagnostics = swing.build_random_comparison_diagnostics(
        selected_oos_metrics=swing.Metrics(
            trade_count=40,
            total_net_pnl=1000.0,
            win_rate=0.55,
            profit_factor=1.3,
            max_drawdown=50_000.0,
            expectancy=25.0,
            positive_month_ratio=0.6,
            worst_month_net_pnl=-10_000.0,
        ),
        random_oos_summaries=[
            {
                "label": "worse",
                "baseline_kind": "signal_set_random",
                "oos": {
                    "trade_count": 40,
                    "total_net_pnl": 500.0,
                    "profit_factor": 1.25,
                    "max_drawdown": 80_000.0,
                    "positive_month_ratio": 0.6,
                    "worst_month_net_pnl": -20_000.0,
                },
            },
            {
                "label": "better_but_risky",
                "baseline_kind": "signal_set_random",
                "oos": {
                    "trade_count": 40,
                    "total_net_pnl": 1500.0,
                    "profit_factor": 1.4,
                    "max_drawdown": 120_000.0,
                    "positive_month_ratio": 0.7,
                    "worst_month_net_pnl": -20_000.0,
                },
            },
        ],
        params=swing.SwingParams(starting_capital=1_000_000.0),
    )

    assert diagnostics["selected_rank_by_net"] == 2
    assert diagnostics["best_random"]["label"] == "better_but_risky"
    assert diagnostics["by_baseline_kind"]["signal_set_random"]["selected_rank_by_net"] == 2
    assert diagnostics["random_gate_like_pass_count"] == 1


def test_low_frequency_research_gate_passes_formal_block_stability_criteria() -> None:
    gate = swing.build_low_frequency_research_gate(
        selected_oos_gate={"status": "PASS", "failures": []},
        selected_oos_block_stability={
            "positive_block_ratio": 0.688,
            "median_trade_count": 21.5,
            "min_net_pnl": -48_000.0,
        },
        random_comparison={
            "random_count": 100,
            "selected_net_percentile": 0.902,
        },
        selected_train_pass_count=10,
        block_count=16,
        params=swing.SwingParams(starting_capital=1_000_000.0),
    )

    assert gate == {
        "gate_type": "low_frequency_block_stability",
        "uses_per_block_full_check_gate": False,
        "status": "PASS",
        "failures": [],
    }


def test_low_frequency_research_gate_keeps_aggregate_and_random_failures() -> None:
    gate = swing.build_low_frequency_research_gate(
        selected_oos_gate={"status": "FAIL", "failures": ["selected_oos_profit_factor 1 <= 1.2"]},
        selected_oos_block_stability={
            "positive_block_ratio": 0.5,
            "median_trade_count": 12.0,
            "min_net_pnl": -60_000.0,
        },
        random_comparison={
            "random_count": 20,
            "selected_net_percentile": 0.6,
        },
        selected_train_pass_count=3,
        block_count=16,
        params=swing.SwingParams(starting_capital=1_000_000.0),
    )

    assert gate["status"] == "FAIL"
    assert gate["failures"] == [
        "aggregate_selected_oos_profit_factor 1 <= 1.2",
        "selected_train_pass_count 3 < 8",
        "positive_block_ratio 0.5 < 0.6667",
        "median_trade_count 12.0 < 15",
        "min_block_net_pnl -60000.0 < -50000.000",
        "random_count 20 < 100",
        "selected_net_percentile 0.6 < 0.75",
    ]


def test_market_context_guard_blocks_middle_positive_return_breadth() -> None:
    params = swing.params_for_candidate("daily_trend_pullback_v5", 1_000_000.0, 200_000_000.0)

    assert not swing.is_market_context_allowed(
        swing.MarketContext(
            date=date(2026, 1, 10),
            close_above_sma20_ratio=0.5,
            trend_breadth_ratio=0.4,
            positive_return_5d_ratio=0.6,
            avg_return_5d=0.01,
            positive_return_20d_ratio=0.5,
            avg_return_20d=0.01,
            positive_return_60d_ratio=0.6,
            avg_return_60d=0.03,
        ),
        params,
    )
    assert swing.is_market_context_allowed(
        swing.MarketContext(
            date=date(2026, 1, 10),
            close_above_sma20_ratio=0.5,
            trend_breadth_ratio=0.4,
            positive_return_5d_ratio=0.6,
            avg_return_5d=0.01,
            positive_return_20d_ratio=0.44,
            avg_return_20d=0.01,
            positive_return_60d_ratio=0.6,
            avg_return_60d=0.03,
        ),
        params,
    )
    assert swing.is_market_context_allowed(
        swing.MarketContext(
            date=date(2026, 1, 10),
            close_above_sma20_ratio=0.5,
            trend_breadth_ratio=0.4,
            positive_return_5d_ratio=0.6,
            avg_return_5d=0.01,
            positive_return_20d_ratio=0.55,
            avg_return_20d=0.01,
            positive_return_60d_ratio=0.6,
            avg_return_60d=0.03,
        ),
        params,
    )
    assert not swing.is_market_context_allowed(None, params)


def test_exit_on_bar_uses_stop_before_target_collision() -> None:
    params = swing.SwingParams()
    position = swing.Position(
        symbol="7203",
        signal_date=date(2026, 1, 30),
        signal_close=100.0,
        signal_sma_short=98.0,
        signal_atr_pct=0.03,
        signal_return_20d=0.08,
        signal_avg_turnover=900_000_000.0,
        entry_gap_pct=0.0,
        entry_date=date(2026, 1, 31),
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        quantity=100,
        max_exit_date=date(2026, 2, 10),
        entry_score=0.12,
        ranked_position=1,
        candidate_count=3,
    )
    trade = swing._exit_on_bar(position, _bar(high=112.0, low=94.0), params)

    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 95.0


def test_fixed_hold_exit_ignores_intrahold_stop_and_target() -> None:
    params = swing.SwingParams(exit_mode="fixed_hold")
    position = swing.Position(
        symbol="7203",
        signal_date=date(2026, 1, 30),
        signal_close=100.0,
        signal_sma_short=98.0,
        signal_atr_pct=0.03,
        signal_return_20d=0.08,
        signal_avg_turnover=900_000_000.0,
        entry_gap_pct=0.0,
        entry_date=date(2026, 1, 31),
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        quantity=100,
        max_exit_date=date(2026, 2, 10),
        entry_score=0.12,
        ranked_position=1,
        candidate_count=3,
    )

    assert (
        swing._exit_on_bar(
            position,
            _bar(date=date(2026, 2, 9), high=120.0, low=80.0),
            params,
        )
        is None
    )
    trade = swing._exit_on_bar(
        position,
        _bar(date=date(2026, 2, 10), high=120.0, low=80.0, close=104.0),
        params,
    )

    assert trade is not None
    assert trade.exit_reason == "fixed_hold"
    assert trade.exit_price == 104.0


def test_gate_rejects_tiny_profitable_sample() -> None:
    metrics = swing.Metrics(
        trade_count=2,
        total_net_pnl=10_000.0,
        win_rate=1.0,
        profit_factor=2.0,
        max_drawdown=0.0,
        expectancy=5_000.0,
        positive_month_ratio=1.0,
        worst_month_net_pnl=10_000.0,
    )

    result = swing.check_gate(metrics, swing.SwingParams())

    assert result["status"] == "FAIL"
    assert any("validation_trade_count" in failure for failure in result["failures"])


def test_gate_rejects_large_single_month_loss() -> None:
    metrics = swing.Metrics(
        trade_count=30,
        total_net_pnl=50_000.0,
        win_rate=0.6,
        profit_factor=1.5,
        max_drawdown=60_000.0,
        expectancy=1666.0,
        positive_month_ratio=0.7,
        worst_month_net_pnl=-60_000.0,
    )

    result = swing.check_gate(metrics, swing.SwingParams())

    assert result["status"] == "FAIL"
    assert any("validation_worst_month_net_pnl" in failure for failure in result["failures"])


def test_order_candidates_random_is_seeded_and_preserves_members() -> None:
    candidates = [
        swing.EntryCandidate(
            symbol=str(idx),
            signal_date=date(2026, 1, 1),
            signal_close=1000.0,
            signal_sma_short=990.0,
            signal_atr_pct=0.03,
            signal_return_20d=0.1,
            signal_avg_turnover=500_000_000.0,
            entry_gap_pct=0.0,
            entry_date=date(2026, 1, 2),
            entry_price=1000.0,
            stop_price=950.0,
            target_price=1100.0,
            quantity=100,
            score=float(idx),
            ranked_position=idx + 1,
            candidate_count=6,
        )
        for idx in range(6)
    ]

    ordered_a = swing.order_candidates(candidates, selection="random", rng=swing.random.Random(7))
    ordered_b = swing.order_candidates(candidates, selection="random", rng=swing.random.Random(7))

    assert [item.symbol for item in ordered_a] == [item.symbol for item in ordered_b]
    assert {item.symbol for item in ordered_a} == {item.symbol for item in candidates}
    assert [item.symbol for item in ordered_a] != [item.symbol for item in candidates]


def test_order_candidates_score_modes_are_deterministic() -> None:
    candidates = [
        swing.EntryCandidate(
            symbol=symbol,
            signal_date=date(2026, 1, 1),
            signal_close=1000.0,
            signal_sma_short=990.0,
            signal_atr_pct=0.03,
            signal_return_20d=0.1,
            signal_avg_turnover=500_000_000.0,
            entry_gap_pct=0.0,
            entry_date=date(2026, 1, 2),
            entry_price=1000.0,
            stop_price=950.0,
            target_price=1100.0,
            quantity=100,
            score=score,
            ranked_position=idx + 1,
            candidate_count=4,
        )
        for idx, (symbol, score) in enumerate(
            [("high", 0.31), ("mid_hi", 0.18), ("mid_lo", 0.12), ("low", 0.04)]
        )
    ]

    ascending = swing.order_candidates(
        candidates,
        selection="score_ascending",
        rng=swing.random.Random(1),
    )
    middle = swing.order_candidates(
        candidates,
        selection="score_middle",
        rng=swing.random.Random(1),
    )

    assert [item.symbol for item in ascending] == ["low", "mid_lo", "mid_hi", "high"]
    assert [item.symbol for item in middle] == ["mid_hi", "mid_lo", "low", "high"]


def test_order_candidates_rank_2_3_first_prefers_middle_ranks() -> None:
    candidates = [
        swing.EntryCandidate(
            symbol=symbol,
            signal_date=date(2026, 1, 1),
            signal_close=1000.0,
            signal_sma_short=990.0,
            signal_atr_pct=0.03,
            signal_return_20d=0.1,
            signal_avg_turnover=500_000_000.0,
            entry_gap_pct=0.0,
            entry_date=date(2026, 1, 2),
            entry_price=1000.0,
            stop_price=950.0,
            target_price=1100.0,
            quantity=100,
            score=score,
            ranked_position=rank,
            candidate_count=6,
        )
        for symbol, rank, score in [
            ("rank1", 1, 0.20),
            ("rank2", 2, 0.18),
            ("rank3", 3, 0.12),
            ("rank4", 4, 0.10),
            ("rank6", 6, 0.08),
        ]
    ]

    ordered = swing.order_candidates(
        candidates,
        selection="rank_2_3_first",
        rng=swing.random.Random(1),
    )

    assert [item.symbol for item in ordered] == ["rank2", "rank3", "rank1", "rank4", "rank6"]


def test_walk_forward_summary_splits_validation_by_exit_date() -> None:
    trades = [
        _trade(exit_date=date(2026, 1, 10), net_pnl=10_000.0),
        _trade(exit_date=date(2026, 1, 20), net_pnl=-2_000.0),
        _trade(exit_date=date(2026, 2, 10), net_pnl=-5_000.0),
        _trade(exit_date=date(2026, 2, 20), net_pnl=1_000.0),
        _trade(exit_date=date(2026, 3, 10), net_pnl=8_000.0),
        _trade(exit_date=date(2026, 3, 20), net_pnl=9_000.0),
    ]

    summary = swing.build_walk_forward_summary(trades, fold_count=3)

    assert summary["fold_count"] == 3
    assert summary["positive_fold_count"] == 2
    assert [fold["metrics"]["total_net_pnl"] for fold in summary["folds"]] == [
        8000.0,
        -4000.0,
        17000.0,
    ]
    assert summary["folds"][0]["start_date"] == "2026-01-10"
    assert summary["folds"][2]["end_date"] == "2026-03-20"


def test_walk_forward_gate_rejects_too_few_positive_folds() -> None:
    gate = {"status": "PASS", "failures": []}
    summary = {
        "fold_count": 3,
        "positive_fold_count": 1,
        "negative_fold_count": 2,
        "folds": [],
    }

    swing.apply_walk_forward_gate(gate, summary)

    assert gate["status"] == "FAIL"
    assert gate["failures"] == ["validation_positive_fold_count 1 < 2"]


def test_walk_forward_gate_uses_custom_label() -> None:
    gate = {"status": "PASS", "failures": []}
    summary = {
        "fold_count": 3,
        "positive_fold_count": 1,
        "negative_fold_count": 2,
        "folds": [],
    }

    swing.apply_walk_forward_gate(gate, summary, label="selected_oos")

    assert gate["status"] == "FAIL"
    assert gate["failures"] == ["selected_oos_positive_fold_count 1 < 2"]


def test_combine_gates_merges_failures() -> None:
    combined = swing.combine_gates(
        {"status": "PASS", "failures": []},
        {"status": "FAIL", "failures": ["validation_profit_factor 1.0 <= 1.2"]},
    )

    assert combined == {
        "status": "FAIL",
        "failures": ["validation_profit_factor 1.0 <= 1.2"],
    }


def test_parse_seed_list() -> None:
    assert swing.parse_seed_list("") == []
    assert swing.parse_seed_list("1,2, 3") == [1, 2, 3]


def test_parse_date_list() -> None:
    assert swing.parse_date_list("") == []
    assert swing.parse_date_list("2026-01-01, 2026-06-01") == [
        date(2026, 1, 1),
        date(2026, 6, 1),
    ]


def test_parse_baseline_kind_list() -> None:
    assert swing.parse_baseline_kind_list("signal_set_random, symbol_matched_random_date") == [
        "signal_set_random",
        "symbol_matched_random_date",
    ]


def test_classify_market_regime_uses_preregistered_buckets() -> None:
    assert (
        swing.classify_market_regime(
            swing.MarketContext(
                date=date(2026, 1, 1),
                close_above_sma20_ratio=0.7,
                trend_breadth_ratio=0.6,
                positive_return_5d_ratio=0.6,
                avg_return_5d=0.01,
                positive_return_20d_ratio=0.65,
                avg_return_20d=0.03,
                positive_return_60d_ratio=0.65,
                avg_return_60d=0.05,
            )
        )
        == "broad_uptrend"
    )
    assert (
        swing.classify_market_regime(
            swing.MarketContext(
                date=date(2026, 1, 1),
                close_above_sma20_ratio=0.3,
                trend_breadth_ratio=0.3,
                positive_return_5d_ratio=0.3,
                avg_return_5d=-0.01,
                positive_return_20d_ratio=0.4,
                avg_return_20d=-0.03,
                positive_return_60d_ratio=0.35,
                avg_return_60d=-0.05,
            )
        )
        == "broad_downtrend"
    )
    assert (
        swing.classify_market_regime(
            swing.MarketContext(
                date=date(2026, 1, 1),
                close_above_sma20_ratio=0.5,
                trend_breadth_ratio=0.4,
                positive_return_5d_ratio=0.55,
                avg_return_5d=0.01,
                positive_return_20d_ratio=0.55,
                avg_return_20d=0.02,
                positive_return_60d_ratio=0.5,
                avg_return_60d=0.03,
            )
        )
        == "narrow_leadership"
    )


def test_gap_stop_stress_adds_slippage_and_limit_down_fill() -> None:
    params = swing.SwingParams()
    position = swing.Position(
        symbol="7203",
        signal_date=date(2026, 1, 30),
        signal_close=100.0,
        signal_sma_short=98.0,
        signal_atr_pct=0.03,
        signal_return_20d=0.08,
        signal_avg_turnover=900_000_000.0,
        entry_gap_pct=0.0,
        entry_date=date(2026, 1, 31),
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        quantity=100,
        max_exit_date=date(2026, 2, 10),
        entry_score=0.12,
        ranked_position=1,
        candidate_count=3,
    )

    slipped = swing._exit_on_bar(
        position,
        _bar(open=94.0, high=96.0, low=90.0, close=93.0),
        params,
        execution_stress=swing.ExecutionStress(gap_stop_additional_slippage_rate=0.01),
    )
    limit_down = swing._exit_on_bar(
        position,
        _bar(open=80.0, high=82.0, low=80.0, close=78.0),
        params,
        execution_stress=swing.ExecutionStress(limit_down_unfillable=True),
    )

    assert slipped is not None
    assert slipped.exit_reason == "gap_stop"
    assert slipped.exit_price == 93.06
    assert limit_down is not None
    assert limit_down.exit_reason == "limit_down_unfillable_gap_stop"
    assert limit_down.exit_price == 78.0


def test_build_selection_comparison_reports_all_selectors() -> None:
    original_simulate = swing.simulate

    def fake_simulate(
        _prepared,
        _params,
        *,
        selection="ranked",
        random_seed=1,
        baseline_kind="strategy",
        execution_stress=None,
        simulation_context=None,
        candidate_pools=None,
    ):
        if selection == "score_middle":
            return [
                _trade(exit_date=date(2026, 1, 10), net_pnl=-1_000.0),
                _trade(exit_date=date(2026, 2, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 3, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 4, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 5, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 6, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 7, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 8, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 9, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 10, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 11, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 12, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 1, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 2, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 3, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 4, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 5, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 6, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 7, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 8, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 9, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 10, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 11, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2027, 12, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 1, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 2, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 3, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 4, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 5, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2028, 6, 10), net_pnl=20_000.0),
            ]
        if selection == "random" and random_seed == 2:
            return [_trade(exit_date=date(2026, 1, 10), net_pnl=30_000.0)]
        return [_trade(exit_date=date(2026, 1, 10), net_pnl=-10_000.0)]

    swing.simulate = fake_simulate
    try:
        result = swing.build_selection_comparison(
            prepared={},
            params=swing.SwingParams(),
            candidate="daily_trend_pullback_v4",
            input_path=Path("dummy.csv"),
            validation_start=date(2026, 1, 1),
            fold_count=3,
            random_seeds=[1, 2],
        )
    finally:
        swing.simulate = original_simulate

    assert [row["label"] for row in result["selections"]] == [
        "score_middle",
        "signal_set_random:seed_2",
        "universe_date_matched_random:seed_2",
        "symbol_matched_random_date:seed_2",
        "ranked",
        "score_ascending",
        "rank_2_3_first",
        "signal_set_random:seed_1",
        "universe_date_matched_random:seed_1",
        "symbol_matched_random_date:seed_1",
    ]
    assert result["selections"][0]["gate"]["status"] == "FAIL"
    assert any(
        failure.startswith("train_")
        for failure in result["selections"][0]["train_gate"]["failures"]
    )


def test_build_multi_split_selection_comparison_summarizes_splits() -> None:
    original_simulate = swing.simulate

    def fake_simulate(
        _prepared,
        _params,
        *,
        selection="ranked",
        random_seed=1,
        baseline_kind="strategy",
        execution_stress=None,
        simulation_context=None,
        candidate_pools=None,
    ):
        if selection == "score_middle":
            return [
                _trade(exit_date=date(2025, 12, 10), net_pnl=50_000.0),
                _trade(exit_date=date(2026, 1, 10), net_pnl=60_000.0),
                _trade(exit_date=date(2026, 6, 10), net_pnl=-10_000.0),
            ]
        if selection == "random" and random_seed == 2:
            return [
                _trade(exit_date=date(2025, 12, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 1, 10), net_pnl=20_000.0),
                _trade(exit_date=date(2026, 6, 10), net_pnl=-5_000.0),
            ]
        return [_trade(exit_date=date(2026, 1, 10), net_pnl=-10_000.0)]

    swing.simulate = fake_simulate
    try:
        result = swing.build_multi_split_selection_comparison(
            prepared={},
            params=swing.SwingParams(),
            candidate="daily_trend_pullback_v4",
            input_path=Path("dummy.csv"),
            validation_starts=[date(2026, 1, 1), date(2026, 6, 1)],
            fold_count=2,
            random_seeds=[1, 2],
        )
    finally:
        swing.simulate = original_simulate

    assert result["validation_starts"] == ["2026-01-01", "2026-06-01"]
    assert len(result["split_comparisons"]) == 2
    score_middle = next(
        row for row in result["selection_summary"] if row["label"] == "score_middle"
    )
    assert score_middle["split_count"] == 2
    assert score_middle["total_validation_net_pnl"] == 40_000.0
    assert len(score_middle["failed_splits"]) == 2


def test_oos_block_trades_require_entry_inside_block() -> None:
    trades = [
        _trade(entry_date=date(2026, 1, 1), exit_date=date(2026, 2, 1), net_pnl=10_000.0),
        _trade(entry_date=date(2026, 2, 5), exit_date=date(2026, 2, 10), net_pnl=20_000.0),
        _trade(entry_date=date(2026, 2, 20), exit_date=date(2026, 3, 2), net_pnl=30_000.0),
    ]

    selected = swing._oos_block_trades(
        trades,
        oos_start=date(2026, 2, 1),
        oos_end=date(2026, 2, 28),
    )

    assert [trade.net_pnl for trade in selected] == [20_000.0]


def test_select_walk_forward_run_prefers_train_gate_pass() -> None:
    passing_trades = [_trade(exit_date=date(2026, 1, day), net_pnl=1_000.0) for day in range(1, 31)]
    passing_trades.append(_trade(exit_date=date(2026, 1, 31), net_pnl=-100.0))
    higher_net_failing_trades = [
        _trade(exit_date=date(2026, 1, 1), net_pnl=40_000.0),
    ]
    runs = [
        {
            "label": "passing",
            "candidate": "daily_trend_pullback_v0",
            "selection": "ranked",
            "random_seed": None,
            "params": swing.SwingParams(),
            "trades": passing_trades,
        },
        {
            "label": "higher_net_failing",
            "candidate": "daily_trend_pullback_v0",
            "selection": "ranked",
            "random_seed": None,
            "params": swing.SwingParams(),
            "trades": higher_net_failing_trades,
        },
    ]

    selected = swing._select_walk_forward_run(runs, date(2026, 2, 1))

    assert selected["label"] == "passing"
    assert selected["selection_reason"] == "best_train_gate_pass"


def test_walk_forward_research_gate_requires_block_and_random_baseline_passes() -> None:
    gate = swing.build_walk_forward_research_gate(
        selected_oos_gate={"status": "PASS", "failures": []},
        block_count=4,
        selected_train_pass_count=1,
        selected_oos_pass_count=1,
        selected_oos_metrics=swing.Metrics(
            trade_count=100,
            total_net_pnl=200_000.0,
            win_rate=0.6,
            profit_factor=1.5,
            max_drawdown=50_000.0,
            expectancy=2_000.0,
            positive_month_ratio=0.7,
            worst_month_net_pnl=-20_000.0,
        ),
        random_oos_summaries=[
            {
                "label": "random",
                "oos": {"total_net_pnl": 250_000.0},
            }
        ],
    )

    assert gate["status"] == "FAIL"
    assert gate["failures"] == [
        "selected_train_pass_count 1 < 4",
        "selected_oos_pass_count 1 < 3",
        "selected_oos_total_net_pnl 200000.000 <= best_random_oos 250000.000",
    ]


def test_bucket_stability_report_prioritizes_same_sign_weak_buckets() -> None:
    train = {
        "entry_score_bins": [
            {"key": "weak", "trade_count": 4, "net_pnl": -1200.0},
            {"key": "mixed", "trade_count": 4, "net_pnl": -800.0},
            {"key": "strong", "trade_count": 4, "net_pnl": 1000.0},
        ]
    }
    validation = {
        "entry_score_bins": [
            {"key": "weak", "trade_count": 2, "net_pnl": -600.0},
            {"key": "mixed", "trade_count": 2, "net_pnl": 500.0},
            {"key": "strong", "trade_count": 2, "net_pnl": 700.0},
        ]
    }

    report = swing.build_bucket_stability_report(train, validation)

    assert report[0] == {
        "diagnostic": "entry_score_bins",
        "bucket": "weak",
        "train_trade_count": 4,
        "validation_trade_count": 2,
        "train_net_pnl": -1200.0,
        "validation_net_pnl": -600.0,
        "train_expectancy": -300.0,
        "validation_expectancy": -300.0,
        "combined_net_pnl": -1800.0,
        "same_sign": True,
    }
    assert any(row["bucket"] == "mixed" and not row["same_sign"] for row in report)


def test_diagnostics_include_drawdown_period_and_exit_reasons() -> None:
    trades = [
        _trade(
            symbol="7203",
            entry_date=date(2026, 1, 5),
            exit_date=date(2026, 1, 10),
            entry_price=1000.0,
            exit_price=1100.0,
            quantity=100,
            exit_reason="target",
            gross_pnl=10_000.0,
            costs=300.0,
            net_pnl=9_700.0,
        ),
        _trade(
            symbol="6758",
            entry_date=date(2026, 1, 11),
            exit_date=date(2026, 1, 15),
            entry_price=1000.0,
            exit_price=900.0,
            quantity=100,
            exit_reason="stop",
            gross_pnl=-10_000.0,
            costs=300.0,
            net_pnl=-10_300.0,
        ),
        _trade(
            symbol="9984",
            entry_date=date(2026, 1, 16),
            exit_date=date(2026, 1, 20),
            entry_price=1000.0,
            exit_price=950.0,
            quantity=100,
            exit_reason="stop",
            gross_pnl=-5_000.0,
            costs=300.0,
            net_pnl=-5_300.0,
        ),
    ]

    diagnostics = swing.build_diagnostics(trades)

    assert diagnostics["max_drawdown_period"]["amount"] == 15600.0
    assert diagnostics["max_drawdown_period"]["peak_date"] == "2026-01-10"
    assert diagnostics["max_drawdown_period"]["trough_date"] == "2026-01-20"
    assert diagnostics["entry_gap_pct_bins"] == [
        {
            "key": "1.0%..3.0%",
            "trade_count": 3,
            "net_pnl": -5900.0,
            "gross_pnl": -5000.0,
            "costs": 900.0,
            "win_rate": 0.3333,
        }
    ]
    assert diagnostics["entry_score_bins"] == [
        {
            "key": "0.10..0.15",
            "trade_count": 3,
            "net_pnl": -5900.0,
            "gross_pnl": -5000.0,
            "costs": 900.0,
            "win_rate": 0.3333,
        }
    ]
    assert diagnostics["ranked_position_bins"] == [
        {
            "key": "1",
            "trade_count": 3,
            "net_pnl": -5900.0,
            "gross_pnl": -5000.0,
            "costs": 900.0,
            "win_rate": 0.3333,
        }
    ]
    assert diagnostics["exit_reasons"] == [
        {
            "key": "stop",
            "trade_count": 2,
            "net_pnl": -15600.0,
            "gross_pnl": -15000.0,
            "costs": 600.0,
            "win_rate": 0.0,
        },
        {
            "key": "target",
            "trade_count": 1,
            "net_pnl": 9700.0,
            "gross_pnl": 10000.0,
            "costs": 300.0,
            "win_rate": 1.0,
        },
    ]


def test_prepare_bars_first_atr_does_not_use_last_close() -> None:
    rows = [
        swing.OhlcvRow(
            symbol="7203",
            date=date(2026, 1, day),
            open=100.0 + day,
            high=102.0 + day,
            low=99.0 + day,
            close=101.0 + day,
            volume=1000,
            turnover=1_000_000.0,
        )
        for day in range(1, 16)
    ]

    bars = swing.prepare_bars(rows, swing.SwingParams())["7203"]

    assert bars[13].atr is None
    assert bars[14].atr is not None


def test_build_market_context_by_date_summarizes_universe_breadth() -> None:
    context = swing.build_market_context_by_date(
        {
            "7203": [
                _bar(
                    symbol="7203",
                    date=date(2026, 1, 10),
                    close=110.0,
                    sma_short=100.0,
                    sma_long=90.0,
                    return_5d=0.04,
                    return_20d=0.08,
                    return_60d=0.12,
                )
            ],
            "6758": [
                _bar(
                    symbol="6758",
                    date=date(2026, 1, 10),
                    close=95.0,
                    sma_short=100.0,
                    sma_long=98.0,
                    return_5d=-0.02,
                    return_20d=-0.03,
                    return_60d=-0.04,
                )
            ],
        }
    )[date(2026, 1, 10)]

    assert context.close_above_sma20_ratio == 0.5
    assert context.trend_breadth_ratio == 0.5
    assert context.positive_return_5d_ratio == 0.5
    assert context.avg_return_5d == 0.01
    assert context.positive_return_20d_ratio == 0.5
    assert context.avg_return_20d == 0.025
    assert context.positive_return_60d_ratio == 0.5
    assert round(context.avg_return_60d, 4) == 0.04
