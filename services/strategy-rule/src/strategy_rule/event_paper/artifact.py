"""Strict validation for causal event-paper candidate artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

import jpholiday
from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_ARTIFACT_SCHEMA_VERSION = 3
EVENT_STRATEGY_KEY = "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
EVENT_STOP_LOSS_PCT = Decimal("0.10")
EVENT_MAX_HOLD_DAYS = 20
JST = ZoneInfo("Asia/Tokyo")
DAILY_BAR_AVAILABLE_TIME_JST = time(15, 30)


def _is_tse_business_day(value: date) -> bool:
    if value.weekday() >= 5 or jpholiday.is_holiday(value):
        return False
    if value.month == 12 and value.day == 31:
        return False
    return not (value.month == 1 and value.day <= 3)


def _next_tse_business_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while not _is_tse_business_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def _previous_tse_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not _is_tse_business_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _required_ohlcv_session_date(feature_cutoff_at: datetime) -> date:
    if feature_cutoff_at.tzinfo is None:
        raise ValueError("feature_cutoff_at must be timezone-aware")
    cutoff_date = feature_cutoff_at.astimezone(JST).date()
    same_day_available_at = datetime.combine(
        cutoff_date,
        DAILY_BAR_AVAILABLE_TIME_JST,
        tzinfo=JST,
    )
    if _is_tse_business_day(cutoff_date) and feature_cutoff_at >= same_day_available_at:
        return cutoff_date
    return _previous_tse_business_day(cutoff_date)


class EventArtifactError(RuntimeError):
    """Raised when a detector artifact is not safe to publish."""


class EventPaperCausality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_features_use_forward_bars: bool
    candidate_artifact_contains_entry_price: bool
    entry_date_source: str
    data_receipt_checked: bool
    receipt_provenance: str
    fetch_completion_verified: bool
    source_coverage_window_verified: bool
    paper_publish_disabled: bool


class EventPaperRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_contains: list[str]
    forecast_per_threshold: Decimal
    missing_forecast_per: str
    max_hold_days: int
    catastrophic_stop_pct: Decimal


class EventPaperSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    late_data_receipt_count: int = Field(ge=0)
    fetched_before_disclosure_count: int = Field(ge=0)
    missing_required_ohlcv_session_count: int = Field(ge=0)
    missing_feature_history_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    published_count: int = Field(ge=0)


class EventPaperCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # ``candidate_id`` is retained for artifact compatibility and is the
    # strategy definition. ``execution_candidate_id`` is the occurrence ID
    # carried by StrategySignal.
    candidate_id: str
    execution_candidate_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    symbol: str = Field(min_length=1)
    symbol_name: str
    signal_date: date
    entry_date: date
    feature_cutoff_at: datetime
    data_available_at: datetime
    source_received_at: datetime
    feature_data_complete: bool
    selection_status: Literal["eligible", "incomplete_required_ohlcv_session"]
    required_ohlcv_session_date: date
    valuation_reference_price: Decimal | None
    valuation_reference_bar_date: date | None
    valuation_reference_available_at: datetime | None
    entry_price_status: Literal["unresolved_until_fresh_market_observation"]
    catastrophic_stop_pct: Decimal
    max_hold_days: int
    min_forecast_per: Decimal | None
    has_earnings_result: bool
    has_dividend_increase: bool
    publish_ready: bool

    @model_validator(mode="after")
    def validate_frozen_candidate(self) -> Self:
        if self.candidate_id != EVENT_STRATEGY_KEY:
            raise ValueError("candidate_id does not match the frozen strategy")
        expected_occurrence = f"{self.cluster_id}:{self.observation_id}"
        if self.execution_candidate_id != expected_occurrence:
            raise ValueError("execution_candidate_id must identify the cluster occurrence")
        if self.catastrophic_stop_pct != -EVENT_STOP_LOSS_PCT:
            raise ValueError("catastrophic_stop_pct drifted from -0.10")
        if self.max_hold_days != EVENT_MAX_HOLD_DAYS:
            raise ValueError("max_hold_days drifted from 20")
        if not self.has_earnings_result or not self.has_dividend_increase:
            raise ValueError("candidate does not contain the frozen event cluster")
        if self.min_forecast_per is not None and self.min_forecast_per > Decimal("15"):
            raise ValueError("candidate forecast PER exceeds the frozen threshold")
        if self.event_id not in self.event_ids or len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("candidate event lineage is inconsistent")
        if self.entry_date <= self.signal_date:
            raise ValueError("candidate entry_date must follow signal_date")
        if self.publish_ready:
            raise ValueError("detector artifacts must remain non-executable")
        for value in (
            self.feature_cutoff_at,
            self.data_available_at,
            self.source_received_at,
        ):
            if value.tzinfo is None:
                raise ValueError("candidate timing lineage must be timezone-aware")
        if self.data_available_at != self.feature_cutoff_at:
            raise ValueError("candidate feature cutoff differs from frozen data availability")
        if self.feature_cutoff_at.astimezone(JST).date() != self.signal_date:
            raise ValueError("candidate feature cutoff date differs from signal_date")
        if self.feature_cutoff_at > self.source_received_at:
            raise ValueError("candidate feature vintage postdates source receipt")
        expected_required_session = _required_ohlcv_session_date(self.feature_cutoff_at)
        if self.required_ohlcv_session_date != expected_required_session:
            raise ValueError("required OHLCV session differs from the feature cutoff")
        if self.feature_data_complete:
            if self.selection_status != "eligible":
                raise ValueError("complete candidate has an incomplete selection status")
            if (
                self.valuation_reference_price is None
                or self.valuation_reference_bar_date is None
                or self.valuation_reference_available_at is None
            ):
                raise ValueError("complete candidate is missing its required valuation reference")
            if self.valuation_reference_bar_date != self.required_ohlcv_session_date:
                raise ValueError("valuation reference does not match the required OHLCV session")
            if self.valuation_reference_available_at.tzinfo is None:
                raise ValueError("valuation availability must be timezone-aware")
            if self.valuation_reference_available_at > self.feature_cutoff_at:
                raise ValueError("valuation reference was unavailable at the feature cutoff")
            expected_reference_available_at = datetime.combine(
                self.valuation_reference_bar_date,
                DAILY_BAR_AVAILABLE_TIME_JST,
                tzinfo=JST,
            )
            if self.valuation_reference_available_at != expected_reference_available_at:
                raise ValueError("valuation reference availability does not match its bar date")
        else:
            if self.selection_status != "incomplete_required_ohlcv_session":
                raise ValueError("incomplete candidate has an unsafe selection status")
            if any(
                value is not None
                for value in (
                    self.valuation_reference_price,
                    self.valuation_reference_bar_date,
                    self.valuation_reference_available_at,
                    self.min_forecast_per,
                )
            ):
                raise ValueError(
                    "incomplete candidate must not retain an older valuation reference"
                )
        return self


class EventPaperArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    strategy_key: str
    candidate_id: str
    mode: Literal["dry_run"]
    paper_live_enabled: bool
    paper_publish_enabled: bool
    publish_enabled: bool
    causality_verified: bool
    causality: EventPaperCausality
    signal_date: date
    fetched_at: datetime
    rule: EventPaperRule
    summary: EventPaperSummary
    candidates: list[EventPaperCandidate]
    exclusions: list[dict[str, Any]]
    published: list[dict[str, Any]]

    @model_validator(mode="after")
    def validate_publish_input(self) -> Self:
        if self.schema_version != EVENT_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported event artifact schema_version")
        if self.strategy_key != EVENT_STRATEGY_KEY or self.candidate_id != EVENT_STRATEGY_KEY:
            raise ValueError("artifact strategy identity mismatch")
        if self.paper_live_enabled or self.paper_publish_enabled or self.publish_enabled:
            raise ValueError("detector artifact must not already be publish-enabled")
        if not self.causality_verified:
            raise ValueError("artifact causality is not verified")
        causality = self.causality
        if (
            causality.candidate_features_use_forward_bars
            or causality.candidate_artifact_contains_entry_price
            or causality.entry_date_source != "tse_business_calendar"
            or not causality.data_receipt_checked
            or causality.receipt_provenance != "export_metadata"
            or not causality.fetch_completion_verified
            or not causality.source_coverage_window_verified
            or not causality.paper_publish_disabled
        ):
            raise ValueError("artifact causality metadata is unsafe")
        if self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        if not _is_tse_business_day(self.signal_date):
            raise ValueError("artifact signal_date is not a TSE business day")
        coverage_start = datetime.combine(
            self.signal_date + timedelta(days=1),
            time(0, 0),
            tzinfo=JST,
        )
        expected_entry_date = _next_tse_business_day(self.signal_date)
        if self.rule.cluster_contains != ["earnings_result", "dividend_revision:increase"]:
            raise ValueError("event cluster definition drifted")
        if self.rule.forecast_per_threshold != Decimal("15"):
            raise ValueError("forecast PER threshold drifted")
        if self.rule.missing_forecast_per != "allowed":
            raise ValueError("missing forecast PER policy drifted")
        if self.rule.max_hold_days != EVENT_MAX_HOLD_DAYS:
            raise ValueError("artifact max_hold_days drifted")
        if self.rule.catastrophic_stop_pct != -EVENT_STOP_LOSS_PCT:
            raise ValueError("artifact catastrophic stop drifted")
        if self.summary.candidate_count != len(self.candidates):
            raise ValueError("candidate count does not match candidate rows")
        incomplete_count = sum(not row.feature_data_complete for row in self.candidates)
        if self.summary.missing_required_ohlcv_session_count != incomplete_count:
            raise ValueError("incomplete feature-data count does not match candidate rows")
        if self.summary.exclusion_count != len(self.exclusions):
            raise ValueError("exclusion count does not match exclusion rows")
        if self.summary.published_count != 0 or self.published:
            raise ValueError("detector artifact unexpectedly contains published rows")
        occurrence_ids = [row.execution_candidate_id for row in self.candidates]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("duplicate execution_candidate_id")
        symbols = [row.symbol for row in self.candidates]
        if len(symbols) != len(set(symbols)):
            raise ValueError("multiple event candidates for one symbol are unsupported")
        for row in self.candidates:
            if row.signal_date != self.signal_date:
                raise ValueError("candidate signal_date differs from artifact")
            if row.entry_date != expected_entry_date:
                raise ValueError("candidate entry_date is not the next TSE business day")
            if row.source_received_at != self.fetched_at:
                raise ValueError("candidate source receipt differs from completed fetch")
            entry_cutoff = datetime.combine(
                row.entry_date,
                time(9, 0),
                tzinfo=JST,
            )
            if not coverage_start <= self.fetched_at.astimezone(JST) < entry_cutoff:
                raise ValueError("artifact fetch is outside the causal coverage window")
        return self

    def validate_target_date(self, target_date: date) -> None:
        if not self.candidates:
            raise EventArtifactError("artifact contains no candidates to publish")
        incomplete = [row.symbol for row in self.candidates if not row.feature_data_complete]
        if incomplete:
            raise EventArtifactError(
                f"candidate feature data is incomplete for execution: {','.join(incomplete)}"
            )
        bad = [row.symbol for row in self.candidates if row.entry_date != target_date]
        if bad:
            raise EventArtifactError(
                f"candidate entry_date does not match target_date: {','.join(bad)}"
            )


@dataclass(frozen=True, slots=True)
class LoadedEventPaperArtifact:
    artifact: EventPaperArtifact
    sha256: str
    source_path: Path


def load_event_paper_artifact(path: Path) -> LoadedEventPaperArtifact:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EventArtifactError(f"cannot read candidate artifact: {exc}") from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventArtifactError(f"candidate artifact is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EventArtifactError("candidate artifact must be a JSON object")
    try:
        artifact = EventPaperArtifact.model_validate(payload)
    except ValueError as exc:
        raise EventArtifactError(f"unsafe candidate artifact: {exc}") from exc
    return LoadedEventPaperArtifact(
        artifact=artifact,
        sha256=hashlib.sha256(raw).hexdigest(),
        source_path=path,
    )
