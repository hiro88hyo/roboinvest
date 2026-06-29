from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
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
    path = Path(__file__).resolve().parents[1] / "summarize-event-cluster-portfolio-stability.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("summarize_event_cluster_stability", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


summarize_event_cluster_stability = _load_module()


def _observation(
    idx: int,
    *,
    event_type: EventType,
    subtype: str | None,
    trade_group: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
) -> ObservationRecord:
    at = datetime.fromisoformat(f"{signal_date}T06:30:00+00:00") + timedelta(minutes=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=trade_group,
        trade_group_id=trade_group,
        symbol="7203",
        event_type=event_type,
        event_subtype=subtype,
        signal_date=signal_date,
        entry_date=entry_date,
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("990"),
        source_record_id=f"fixture-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            profit_revision_pct=FeatureValue(value=Decimal("0.10"), valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value=300_000_000, valid=True),
            atr_pct_14d=FeatureValue(value=Decimal("0.02"), valid=True),
            return_20d=FeatureValue(value=Decimal("0.05"), valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_20d": 0.08,
            "exit_date_20d": exit_date,
            "exit_price_20d": "1080",
            "catastrophic_stop_return_20d": 0.08,
            "catastrophic_stop_exit_date_20d": exit_date,
        },
    )


def _write_ohlcv_csv(path: Path) -> None:
    lines = ["symbol,date,open,high,low,close,volume,turnover"]
    start = datetime(2025, 12, 20, tzinfo=UTC).date()
    for idx in range(80):
        day = start + timedelta(days=idx)
        price = Decimal("900") + Decimal(idx)
        lines.append(
            ",".join(
                [
                    "7203",
                    day.isoformat(),
                    str(price),
                    str(price + Decimal("10")),
                    str(price - Decimal("10")),
                    str(price + Decimal("2")),
                    "1000000",
                    str((price + Decimal("2")) * Decimal("1000000")),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_event_cluster_portfolio_stability_cli(tmp_path: Path, monkeypatch) -> None:
    observations_path = tmp_path / "observations.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "stability.json"
    output_csv = tmp_path / "stability.csv"
    rows = [
        _observation(
            0,
            event_type=EventType.EARNINGS_RESULT,
            subtype=None,
            trade_group="trade-a",
            signal_date="2026-01-02",
            entry_date="2026-01-03",
            exit_date="2026-01-23",
        ),
        _observation(
            1,
            event_type=EventType.DIVIDEND_REVISION,
            subtype="increase",
            trade_group="trade-a",
            signal_date="2026-01-02",
            entry_date="2026-01-03",
            exit_date="2026-01-23",
        ),
    ]
    observations_path.write_text(
        "\n".join(row.model_dump_json() for row in rows) + "\n",
        encoding="utf-8",
    )
    _write_ohlcv_csv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize-event-cluster-portfolio-stability.py",
            "--observations",
            str(observations_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--split",
            "all",
            "--include-locked-oos",
            "--block-trading-days",
            "20",
            "--capital",
            "200000",
            "--max-notional-per-position-pct",
            "1.0",
            "--random-seeds",
            "2",
        ],
    )

    assert summarize_event_cluster_stability.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["paper_live_enabled"] is False
    assert report["selected_candidate_count"] == 1
    assert report["summary"][0]["active_block_count"] == 1
    assert report["rows"][0]["opened_trade_count"] == 1
    assert report["rows"][0]["random_seed_count"] == 2
    assert output_csv.exists()
