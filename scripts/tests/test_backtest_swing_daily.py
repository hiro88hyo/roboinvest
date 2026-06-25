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


def test_build_selection_comparison_reports_all_selectors() -> None:
    original_simulate = swing.simulate

    def fake_simulate(_prepared, _params, *, selection="ranked", random_seed=1):
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
        "random_seed_2",
        "ranked",
        "score_ascending",
        "rank_2_3_first",
        "random_seed_1",
    ]
    assert result["selections"][0]["gate"]["status"] == "FAIL"
    assert any(
        failure.startswith("train_")
        for failure in result["selections"][0]["train_gate"]["failures"]
    )


def test_build_multi_split_selection_comparison_summarizes_splits() -> None:
    original_simulate = swing.simulate

    def fake_simulate(_prepared, _params, *, selection="ranked", random_seed=1):
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
