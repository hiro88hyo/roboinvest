from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(StrEnum):
    FORECAST_REVISION = "forecast_revision"
    DIVIDEND_REVISION = "dividend_revision"
    EARNINGS_RESULT = "earnings_result"
    BUYBACK_ANNOUNCEMENT = "buyback_announcement"


class EventSource(StrEnum):
    JQUANTS_FINS_SUMMARY = "jquants_fins_summary"
    TDNET_ARCHIVE = "tdnet_archive"
    FIXTURE = "fixture"


class ExecutionMode(StrEnum):
    NEXT_OPEN_UNCONDITIONAL = "next_open_unconditional"
    NEXT_0915_CONDITIONAL = "next_0915_conditional"


class EntryArm(StrEnum):
    EVENT_ONLY = "event_only"
    EVENT_PLUS_FUNDAMENTAL = "event_plus_fundamental"
    EVENT_PLUS_TECHNICAL = "event_plus_technical"
    EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL = "event_plus_fundamental_plus_technical"
    EVENT_PLUS_AI = "event_plus_ai"
    EVENT_PLUS_AI_PLUS_FUNDAMENTAL = "event_plus_ai_plus_fundamental"
    EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL = "event_plus_ai_plus_fundamental_plus_technical"


class ExitArm(StrEnum):
    FIXED_2D = "fixed_2d"
    FIXED_5D = "fixed_5d"
    FIXED_10D = "fixed_10d"
    FIXED_20D = "fixed_20d"
    FIXED_10D_PLUS_CATASTROPHIC_STOP = "fixed_10d_plus_catastrophic_stop"
    FIXED_20D_PLUS_CATASTROPHIC_STOP = "fixed_20d_plus_catastrophic_stop"


class FeatureValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    valid: bool = False
    source_disclosed_at: datetime | None = None
    available_at: datetime | None = None
    feature_cutoff_at: datetime | None = None
    age_days: int | None = None
    source_record_id: str | None = None


class FundamentalFeaturesV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eps_latest: FeatureValue = Field(default_factory=FeatureValue)
    eps_ttm: FeatureValue = Field(default_factory=FeatureValue)
    previous_forecast_eps: FeatureValue = Field(default_factory=FeatureValue)
    revised_forecast_eps: FeatureValue = Field(default_factory=FeatureValue)
    forecast_eps_revision_absolute: FeatureValue = Field(default_factory=FeatureValue)
    forecast_eps_revision_pct: FeatureValue = Field(default_factory=FeatureValue)
    operating_profit_revision_absolute: FeatureValue = Field(default_factory=FeatureValue)
    operating_profit_revision_pct: FeatureValue = Field(default_factory=FeatureValue)
    profit_revision_absolute: FeatureValue = Field(default_factory=FeatureValue)
    profit_revision_pct: FeatureValue = Field(default_factory=FeatureValue)
    sales_revision_pct: FeatureValue = Field(default_factory=FeatureValue)
    eps_growth_yoy: FeatureValue = Field(default_factory=FeatureValue)
    is_loss_to_profit: FeatureValue = Field(default_factory=FeatureValue)
    is_profit_to_loss: FeatureValue = Field(default_factory=FeatureValue)
    is_one_off_profit_suspected: FeatureValue = Field(default_factory=FeatureValue)
    accounting_standard: FeatureValue = Field(default_factory=FeatureValue)
    fundamental_data_age_days: FeatureValue = Field(default_factory=FeatureValue)
    revision_pct_valid: bool = False
    previous_eps_near_zero: bool = False
    sign_changed: bool = False
    negative_eps: bool = False
    missing_eps: bool = False


class ValuationFeaturesV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trailing_per: FeatureValue = Field(default_factory=FeatureValue)
    forecast_per: FeatureValue = Field(default_factory=FeatureValue)
    earnings_yield: FeatureValue = Field(default_factory=FeatureValue)
    bps: FeatureValue = Field(default_factory=FeatureValue)
    pbr: FeatureValue = Field(default_factory=FeatureValue)
    roe: FeatureValue = Field(default_factory=FeatureValue)
    forecast_dividend_per_share: FeatureValue = Field(default_factory=FeatureValue)
    forecast_dividend_yield: FeatureValue = Field(default_factory=FeatureValue)
    payout_ratio: FeatureValue = Field(default_factory=FeatureValue)
    sector_forecast_per_median: FeatureValue = Field(default_factory=FeatureValue)
    sector_relative_forecast_per: FeatureValue = Field(default_factory=FeatureValue)
    sector_pbr_median: FeatureValue = Field(default_factory=FeatureValue)
    sector_relative_pbr: FeatureValue = Field(default_factory=FeatureValue)
    sector_earnings_yield_rank: FeatureValue = Field(default_factory=FeatureValue)
    own_forecast_per_percentile_if_history_available: FeatureValue = Field(
        default_factory=FeatureValue
    )
    trailing_per_valid: bool = False
    forecast_per_valid: bool = False
    pbr_valid: bool = False
    dividend_yield_valid: bool = False


