from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_contracts.event_research import (
    EventType,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
    ValuationFeaturesV0,
)


def _load_module():
    path = Path(__file__).resolve().parents[1] / "simulate-event-portfolio.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("simulate_event_portfolio", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


simulate_event_portfolio = _load_module()


def test_random_candidate_uses_same_frozen_catastrophic_stop() -> None:
    bar_date = date(2026, 1, 6)
    bar = simulate_event_portfolio.OhlcvRow(
        symbol="7203",
        date=bar_date,
        open=Decimal("95"),
        high=Decimal("100"),
        low=Decimal("89"),
        close=Decimal("96"),
        volume=1000,
        turnover=Decimal("96000"),
    )

    exit_date, exit_price = simulate_event_portfolio.catastrophic_stop_from_bars(
        [bar],
        entry_price=Decimal("100"),
        fixed_exit_date=date(2026, 1, 30),
        fixed_exit_price=Decimal("110"),
    )

    assert exit_date == bar_date
    assert exit_price == Decimal("90")


def _observation(
    idx: int,
    *,
    entry_date: str,
    exit_date: str,
    entry_price: Decimal = Decimal("1000"),
    exit_price: Decimal = Decimal("1100"),
    forecast_per: Decimal | None = Decimal("12"),
) -> ObservationRecord:
    at = datetime(2026, 1, 1, 15, 30, tzinfo=UTC) + timedelta(days=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=f"cluster-{idx}",
        trade_group_id=f"trade-{idx}",
        symbol=f"72{idx:02d}",
        event_type=EventType.FORECAST_REVISION,
        event_subtype="ForecastRevision_Consolidated_JP",
        signal_date=entry_date,
        entry_date=entry_date,
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=entry_price,
        valuation_price=entry_price,
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=Decimal("0.20"), valid=True),
            operating_profit_revision_pct=FeatureValue(value=Decimal("0.20"), valid=True),
            forecast_eps_revision_absolute=FeatureValue(value=Decimal("10"), valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value=forecast_per, valid=forecast_per is not None),
            forecast_per_valid=forecast_per is not None,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000, valid=True),
            atr_pct_14d=FeatureValue(value=Decimal("0.02"), valid=True),
            return_20d=FeatureValue(value=Decimal("0.05"), valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_5d": float((exit_price / entry_price) - Decimal("1")),
            "exit_date_5d": exit_date,
            "exit_price_5d": str(exit_price),
        },
    )


def _write_jsonl(path: Path, rows: list[ObservationRecord]) -> None:
    path.write_text("\n".join(row.model_dump_json() for row in rows) + "\n", encoding="utf-8")


