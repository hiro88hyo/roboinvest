from __future__ import annotations

import importlib.util
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
    path = Path(__file__).resolve().parents[1] / "screen-event-prospective-high-frequency.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(
        "screen_event_prospective_high_frequency",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


screen = _load_module()


def _observation(
    idx: int,
    *,
    event_type: EventType,
    event_subtype: str | None = None,
    trade_group_id: str = "group-1",
    forecast_per: Decimal | None = Decimal("12"),
    dividend_yield: Decimal | None = None,
    profit_revision: Decimal = Decimal("0.10"),
) -> ObservationRecord:
    at = datetime(2026, 1, 5, 6, 30, tzinfo=UTC) + timedelta(seconds=idx)
    return ObservationRecord(
        observation_id=f"obs-{idx}",
        event_id=f"event-{idx}",
        event_cluster_id=trade_group_id,
        trade_group_id=trade_group_id,
        symbol="7203",
        event_type=event_type,
        event_subtype=event_subtype,
        signal_date="2026-01-05",
        entry_date="2026-01-06",
        feature_cutoff_at=at,
        data_available_at=at,
        entry_price=Decimal("1000"),
        valuation_price=Decimal("1000"),
        source_record_id=f"source-{idx}",
        fundamental_features_v0=FundamentalFeaturesV0(
            revised_forecast_eps=FeatureValue(value="100", valid=True),
            profit_revision_pct=FeatureValue(value=profit_revision, valid=True),
            operating_profit_revision_pct=FeatureValue(value="0.08", valid=True),
            forecast_eps_revision_absolute=FeatureValue(value="10", valid=True),
        ),
        valuation_features_v0=ValuationFeaturesV0(
            forecast_per=FeatureValue(value=forecast_per, valid=forecast_per is not None),
            forecast_dividend_yield=FeatureValue(
                value=dividend_yield,
                valid=dividend_yield is not None,
            ),
            forecast_per_valid=forecast_per is not None,
            dividend_yield_valid=dividend_yield is not None,
        ),
        technical_context_v0=TechnicalContextV0(
            avg_turnover_20d=FeatureValue(value="300000000", valid=True),
            atr_pct_14d=FeatureValue(value="0.03", valid=True),
            return_20d=FeatureValue(value="0.05", valid=True),
            market_regime=FeatureValue(value="broad_uptrend", valid=True),
        ),
        labels={
            "forward_return_2d": 0.02,
            "exit_date_2d": "2026-01-08",
            "exit_price_2d": "1020",
        },
    )


def test_quality_tiers_are_deterministic() -> None:
    forecast = _observation(1, event_type=EventType.FORECAST_REVISION)
    dividend = _observation(
        2,
        event_type=EventType.DIVIDEND_REVISION,
        event_subtype="increase",
        forecast_per=None,
        dividend_yield=Decimal("0.03"),
    )
    other = _observation(3, event_type=EventType.EARNINGS_RESULT)

    assert screen.quality_tier(forecast) == 0
    assert screen.quality_tier(dividend) == 2
    assert screen.quality_tier(other) == 3


def test_quality_priority_selects_best_member_of_trade_group() -> None:
    other = _observation(1, event_type=EventType.EARNINGS_RESULT)
    forecast = _observation(2, event_type=EventType.FORECAST_REVISION)
    groups = {"group-1": [other, forecast]}

    feature_time = screen.selected_for_variant(
        groups,
        variant="broad_feature_time_fixed2",
    )
    priority = screen.selected_for_variant(
        groups,
        variant="broad_quality_priority_fixed2",
    )

    assert feature_time == [other]
    assert priority == [forecast]


def test_tier_filter_excludes_fallback_only_group() -> None:
    other = _observation(1, event_type=EventType.EARNINGS_RESULT)

    assert (
        screen.selected_for_variant(
            {"group-1": [other]},
            variant="quality_tiers_0_2_fixed2",
        )
        == []
    )