class TechnicalContextV0(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_5d: FeatureValue = Field(default_factory=FeatureValue)
    return_20d: FeatureValue = Field(default_factory=FeatureValue)
    return_60d: FeatureValue = Field(default_factory=FeatureValue)
    distance_from_sma20: FeatureValue = Field(default_factory=FeatureValue)
    distance_from_sma60: FeatureValue = Field(default_factory=FeatureValue)
    sma20_slope: FeatureValue = Field(default_factory=FeatureValue)
    sma60_slope: FeatureValue = Field(default_factory=FeatureValue)
    atr_pct_14d: FeatureValue = Field(default_factory=FeatureValue)
    realized_volatility_20d: FeatureValue = Field(default_factory=FeatureValue)
    volume_ratio_20d: FeatureValue = Field(default_factory=FeatureValue)
    avg_turnover_20d: FeatureValue = Field(default_factory=FeatureValue)
    lot_notional: FeatureValue = Field(default_factory=FeatureValue)
    distance_from_high20: FeatureValue = Field(default_factory=FeatureValue)
    pre_event_gap_history: FeatureValue = Field(default_factory=FeatureValue)
    sector_relative_return_20d: FeatureValue = Field(default_factory=FeatureValue)
    topix_return_20d: FeatureValue = Field(default_factory=FeatureValue)
    market_regime: FeatureValue = Field(default_factory=FeatureValue)


class EventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_cluster_id: str
    symbol: str
    source: EventSource
    raw_document_type: str
    event_type: EventType
    event_subtype: str | None = None
    disclosed_date: str
    disclosed_time: str | None = None
    disclosed_at: datetime
    data_available_at: datetime
    signal_date: str
    entry_date: str
    feature_cutoff_at: datetime
    raw_source_identifier: str
    fetched_at: datetime
    cluster_member_count: int = 1
    raw: dict[str, Any] = Field(default_factory=dict)


class ObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    event_id: str
    event_cluster_id: str
    symbol: str
    sector: str | None = None
    event_type: EventType
    execution_mode: ExecutionMode = ExecutionMode.NEXT_OPEN_UNCONDITIONAL
    signal_date: str
    entry_date: str
    feature_cutoff_at: datetime
    data_available_at: datetime
    entry_price: Decimal | None = None
    valuation_price: Decimal | None = None
    source_record_id: str
    fundamental_features_v0: FundamentalFeaturesV0 = Field(default_factory=FundamentalFeaturesV0)
    valuation_features_v0: ValuationFeaturesV0 = Field(default_factory=ValuationFeaturesV0)
    technical_context_v0: TechnicalContextV0 = Field(default_factory=TechnicalContextV0)
    labels: dict[str, Any] = Field(default_factory=dict)


class EventAiLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    fundamental_direction: Literal["positive", "negative", "mixed", "neutral", "unclear"]
    fundamental_strength: int = Field(ge=0, le=3)
    revision_quality: Literal["high", "medium", "low", "unclear"]
    valuation_context: Literal["cheap", "fair", "expensive", "invalid", "unclear"]
    technical_context: Literal["favorable", "neutral", "extended", "high_risk", "unclear"]
    expected_horizon: Literal["2d", "5d", "10d", "20d", "avoid", "unclear"]
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("confidence")
    @classmethod
    def _confidence_is_finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("confidence must be finite")
        return value


class EventAiJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    event_id: str
    prompt_version: str
    prompt_hash: str
    prompt: str
    feature_schema_version: str
    feature_cutoff_at: datetime
    model_provider: str
    model_id: str
    temperature: Decimal
    seed: int | None = None
    created_at: datetime


class EventAiLabeledRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    event_id: str
    prompt_hash: str
    model_provider: str
    model_id: str
    raw_response: str
    label: EventAiLabel
    created_at: datetime
