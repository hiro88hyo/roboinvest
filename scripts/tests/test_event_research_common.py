from __future__ import annotations

import importlib.util
import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from trade_contracts.event_research import EntryArm, EventType, ExitArm


def _load_module():
    path = Path(__file__).resolve().parents[1] / "event_research_common.py"
    spec = importlib.util.spec_from_file_location("event_research_common", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


event_research = _load_module()


def _bars(symbol: str = "7203", *, start_close: int = 1000):
    rows = []
    for idx in range(45):
        day = date(2026, 1, 1) + timedelta(days=idx)
        close = Decimal(start_close + idx * 5)
        rows.append(
            event_research.OhlcvRow(
                symbol=symbol,
                date=day,
                open=close - Decimal("2"),
                high=close + Decimal("8"),
                low=close - Decimal("8"),
                close=close,
                volume=500_000,
                turnover=close * Decimal("500000"),
            )
        )
    return rows


def _raw(**overrides):
    raw = {
        "Code": "72030",
        "DiscDate": "2026-01-21",
        "DiscTime": "15:30",
        "DiscNo": "fixture-1",
        "DocType": "ForecastRevision_Consolidated_JP",
        "CurFYEn": "2026-03-31",
        "FEPS": "125",
        "FOP": "1250",
        "FNP": "1000",
        "FSales": "10000",
        "EPS": "110",
        "BPS": "1000",
        "FDivAnn": "40",
    }
    raw.update(overrides)
    return raw


def _event(raw: dict[str, object], bars: list[object]):
    return event_research.build_events_from_financial_rows(
        [raw],
        ohlcv_rows=bars,
        fetched_at=datetime(2026, 1, 22, tzinfo=UTC),
    )[0]


def test_post_close_and_unknown_time_enter_next_trading_day() -> None:
    bars = _bars()
    post_close = _event(_raw(DiscDate="2026-01-21", DiscTime="15:30"), bars)
    unknown = _event(_raw(DiscDate="2026-01-21", DiscTime=""), bars)

    assert post_close.disclosed_at.isoformat() == "2026-01-21T06:30:00+00:00"
    assert unknown.disclosed_at.isoformat() == "2026-01-20T15:00:00+00:00"
    assert post_close.entry_date == "2026-01-22"
    assert unknown.entry_date == "2026-01-22"


def test_events_without_next_trading_day_are_skipped() -> None:
    bars = _bars()
    events = event_research.build_events_from_financial_rows(
        [_raw(DiscDate="2026-02-14", DiscTime="15:30")],
        ohlcv_rows=bars,
        fetched_at=datetime(2026, 2, 14, tzinfo=UTC),
    )

    assert events == []


def test_next_open_features_use_signal_close_not_entry_open() -> None:
    bars = _bars()
    event = _event(_raw(), bars)
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    assert observation.entry_date == "2026-01-22"
    assert observation.valuation_price == Decimal("1100")
    assert observation.entry_price == Decimal("1103")
    assert observation.valuation_features_v0.forecast_per.value == Decimal("8.8")
    assert observation.source_bar_date == "2026-01-21"
    assert observation.source_bar_available_at <= observation.feature_cutoff_at


def test_intraday_and_unknown_time_do_not_use_same_day_bar() -> None:
    bars = _bars()
    intraday = _event(_raw(DiscTime="14:00"), bars)
    unknown = _event(_raw(DiscTime=""), bars)

    intraday_obs = event_research.build_observations([intraday], ohlcv_rows=bars)[0]
    unknown_obs = event_research.build_observations([unknown], ohlcv_rows=bars)[0]

    assert unknown.signal_date == "2026-01-21"
    assert unknown.entry_date == "2026-01-22"
    assert intraday_obs.source_bar_date == "2026-01-20"
    assert unknown_obs.source_bar_date == "2026-01-20"


def test_real_jquants_summary_keys_are_mapped_point_in_time() -> None:
    bars = _bars()
    raw = {
        "Code": "72030",
        "DiscDate": "2026-01-21",
        "DiscTime": "15:30:00",
        "DiscNo": "20260121555555",
        "DocType": "3QFinancialStatements_Consolidated_JP",
        "EPS": "40.3",
        "FEPS": "59.38",
        "FOP": "2200000000",
        "FNP": "1220000000",
        "FSales": "38600000000",
        "BPS": "1000",
        "FDivAnn": "20",
    }

    event = _event(raw, bars)
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    assert event.event_type == EventType.EARNINGS_RESULT
    assert event.raw_document_type == "3QFinancialStatements_Consolidated_JP"
    assert event.raw_source_identifier == "20260121555555"
    assert observation.fundamental_features_v0.eps_latest.value == Decimal("40.3")
    assert observation.fundamental_features_v0.revised_forecast_eps.value == Decimal("59.38")
    assert observation.valuation_features_v0.forecast_dividend_per_share.value == Decimal("20")


def test_eps_sign_change_does_not_make_percentage_or_negative_per() -> None:
    bars = _bars(symbol="6758", start_close=2000)
    prior = _raw(
        Code="67580",
        DiscDate="2026-01-20",
        DiscNo="fixture-6758-prior",
        FEPS="-5",
        EPS="-3",
    )
    current = _raw(
        Code="67580",
        DiscNo="fixture-6758-current",
        FEPS="8",
        EPS="-3",
    )
    events = event_research.build_events_from_financial_rows(
        [prior, current],
        ohlcv_rows=bars,
        fetched_at=datetime(2026, 1, 22, tzinfo=UTC),
    )
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    observation = next(
        obs for obs in observations if obs.source_record_id == "fixture-6758-current"
    )

    assert observation.fundamental_features_v0.sign_changed is True
    assert observation.fundamental_features_v0.previous_forecast_eps.value == Decimal("-5")
    assert observation.fundamental_features_v0.forecast_eps_revision_pct.valid is False
    assert observation.valuation_features_v0.trailing_per.value is None
    assert observation.valuation_features_v0.trailing_per_valid is False
    assert observation.valuation_features_v0.forecast_per.value is not None


def test_dividend_revision_subtype_blocks_decrease_long_candidate() -> None:
    bars = _bars()
    prior = _raw(
        DiscDate="2026-01-19",
        DiscNo="fixture-prior-dividend",
        DocType="ForecastRevision_Consolidated_JP",
        FDivAnn="60",
    )
    increase = _raw(
        DiscNo="fixture-dividend-increase",
        DocType="DividendRevision_Consolidated_JP",
        FDivAnn="75",
    )
    decrease = _raw(
        DiscDate="2026-01-22",
        DiscNo="fixture-dividend-decrease",
        DocType="DividendRevision_Consolidated_JP",
        FDivAnn="50",
    )
    events = event_research.build_events_from_financial_rows(
        [increase, decrease, prior],
        ohlcv_rows=bars,
        fetched_at=datetime(2026, 1, 23, tzinfo=UTC),
    )
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    by_source = {obs.source_record_id: obs for obs in observations}

    assert by_source["fixture-dividend-increase"].event_subtype == "increase"
    assert by_source["fixture-dividend-decrease"].event_subtype == "decrease"
    assert event_research.fundamental_rule_allows(by_source["fixture-dividend-increase"])
    assert not event_research.fundamental_rule_allows(by_source["fixture-dividend-decrease"])


def test_missing_financial_values_are_null_not_zero() -> None:
    bars = _bars()
    event = _event(
        _raw(
            ForecastEarningsPerShare="",
            EarningsPerShare="",
            BookValuePerShare="",
            ForecastDividendPerShareAnnual="",
            FEPS="",
            EPS="",
            BPS="",
            FDivAnn="",
        ),
        bars,
    )
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    assert observation.fundamental_features_v0.missing_eps is True
    assert observation.valuation_features_v0.forecast_per.value is None
    assert observation.valuation_features_v0.pbr.value is None
    assert observation.valuation_features_v0.forecast_dividend_yield.value is None


def test_technical_veto_is_preregistered_and_uses_pre_event_bars_only() -> None:
    bars = _bars()
    event = _event(_raw(), bars)
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    assert event_research.entry_arm_allows(observation, EntryArm.EVENT_PLUS_TECHNICAL)
    assert observation.technical_context_v0.avg_turnover_20d.valid is True


def test_fixed_exit_uses_trading_session_horizon() -> None:
    bars = _bars()
    event = _event(_raw(), bars)
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    assert observation.labels["exit_date_10d"] == "2026-02-01"
    metrics = event_research.metrics_for_observations([observation], exit_arm=ExitArm.FIXED_10D)
    assert metrics["trade_count"] == 1


def test_gap_through_catastrophic_stop_exits_at_unfavorable_open() -> None:
    bars = _bars()
    for idx, bar in enumerate(bars):
        if bar.date == date(2026, 1, 24):
            bars[idx] = event_research.OhlcvRow(
                symbol=bar.symbol,
                date=bar.date,
                open=Decimal("850"),
                high=Decimal("860"),
                low=Decimal("840"),
                close=Decimal("855"),
                volume=bar.volume,
                turnover=bar.turnover,
            )
            break
    event = _event(_raw(), bars)
    observation = event_research.build_observations([event], ohlcv_rows=bars)[0]

    stop_return = observation.labels["catastrophic_stop_return_10d"]
    assert stop_return == pytest.approx((850 / 1103) - 1)
    assert observation.labels["catastrophic_stop_exit_reason_10d"] == (
        "gap_through_catastrophic_stop"
    )


def test_manifest_has_20_trading_day_purge() -> None:
    bars = _bars()
    events = [
        _event(_raw(DiscDate=f"2026-01-{day:02d}", DiscNo=f"fixture-{day}"), bars)
        for day in range(3, 26)
    ]
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    manifest = event_research.split_manifest(observations)

    assert manifest["purge_days"] == 20
    assert manifest["feature_schema_version"] == "event_research_v0"


def test_select_observations_for_split_excludes_purge_and_locked_oos() -> None:
    bars = _bars()
    events = [
        _event(_raw(DiscDate=f"2026-01-{day:02d}", DiscNo=f"fixture-{day}"), bars)
        for day in range(3, 26)
    ]
    observations = event_research.build_observations(events, ohlcv_rows=bars)

    development, info = event_research.select_observations_for_split(
        observations,
        split="development",
    )
    locked, _ = event_research.select_observations_for_split(observations, split="locked-oos")

    assert info["split_counts"]["purge_train_validation"] > 0
    assert len(development) + len(locked) < len(observations)
    assert {obs.observation_id for obs in development}.isdisjoint(
        {obs.observation_id for obs in locked}
    )
    manifest = info["split_manifest"]
    validation_start = date.fromisoformat(manifest["validation_start"])
    train = [
        obs
        for obs in development
        if event_research.observation_split_label(obs, manifest) == "train"
    ]
    assert all(date.fromisoformat(obs.labels["exit_date_20d"]) < validation_start for obs in train)


def test_fixed_split_manifest_keeps_boundaries_when_older_history_is_added() -> None:
    bars = _bars()
    events = [
        _event(_raw(DiscDate=f"2026-01-{day:02d}", DiscNo=f"fixture-{day}"), bars)
        for day in range(3, 26)
    ]
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    manifest = event_research.split_manifest(observations)
    older_observations = []
    for idx in range(8):
        signal = date(2025, 12, 1) + timedelta(days=idx)
        older_observations.append(
            observations[0].model_copy(
                deep=True,
                update={
                    "observation_id": f"older-{idx}",
                    "event_id": f"older-event-{idx}",
                    "event_cluster_id": f"older-cluster-{idx}",
                    "trade_group_id": f"older-trade-{idx}",
                    "signal_date": signal.isoformat(),
                    "entry_date": (signal + timedelta(days=1)).isoformat(),
                    "labels": {
                        **observations[0].labels,
                        "exit_date_20d": (signal + timedelta(days=20)).isoformat(),
                    },
                },
            )
        )
    expanded = [*older_observations, *observations]

    fixed_train, fixed_info = event_research.select_observations_for_split(
        expanded,
        split="train",
        fixed_split_manifest=manifest,
    )
    dynamic_manifest = event_research.split_manifest(expanded)

    assert fixed_info["split_manifest"]["fixed_split_manifest"] is True
    assert fixed_info["split_manifest"]["train_end"] == manifest["train_end"]
    assert fixed_info["split_manifest"]["validation_start"] == manifest["validation_start"]
    assert fixed_info["split_manifest"]["locked_oos_start"] == manifest["locked_oos_start"]
    assert dynamic_manifest["train_end"] != manifest["train_end"]
    assert {obs.observation_id for obs in older_observations} <= {
        obs.observation_id for obs in fixed_train
    }


def test_random_baseline_is_seeded_and_symbol_constrained() -> None:
    bars = _bars() + _bars(symbol="6758", start_close=2000)
    events = [
        _event(_raw(Code="72030", DiscNo="fixture-7203"), bars),
        _event(_raw(Code="67580", DiscNo="fixture-6758"), bars),
    ]
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    random_dates = event_research.build_random_date_observations(ohlcv_rows=bars)

    first = event_research.random_baselines(
        observations,
        seed_count=5,
        random_date_observations=random_dates,
    )
    second = event_research.random_baselines(
        observations,
        seed_count=5,
        random_date_observations=random_dates,
    )
    sample = event_research.sample_random_baseline(
        observations,
        name="same_symbol_random_date",
        rng=random.Random(1),
    )

    assert first == second
    assert [obs.symbol for obs in sample] == [obs.symbol for obs in observations]
    assert 0 <= first["same_symbol_random_date"]["selected_percentile"] <= 1
    assert 0 <= first["same_symbol_random_event_date"]["selected_percentile"] <= 1


def test_evaluation_rows_include_matched_random_percentiles() -> None:
    bars = _bars() + _bars(symbol="6758", start_close=2000)
    events = [
        _event(_raw(Code="72030", DiscNo="fixture-7203"), bars),
        _event(_raw(Code="67580", DiscNo="fixture-6758"), bars),
    ]
    observations = event_research.build_observations(events, ohlcv_rows=bars)
    random_dates = event_research.build_random_date_observations(ohlcv_rows=bars)

    first = event_research.evaluate_observations(
        observations,
        random_seed_count=3,
        random_date_observations=random_dates,
    )
    second = event_research.evaluate_observations(
        observations,
        random_seed_count=3,
        random_date_observations=random_dates,
    )
    row = next(
        item
        for item in first["rows"]
        if item["entry_arm"] == EntryArm.EVENT_ONLY.value
        and item["exit_arm"] == ExitArm.FIXED_10D.value
    )

    assert first == second
    assert len(first["row_random_baselines"]) == len(first["rows"])
    assert row["random_baselines"]["same_symbol_random_date"]["random_count"] == 3
    assert row["random_baselines"]["same_symbol_random_event_date"]["random_count"] == 3
    assert 0 <= row["same_symbol_random_date_percentile"] <= 1
    assert first["random_baseline_coverage"]["same_symbol_random_date"]["matched"] > 0