def _write_ohlcv_csv(path: Path, *, symbol: str = "7201") -> None:
    lines = ["symbol,date,open,high,low,close,volume,turnover"]
    start = datetime(2025, 12, 20, tzinfo=UTC).date()
    for idx in range(30):
        day = start + timedelta(days=idx)
        price = Decimal("900") + Decimal(idx * 5)
        lines.append(
            ",".join(
                [
                    symbol,
                    day.isoformat(),
                    str(price),
                    str(price + Decimal("10")),
                    str(price - Decimal("10")),
                    str(price + Decimal("3")),
                    "1000000",
                    str((price + Decimal("3")) * Decimal("1000000")),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_portfolio_simulation_does_not_reuse_same_day_close_exit_cash() -> None:
    observations = [
        _observation(1, entry_date="2026-01-02", exit_date="2026-01-07"),
        _observation(2, entry_date="2026-01-07", exit_date="2026-01-12"),
    ]
    result = simulate_event_portfolio.simulate_portfolio(
        observations,
        params=simulate_event_portfolio.PortfolioParams(
            capital=Decimal("200000"),
            max_positions=1,
            max_notional_per_position_pct=Decimal("1.0"),
            lot_size=100,
        ),
    )

    assert result.opened_trade_count == 1
    assert result.skipped_position_cap_count == 1
    assert result.trades[0].entry_date == "2026-01-02"
    assert result.trades[0].exit_date == "2026-01-07"


def test_portfolio_simulation_applies_adverse_slippage_stress() -> None:
    observations = [
        _observation(
            1,
            entry_date="2026-01-02",
            exit_date="2026-01-07",
            entry_price=Decimal("1000"),
            exit_price=Decimal("1100"),
        ),
    ]
    result = simulate_event_portfolio.simulate_portfolio(
        observations,
        params=simulate_event_portfolio.PortfolioParams(
            capital=Decimal("200000"),
            max_positions=1,
            max_notional_per_position_pct=Decimal("1.0"),
            lot_size=100,
            entry_additional_slippage_bps=Decimal("100"),
            exit_additional_slippage_bps=Decimal("100"),
        ),
    )

    assert result.opened_trade_count == 1
    assert result.params["entry_additional_slippage_bps"] == 100.0
    assert result.params["exit_additional_slippage_bps"] == 100.0
    assert result.trades[0].entry_price == "1010.00"
    assert result.trades[0].exit_price == "1089.00"


def test_cluster_value_guard_allows_missing_or_low_forecast_per() -> None:
    low = _observation(
        1,
        entry_date="2026-01-02",
        exit_date="2026-01-22",
        forecast_per=Decimal("15"),
    )
    missing = _observation(
        2,
        entry_date="2026-01-02",
        exit_date="2026-01-22",
        forecast_per=None,
    )
    expensive = _observation(
        3,
        entry_date="2026-01-02",
        exit_date="2026-01-22",
        forecast_per=Decimal("16"),
    )

    assert simulate_event_portfolio.cluster_forecast_per_missing_or_lte(
        [low],
        Decimal("15"),
    )
    assert simulate_event_portfolio.cluster_forecast_per_missing_or_lte(
        [missing],
        Decimal("15"),
    )
    assert not simulate_event_portfolio.cluster_forecast_per_missing_or_lte(
        [expensive],
        Decimal("15"),
    )


def test_portfolio_simulation_cli_writes_summary(tmp_path: Path, monkeypatch) -> None:
    observations_path = tmp_path / "observations.jsonl"
    output_json = tmp_path / "portfolio.json"
    output_csv = tmp_path / "portfolio.csv"
    _write_jsonl(
        observations_path,
        [_observation(1, entry_date="2026-01-02", exit_date="2026-01-07")],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "simulate-event-portfolio.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
            "--capital",
            "200000",
            "--max-notional-per-position-pct",
            "1.0",
        ],
    )

    assert simulate_event_portfolio.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["candidate_id"] == simulate_event_portfolio.CANDIDATE_ID
    assert report["paper_live_enabled"] is False
    assert report["results"][0]["opened_trade_count"] == 1
    assert output_csv.exists()


def test_portfolio_simulation_cli_writes_random_and_order_stress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observations_path = tmp_path / "observations.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "portfolio-random.json"
    output_csv = tmp_path / "portfolio-random.csv"
    _write_jsonl(
        observations_path,
        [_observation(1, entry_date="2026-01-02", exit_date="2026-01-07")],
    )
    _write_ohlcv_csv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "simulate-event-portfolio.py",
            "--observations",
            str(observations_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
            "--capital",
            "200000",
            "--max-notional-per-position-pct",
            "1.0",
            "--ohlcv",
            str(ohlcv_path),
            "--random-seeds",
            "3",
            "--include-selection-order-stress",
        ],
    )

    assert simulate_event_portfolio.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    random_report = report["portfolio_random_baselines"]
    assert random_report["enabled"] is True
    assert random_report["coverage"]["matched"] == 1
    assert random_report["by_capital"]["200000"]["random_count"] == 3
    assert report["selection_order_stress"]["enabled"] is True
    assert "entry_price_desc" in report["selection_order_stress"]["orders"]
