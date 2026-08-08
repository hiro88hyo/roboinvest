from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trade_contracts.event_research import (
    EntryArm,
    EventRecord,
    EventSource,
    EventType,
    ExecutionMode,
    ExitArm,
    FeatureValue,
    FundamentalFeaturesV0,
    ObservationRecord,
    TechnicalContextV0,
    ValuationFeaturesV0,
)
from universe_scanner.calendar import is_tse_business_day, next_business_day, previous_business_day

FEATURE_SCHEMA_VERSION = "event_research_v0"
PURGE_TRADING_DAYS = 20
TOKYO = ZoneInfo("Asia/Tokyo")
DAILY_BAR_AVAILABLE_TIME_JST = time(15, 30)
DEFAULT_CAPITAL = Decimal("1000000")
DEFAULT_TRADE_NOTIONAL = Decimal("100000")
ROUND_TRIP_COST_RATE = Decimal("0.00298")
CAT_STOP_PCT = Decimal("-0.10")
EXIT_ARMS_FOR_REPORT = (
    ExitArm.FIXED_2D,
    ExitArm.FIXED_5D,
    ExitArm.FIXED_10D,
    ExitArm.FIXED_20D,
    ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP,
    ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP,
)
RANDOM_BASELINE_NAMES = (
    "same_symbol_random_date",
    "same_symbol_random_event_date",
    "same_symbol_same_month_random_date",
    "same_symbol_same_regime_random_date",
    "same_sector_same_date_random",
    "event_type_matched_random",
)
EVALUATION_SPLITS = (
    "development",
    "train",
    "validation",
    "locked-oos",
    "all",
)


@dataclass(frozen=True, slots=True)
class OhlcvRow:
    symbol: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    turnover: Decimal


@dataclass(frozen=True, slots=True)
class MasterRow:
    symbol: str
    symbol_name: str = ""
    sector: str | None = None


@dataclass(frozen=True, slots=True)
class ForecastSnapshot:
    source_record_id: str
    available_at: datetime
    eps: Decimal | None
    operating_profit: Decimal | None
    profit: Decimal | None
    sales: Decimal | None
    dividend: Decimal | None


@dataclass(frozen=True, slots=True)
class _RegimeFeature:
    value: str


@dataclass(frozen=True, slots=True)
class _RandomDateTechnicalContext:
    symbol_regime: _RegimeFeature
    market_regime: _RegimeFeature


@dataclass(frozen=True, slots=True)
class _RandomDateLabels:
    forward_return_2d: float | None = None
    forward_return_5d: float | None = None
    forward_return_10d: float | None = None
    forward_return_20d: float | None = None
    catastrophic_stop_return_10d: float | None = None
    catastrophic_stop_return_20d: float | None = None

    def __contains__(self, key: str) -> bool:
        return getattr(self, key, None) is not None

    def __getitem__(self, key: str) -> float:
        value = getattr(self, key)
        if value is None:
            raise KeyError(key)
        return value

    def get(self, key: str, default: Any = None) -> float | Any:
        return getattr(self, key, default)


@dataclass(frozen=True, slots=True)
class RandomDateObservation:
    symbol: str
    sector: str | None
    event_type: EventType
    signal_date: str
    labels: _RandomDateLabels
    technical_context_v0: _RandomDateTechnicalContext


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_split_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = payload.get("split_manifest", payload)
    required = {
        "train_end",
        "validation_start",
        "validation_end",
        "locked_oos_start",
        "purge_days",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"split manifest missing required fields: {missing}")
    return dict(manifest)


def write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            if hasattr(row, "model_dump_json"):
                f.write(row.model_dump_json() + "\n")
            else:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_ohlcv_csv(
    path: Path,
    *,
    symbols: set[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[OhlcvRow]:
    """Read OHLCV rows without retaining the raw CSV dictionaries.

    Optional filters are applied while streaming so callers working on a
    research split need not materialize unrelated symbols or future sessions.
    """
    out: list[OhlcvRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("close") in (None, ""):
                continue
            symbol = str(row["symbol"])
            if symbols is not None and symbol not in symbols:
                continue
            row_date = date.fromisoformat(str(row["date"]))
            if start_date is not None and row_date < start_date:
                continue
            if end_date is not None and row_date > end_date:
                continue
            out.append(
                OhlcvRow(
                    symbol=symbol,
                    date=row_date,
                    open=_decimal(row["open"]) or Decimal("0"),
                    high=_decimal(row["high"]) or Decimal("0"),
                    low=_decimal(row["low"]) or Decimal("0"),
                    close=_decimal(row["close"]) or Decimal("0"),
                    volume=int(float(row.get("volume") or 0)),
                    turnover=_decimal(row.get("turnover")) or Decimal("0"),
                )
            )
    return sorted(out, key=lambda item: (item.symbol, item.date))


def read_master_csv(path: Path | None) -> dict[str, MasterRow]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {
        str(row["symbol"]): MasterRow(
            symbol=str(row["symbol"]),
            symbol_name=str(row.get("symbol_name") or ""),
            sector=str(row.get("sector") or "") or None,
        )
        for row in rows
    }


def build_events_from_financial_rows(
    rows: list[dict[str, Any]],
    *,
    ohlcv_rows: list[OhlcvRow],
    fetched_at: datetime,
    entry_date_resolver: Callable[[date], date] | None = None,
) -> list[EventRecord]:
    trading_dates = sorted({row.date for row in ohlcv_rows})
    events: list[EventRecord] = []
    cluster_counts: dict[str, int] = defaultdict(int)
    raw_events: list[tuple[dict[str, Any], EventType, date, datetime, str, str, date]] = []
    for raw in rows:
        event_type = classify_event_type(raw)
        if event_type is None:
            continue
        symbol = normalize_symbol(str(raw.get("Code") or raw.get("code") or ""))
        disclosed_date = _parse_date_field(_first(raw, "DiscDate", "DisclosedDate", "Date"))
        disclosed_time = _time_str(_first(raw, "DiscTime", "DisclosedTime"))
        disclosed_at = disclosed_datetime(disclosed_date, disclosed_time)
        if entry_date_resolver is None:
            try:
                entry_date = next_trading_date(trading_dates, disclosed_date)
            except ValueError:
                continue
        else:
            entry_date = entry_date_resolver(disclosed_date)
        cluster_id = event_cluster_id(symbol, disclosed_at)
        cluster_counts[cluster_id] += 1
        raw_events.append(
            (
                raw,
                event_type,
                disclosed_date,
                disclosed_at,
                disclosed_time or "",
                symbol,
                entry_date,
            )
        )

    latest_dividend_by_key: dict[tuple[str, str | None, str], Decimal] = {}
    for raw, event_type, disclosed_date, disclosed_at, disclosed_time, symbol, entry_date in sorted(
        raw_events,
        key=lambda item: (item[3], item[5], str(_first(item[0], "DiscNo", "DisclosureNumber"))),
    ):
        raw_id = str(
            raw.get("DisclosureNumber")
            or raw.get("DiscNo")
            or raw.get("disclosure_number")
            or raw.get("LocalCode")
            or raw.get("Code")
            or hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        )
        cluster_id = event_cluster_id(symbol, disclosed_at)
        event_id = event_record_id(symbol, disclosed_at, raw_id, event_type)
        event_subtype = str(_first(raw, "DocType", "TypeOfDocument") or "") or None
        dividend_key = (symbol, fiscal_target(raw), consolidation_type(raw))
        current_dividend = _decimal(
            _first(raw, "FDivAnn", "ForecastDividendPerShareAnnual", "ForecastDividend")
        )
        previous_dividend = latest_dividend_by_key.get(dividend_key)
        if event_type == EventType.DIVIDEND_REVISION:
            event_subtype = classify_dividend_revision_subtype(
                previous_dividend,
                current_dividend,
            )
        events.append(
            EventRecord(
                event_id=event_id,
                event_cluster_id=cluster_id,
                symbol=symbol,
                source=EventSource.JQUANTS_FINS_SUMMARY,
                raw_document_type=str(
                    _first(raw, "DocType", "TypeOfDocument", "DocumentType") or ""
                ),
                event_type=event_type,
                event_subtype=event_subtype,
                disclosed_date=disclosed_date.isoformat(),
                disclosed_time=disclosed_time or None,
                disclosed_at=disclosed_at,
                data_available_at=disclosed_at,
                signal_date=disclosed_date.isoformat(),
                entry_date=entry_date.isoformat(),
                feature_cutoff_at=disclosed_at,
                raw_source_identifier=raw_id,
                fetched_at=financial_row_fetched_at(raw, fallback=fetched_at),
                cluster_member_count=cluster_counts[cluster_id],
                fiscal_target=fiscal_target(raw),
                consolidation_type=consolidation_type(raw),
                raw=raw,
            )
        )
        if current_dividend is not None:
            latest_dividend_by_key[dividend_key] = current_dividend
    return sorted(events, key=lambda item: (item.disclosed_at, item.symbol, item.event_id))


def build_candidate_features(
    events: list[EventRecord],
    *,
    ohlcv_rows: list[OhlcvRow],
    master: dict[str, MasterRow] | None = None,
) -> list[ObservationRecord]:
    """Build point-in-time features without reading the entry session or labels.

    This is the operational candidate API.  It deliberately leaves
    ``entry_price`` and ``labels`` empty so a candidate can be detected before
    its intended entry session has produced any OHLCV data.
    """

    master = {} if master is None else master
    by_symbol = group_ohlcv_by_symbol(ohlcv_rows)
    sector_by_symbol = {symbol: row.sector for symbol, row in master.items()}
    observations: list[ObservationRecord] = []
    previous_by_event_id = previous_forecast_snapshots(events)
    for event in events:
        bars = by_symbol.get(event.symbol, [])
        required_session_date = required_ohlcv_session_date(event.feature_cutoff_at)
        signal_idx = index_by_date(bars, required_session_date)
        signal_bar = None if signal_idx is None else bars[signal_idx]
        raw = event.raw
        previous = previous_by_event_id.get(event.event_id)
        fundamental = build_fundamental_features(raw, event, previous=previous)
        valuation = build_valuation_features(
            raw,
            event,
            valuation_price=None if signal_bar is None else signal_bar.close,
            sector_medians=sector_medians_for_date(
                by_symbol=by_symbol,
                sector_by_symbol=sector_by_symbol,
                signal_date=required_session_date,
            ),
            sector=sector_by_symbol.get(event.symbol),
        )
        technical = build_technical_context(
            bars=bars,
            signal_idx=signal_idx,
            feature_cutoff_at=event.feature_cutoff_at,
            source_record_id=event.raw_source_identifier,
        )
        observations.append(
            ObservationRecord(
                observation_id=observation_id(
                    event.event_id,
                    ExecutionMode.NEXT_OPEN_UNCONDITIONAL,
                ),
                event_id=event.event_id,
                event_cluster_id=event.event_cluster_id,
                trade_group_id=trade_group_id(event),
                symbol=event.symbol,
                sector=sector_by_symbol.get(event.symbol),
                event_type=event.event_type,
                event_subtype=event.event_subtype,
                execution_mode=ExecutionMode.NEXT_OPEN_UNCONDITIONAL,
                signal_date=event.signal_date,
                entry_date=event.entry_date,
                feature_cutoff_at=event.feature_cutoff_at,
                data_available_at=event.data_available_at,
                entry_price=None,
                valuation_price=None if signal_bar is None else signal_bar.close,
                required_ohlcv_session_date=required_session_date.isoformat(),
                source_bar_date=None if signal_bar is None else signal_bar.date.isoformat(),
                source_bar_available_at=(
                    None if signal_bar is None else daily_bar_available_at(signal_bar.date)
                ),
                previous_forecast_source_record_id=None
                if previous is None
                else previous.source_record_id,
                previous_forecast_available_at=None if previous is None else previous.available_at,
                source_record_id=event.raw_source_identifier,
                fundamental_features_v0=fundamental,
                valuation_features_v0=valuation,
                technical_context_v0=technical,
                labels={},
            )
        )
    return observations


def attach_forward_labels(
    candidates: list[ObservationRecord],
    *,
    ohlcv_rows: list[OhlcvRow],
) -> list[ObservationRecord]:
    """Attach observed next-open prices and forward labels for research only.

    A candidate whose entry-session bar is not in the research dataset is
    omitted, preserving the historical dataset behavior without coupling
    operational detection to future price rows.
    """

    by_symbol = group_ohlcv_by_symbol(ohlcv_rows)
    observations: list[ObservationRecord] = []
    for candidate in candidates:
        if (
            candidate.required_ohlcv_session_date is not None
            and candidate.source_bar_date != candidate.required_ohlcv_session_date
        ):
            continue
        bars = by_symbol.get(candidate.symbol, [])
        entry_idx = index_by_date(bars, date.fromisoformat(candidate.entry_date))
        if entry_idx is None:
            continue
        entry_price = bars[entry_idx].open
        observations.append(
            candidate.model_copy(
                deep=True,
                update={
                    "entry_price": entry_price,
                    "labels": build_forward_labels(
                        bars,
                        entry_idx=entry_idx,
                        entry_price=entry_price,
                    ),
                },
            )
        )
    return observations


def build_observations(
    events: list[EventRecord],
    *,
    ohlcv_rows: list[OhlcvRow],
    master: dict[str, MasterRow] | None = None,
) -> list[ObservationRecord]:
    """Build labelled historical observations for research compatibility."""

    return attach_forward_labels(
        build_candidate_features(events, ohlcv_rows=ohlcv_rows, master=master),
        ohlcv_rows=ohlcv_rows,
    )


def build_random_date_observations(
    *,
    ohlcv_rows: list[OhlcvRow],
    master: dict[str, MasterRow] | None = None,
    symbols: set[str] | None = None,
) -> list[ObservationRecord]:
    master = {} if master is None else master
    by_symbol = group_ohlcv_by_symbol(ohlcv_rows)
    sector_by_symbol = {symbol: row.sector for symbol, row in master.items()}
    observations: list[RandomDateObservation] = []
    for symbol, bars in by_symbol.items():
        if symbols is not None and symbol not in symbols:
            continue
        closes = [bar.close for bar in bars]
        for signal_idx, signal_bar in enumerate(bars):
            entry_idx = signal_idx + 1
            if entry_idx >= len(bars):
                continue
            entry_bar = bars[entry_idx]
            symbol_regime = classify_market_regime(closes, signal_idx)
            technical = _RandomDateTechnicalContext(
                symbol_regime=_RegimeFeature(symbol_regime),
                market_regime=_RegimeFeature(symbol_regime),
            )
            labels = build_random_forward_labels(
                bars,
                entry_idx=entry_idx,
                entry_price=entry_bar.open,
            )
            observations.append(
                RandomDateObservation(
                    symbol=symbol,
                    sector=sector_by_symbol.get(symbol),
                    event_type=EventType.FORECAST_REVISION,
                    signal_date=signal_bar.date.isoformat(),
                    technical_context_v0=technical,
                    labels=labels,
                )
            )
    return observations


def build_random_forward_labels(
    bars: list[OhlcvRow],
    *,
    entry_idx: int,
    entry_price: Decimal,
) -> _RandomDateLabels:
    values: dict[str, float | None] = {
        "forward_return_2d": None,
        "forward_return_5d": None,
        "forward_return_10d": None,
        "forward_return_20d": None,
        "catastrophic_stop_return_10d": None,
        "catastrophic_stop_return_20d": None,
    }
    for horizon in (2, 5, 10, 20):
        exit_idx = entry_idx + horizon
        if exit_idx >= len(bars) or entry_price <= 0:
            continue
        exit_bar = bars[exit_idx]
        ret = (exit_bar.close / entry_price) - Decimal("1")
        values[f"forward_return_{horizon}d"] = float(ret)
        if horizon in (10, 20):
            stop = _catastrophic_stop_label(
                bars[entry_idx : exit_idx + 1],
                entry_price=entry_price,
                fixed_return=ret,
                fixed_exit_date=exit_bar.date.isoformat(),
            )
            values[f"catastrophic_stop_return_{horizon}d"] = float(stop["return"])
    return _RandomDateLabels(**values)


def classify_event_type(raw: dict[str, Any]) -> EventType | None:
    doc = str(_first(raw, "DocType", "TypeOfDocument", "DocumentType") or "").lower()
    if "buyback" in doc or "repurchase" in doc:
        return EventType.BUYBACK_ANNOUNCEMENT
    if "dividend" in doc:
        return EventType.DIVIDEND_REVISION
    if "earnings" in doc or "financialstatements" in doc or "financial statements" in doc:
        return EventType.EARNINGS_RESULT
    if "forecast" in doc or _has_any(raw, "FEPS", "FOP", "FNP", "FSales"):
        return EventType.FORECAST_REVISION
    return None


def classify_dividend_revision_subtype(
    previous_dividend: Decimal | None,
    current_dividend: Decimal | None,
) -> str:
    if previous_dividend is None or current_dividend is None:
        return "invalid"
    if current_dividend > previous_dividend:
        return "increase"
    if current_dividend < previous_dividend:
        return "decrease"
    return "invalid"


def build_fundamental_features(
    raw: dict[str, Any],
    event: EventRecord,
    *,
    previous: ForecastSnapshot | None = None,
) -> FundamentalFeaturesV0:
    prev_eps = None if previous is None else previous.eps
    revised_eps = _decimal(_first(raw, "ForecastEarningsPerShare", "ForecastEPS", "FEPS"))
    eps_latest = _decimal(_first(raw, "EarningsPerShare", "EPS"))
    prev_op = None if previous is None else previous.operating_profit
    revised_op = _decimal(_first(raw, "ForecastOperatingProfit", "FOP"))
    prev_profit = None if previous is None else previous.profit
    revised_profit = _decimal(_first(raw, "ForecastProfit", "FNP"))
    prev_sales = None if previous is None else previous.sales
    revised_sales = _decimal(_first(raw, "ForecastNetSales", "ForecastSales", "FSales"))

    missing_eps = revised_eps is None and eps_latest is None
    negative_eps = any(value is not None and value < 0 for value in (revised_eps, eps_latest))
    previous_eps_near_zero = prev_eps is not None and abs(prev_eps) < Decimal("0.01")
    sign_changed = (
        prev_eps is not None and revised_eps is not None and _sign(prev_eps) != _sign(revised_eps)
    )
    pct_valid = (
        prev_eps is not None
        and revised_eps is not None
        and not previous_eps_near_zero
        and not sign_changed
    )
    op_revision_pct = _pct_delta(prev_op, revised_op)
    profit_revision_pct = _pct_delta(prev_profit, revised_profit)
    sales_revision_pct = _pct_delta(prev_sales, revised_sales)
    eps_growth_yoy = _decimal(raw.get("EpsGrowthYoY"))
    common = _feature_meta(event)
    previous_common = (
        common
        if previous is None
        else {
            **common,
            "available_at": previous.available_at,
            "source_record_id": previous.source_record_id,
            "age_days": max(
                (event.feature_cutoff_at.date() - previous.available_at.date()).days, 0
            ),
        }
    )
    return FundamentalFeaturesV0(
        eps_latest=feature(eps_latest, eps_latest is not None, **common),
        eps_ttm=feature(_decimal(raw.get("EpsTtm")), raw.get("EpsTtm") is not None, **common),
        previous_forecast_eps=feature(prev_eps, prev_eps is not None, **previous_common),
        revised_forecast_eps=feature(revised_eps, revised_eps is not None, **common),
        forecast_eps_revision_absolute=feature(
            None if prev_eps is None or revised_eps is None else revised_eps - prev_eps,
            prev_eps is not None and revised_eps is not None,
            **common,
        ),
        forecast_eps_revision_pct=feature(
            None if not pct_valid else (revised_eps - prev_eps) / abs(prev_eps),
            pct_valid,
            **common,
        ),
        operating_profit_revision_absolute=feature(
            None if prev_op is None or revised_op is None else revised_op - prev_op,
            prev_op is not None and revised_op is not None,
            **common,
        ),
        operating_profit_revision_pct=feature(
            op_revision_pct,
            op_revision_pct is not None,
            **common,
        ),
        profit_revision_absolute=feature(
            None if prev_profit is None or revised_profit is None else revised_profit - prev_profit,
            prev_profit is not None and revised_profit is not None,
            **common,
        ),
        profit_revision_pct=feature(
            profit_revision_pct,
            profit_revision_pct is not None,
            **common,
        ),
        sales_revision_pct=feature(
            sales_revision_pct,
            sales_revision_pct is not None,
            **common,
        ),
        eps_growth_yoy=feature(eps_growth_yoy, eps_growth_yoy is not None, **common),
        is_loss_to_profit=feature(
            prev_eps is not None and revised_eps is not None and prev_eps < 0 < revised_eps,
            prev_eps is not None and revised_eps is not None,
            **common,
        ),
        is_profit_to_loss=feature(
            prev_eps is not None and revised_eps is not None and prev_eps > 0 > revised_eps,
            prev_eps is not None and revised_eps is not None,
            **common,
        ),
        is_one_off_profit_suspected=feature(
            bool(raw.get("OneOffProfitSuspected", False)),
            True,
            **common,
        ),
        accounting_standard=feature(
            _first(raw, "AccountingStandard", "DocType"),
            _first(raw, "AccountingStandard", "DocType") is not None,
            **common,
        ),
        fundamental_data_age_days=feature(0, True, **common),
        revision_pct_valid=pct_valid,
        previous_eps_near_zero=previous_eps_near_zero,
        sign_changed=sign_changed,
        negative_eps=negative_eps,
        missing_eps=missing_eps,
    )


def build_valuation_features(
    raw: dict[str, Any],
    event: EventRecord,
    *,
    valuation_price: Decimal | None,
    sector_medians: dict[str, Decimal],
    sector: str | None,
) -> ValuationFeaturesV0:
    eps_latest = _decimal(_first(raw, "EarningsPerShare", "EPS"))
    forecast_eps = _decimal(_first(raw, "ForecastEarningsPerShare", "ForecastEPS", "FEPS"))
    bps = _decimal(_first(raw, "BookValuePerShare", "BPS"))
    roe = _decimal(raw.get("ROE"))
    dividend = _decimal(
        _first(raw, "ForecastDividendPerShareAnnual", "ForecastDividend", "FDivAnn")
    )
    trailing_per = None if valuation_price is None else _per(valuation_price, eps_latest)
    forecast_per = None if valuation_price is None else _per(valuation_price, forecast_eps)
    pbr = None if valuation_price is None or bps is None or bps <= 0 else valuation_price / bps
    dividend_yield = (
        None
        if dividend is None or valuation_price is None or valuation_price <= 0
        else dividend / valuation_price
    )
    earnings_yield = (
        None
        if valuation_price is None or forecast_eps is None or forecast_eps <= 0
        else forecast_eps / valuation_price
    )
    sector_per = sector_medians.get(f"{sector}:forecast_per") if sector else None
    sector_pbr = sector_medians.get(f"{sector}:pbr") if sector else None
    common = _feature_meta(event)
    return ValuationFeaturesV0(
        trailing_per=feature(trailing_per, trailing_per is not None, **common),
        forecast_per=feature(forecast_per, forecast_per is not None, **common),
        earnings_yield=feature(earnings_yield, earnings_yield is not None, **common),
        bps=feature(bps, bps is not None, **common),
        pbr=feature(pbr, pbr is not None, **common),
        roe=feature(roe, roe is not None, **common),
        forecast_dividend_per_share=feature(dividend, dividend is not None, **common),
        forecast_dividend_yield=feature(dividend_yield, dividend_yield is not None, **common),
        payout_ratio=feature(None, False, **common),
        sector_forecast_per_median=feature(sector_per, sector_per is not None, **common),
        sector_relative_forecast_per=feature(
            None
            if forecast_per is None or sector_per in (None, Decimal("0"))
            else forecast_per / sector_per,
            forecast_per is not None and sector_per not in (None, Decimal("0")),
            **common,
        ),
        sector_pbr_median=feature(sector_pbr, sector_pbr is not None, **common),
        sector_relative_pbr=feature(
            None if pbr is None or sector_pbr in (None, Decimal("0")) else pbr / sector_pbr,
            pbr is not None and sector_pbr not in (None, Decimal("0")),
            **common,
        ),
        sector_earnings_yield_rank=feature(None, False, **common),
        trailing_per_valid=trailing_per is not None,
        forecast_per_valid=forecast_per is not None,
        pbr_valid=pbr is not None,
        dividend_yield_valid=dividend_yield is not None,
    )


def build_technical_context(
    *,
    bars: list[OhlcvRow],
    signal_idx: int | None,
    feature_cutoff_at: datetime,
    source_record_id: str,
) -> TechnicalContextV0:
    if signal_idx is None:
        meta = {
            "source_disclosed_at": feature_cutoff_at,
            "available_at": feature_cutoff_at,
            "feature_cutoff_at": feature_cutoff_at,
            "age_days": 0,
            "source_record_id": source_record_id,
        }
        return TechnicalContextV0(
            **{name: feature(None, False, **meta) for name in TechnicalContextV0.model_fields}
        )
    close = bars[signal_idx].close
    closes = [bar.close for bar in bars]
    turnovers = [bar.turnover for bar in bars]
    volumes = [Decimal(bar.volume) for bar in bars]
    sma20 = _sma(closes, signal_idx, 20)
    sma60 = _sma(closes, signal_idx, 60)
    sma20_past = _sma(closes, signal_idx - 5, 20)
    sma60_past = _sma(closes, signal_idx - 20, 60)
    atr = _atr(bars, signal_idx, 14)
    avg_turnover = _sma(turnovers, signal_idx, 20)
    avg_volume = _sma(volumes, signal_idx, 20)
    meta = {
        "source_disclosed_at": feature_cutoff_at,
        "available_at": feature_cutoff_at,
        "feature_cutoff_at": feature_cutoff_at,
        "age_days": 0,
        "source_record_id": source_record_id,
    }
    return TechnicalContextV0(
        return_5d=feature(_return(closes, signal_idx, 5), signal_idx >= 5, **meta),
        return_20d=feature(_return(closes, signal_idx, 20), signal_idx >= 20, **meta),
        return_60d=feature(_return(closes, signal_idx, 60), signal_idx >= 60, **meta),
        distance_from_sma20=feature(_ratio(close, sma20), sma20 is not None, **meta),
        distance_from_sma60=feature(_ratio(close, sma60), sma60 is not None, **meta),
        sma20_slope=feature(
            _ratio(sma20, sma20_past),
            sma20 is not None and sma20_past is not None,
            **meta,
        ),
        sma60_slope=feature(
            _ratio(sma60, sma60_past),
            sma60 is not None and sma60_past is not None,
            **meta,
        ),
        atr_pct_14d=feature(
            None if atr is None or close <= 0 else atr / close,
            atr is not None,
            **meta,
        ),
        realized_volatility_20d=feature(
            _realized_vol(closes, signal_idx, 20),
            signal_idx >= 20,
            **meta,
        ),
        volume_ratio_20d=feature(
            None if avg_volume in (None, Decimal("0")) else volumes[signal_idx] / avg_volume,
            avg_volume not in (None, Decimal("0")),
            **meta,
        ),
        avg_turnover_20d=feature(avg_turnover, avg_turnover is not None, **meta),
        lot_notional=feature(close * Decimal("100"), close > 0, **meta),
        distance_from_high20=feature(
            _distance_from_high(bars, signal_idx, 20),
            signal_idx >= 20,
            **meta,
        ),
        pre_event_gap_history=feature(_pre_event_gap(bars, signal_idx), signal_idx >= 1, **meta),
        symbol_regime=feature(classify_market_regime(closes, signal_idx), True, **meta),
        market_regime=feature(classify_market_regime(closes, signal_idx), True, **meta),
    )


def build_forward_labels(
    bars: list[OhlcvRow],
    *,
    entry_idx: int,
    entry_price: Decimal,
) -> dict[str, Any]:
    labels: dict[str, Any] = {}
    for horizon in (2, 5, 10, 20):
        exit_idx = entry_idx + horizon
        if exit_idx >= len(bars) or entry_price <= 0:
            continue
        exit_bar = bars[exit_idx]
        ret = (exit_bar.close / entry_price) - Decimal("1")
        low_path = min(bar.low for bar in bars[entry_idx : exit_idx + 1])
        stop = _catastrophic_stop_label(
            bars[entry_idx : exit_idx + 1],
            entry_price=entry_price,
            fixed_return=ret,
            fixed_exit_date=exit_bar.date.isoformat(),
        )
        labels[f"forward_return_{horizon}d"] = float(ret)
        labels[f"exit_date_{horizon}d"] = exit_bar.date.isoformat()
        labels[f"exit_price_{horizon}d"] = str(exit_bar.close)
        labels[f"min_path_return_{horizon}d"] = float((low_path / entry_price) - Decimal("1"))
        labels[f"catastrophic_stop_return_{horizon}d"] = float(stop["return"])
        labels[f"catastrophic_stop_exit_date_{horizon}d"] = stop["exit_date"]
        labels[f"catastrophic_stop_exit_reason_{horizon}d"] = stop["exit_reason"]
    return labels


def evaluate_observations(
    observations: list[ObservationRecord],
    *,
    random_seed_count: int = 300,
    random_date_observations: list[ObservationRecord] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_random_baselines: list[dict[str, Any]] = []
    observation_indexes = {obs.observation_id: idx for idx, obs in enumerate(observations)}
    pools_by_name, combined_observations, coverage = _baseline_index_pools(
        observations,
        random_date_observations=random_date_observations,
    )
    pnl_by_exit = _pnl_by_exit_arm(combined_observations)
    for event_type in sorted({obs.event_type for obs in observations}, key=str):
        subset = [obs for obs in observations if obs.event_type == event_type]
        for entry_arm in (
            EntryArm.EVENT_ONLY,
            EntryArm.EVENT_PLUS_FUNDAMENTAL,
            EntryArm.EVENT_PLUS_TECHNICAL,
            EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
        ):
            selected = cluster_trade_representatives(
                [obs for obs in subset if entry_arm_allows(obs, entry_arm)]
            )
            selected_indexes = [observation_indexes[obs.observation_id] for obs in selected]
            random_by_exit = random_baselines_for_selection_by_exit(
                combined_observations,
                selected_indexes=selected_indexes,
                seed_count=random_seed_count,
                pools_by_name=pools_by_name,
                pnl_by_exit=pnl_by_exit,
            )
            for exit_arm in EXIT_ARMS_FOR_REPORT:
                row_baselines = random_by_exit[exit_arm.value]
                row = {
                    "event_type": event_type.value,
                    "entry_arm": entry_arm.value,
                    "exit_arm": exit_arm.value,
                    **metrics_for_observations(selected, exit_arm=exit_arm),
                    "random_baselines": row_baselines,
                }
                for name in RANDOM_BASELINE_NAMES:
                    row[f"{name}_percentile"] = row_baselines[name]["selected_percentile"]
                rows.append(row)
                row_random_baselines.append(
                    {
                        "event_type": event_type.value,
                        "entry_arm": entry_arm.value,
                        "exit_arm": exit_arm.value,
                        "baselines": row_baselines,
                    }
                )
    baselines = random_baselines(
        observations,
        seed_count=random_seed_count,
        random_date_observations=random_date_observations,
    )
    return {
        "rows": rows,
        "random_baselines": baselines,
        "row_random_baselines": row_random_baselines,
        "random_baseline_coverage": coverage,
    }


def entry_arm_allows(obs: ObservationRecord, arm: EntryArm) -> bool:
    if arm == EntryArm.EVENT_ONLY:
        return True
    if arm == EntryArm.EVENT_PLUS_FUNDAMENTAL:
        return fundamental_rule_allows(obs)
    if arm == EntryArm.EVENT_PLUS_TECHNICAL:
        return technical_veto_allows(obs)
    if arm == EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL:
        return fundamental_rule_allows(obs) and technical_veto_allows(obs)
    if arm in (
        EntryArm.EVENT_PLUS_AI,
        EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL,
        EntryArm.EVENT_PLUS_AI_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
    ):
        return True
    raise ValueError(f"unsupported entry arm: {arm}")


def cluster_earnings_dividend_increase_allows(items: list[ObservationRecord]) -> bool:
    has_earnings = any(obs.event_type == EventType.EARNINGS_RESULT for obs in items)
    has_dividend_increase = any(
        obs.event_type == EventType.DIVIDEND_REVISION and obs.event_subtype == "increase"
        for obs in items
    )
    return has_earnings and has_dividend_increase


def cluster_forecast_per_missing_or_lte(
    items: list[ObservationRecord],
    threshold: Decimal,
) -> bool:
    values = [
        value
        for obs in items
        if (
            value := _decimal_feature_value(
                obs.valuation_features_v0.forecast_per.value,
                valid=obs.valuation_features_v0.forecast_per.valid,
            )
        )
        is not None
        and value > 0
    ]
    return not values or min(values) <= threshold


def cluster_earnings_dividend_value_guard_allows(
    items: list[ObservationRecord],
    *,
    per_threshold: Decimal = Decimal("15"),
) -> bool:
    return cluster_earnings_dividend_increase_allows(items) and cluster_forecast_per_missing_or_lte(
        items,
        per_threshold,
    )


def _decimal_feature_value(value: object, *, valid: bool) -> Decimal | None:
    if not valid or value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def cluster_trade_representatives(
    observations: list[ObservationRecord],
) -> list[ObservationRecord]:
    by_trade: dict[str, ObservationRecord] = {}
    for obs in sorted(observations, key=lambda item: (item.entry_date, item.event_id)):
        key = obs.trade_group_id or obs.event_cluster_id or obs.observation_id
        by_trade.setdefault(key, obs)
    return list(by_trade.values())


def fundamental_rule_allows(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    profit_pct = features.profit_revision_pct.value
    op_pct = features.operating_profit_revision_pct.value
    eps_abs = features.forecast_eps_revision_absolute.value
    revisions = [_as_decimal(value) for value in (profit_pct, op_pct, eps_abs)]
    if obs.event_type == EventType.DIVIDEND_REVISION:
        return obs.event_subtype == "increase"
    return any(value is not None and value > 0 for value in revisions)


def technical_veto_allows(obs: ObservationRecord) -> bool:
    tech = obs.technical_context_v0
    avg_turnover = _as_decimal(tech.avg_turnover_20d.value)
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    return_20d = _as_decimal(tech.return_20d.value)
    regime = _technical_regime_value(tech)
    return (
        avg_turnover is not None
        and avg_turnover >= Decimal("200000000")
        and atr_pct is not None
        and Decimal("0.005") <= atr_pct <= Decimal("0.08")
        and (return_20d is None or return_20d < Decimal("0.30"))
        and regime != "broad_downtrend"
    )


def _technical_regime_value(tech: Any) -> str:
    symbol_regime = getattr(tech, "symbol_regime", None)
    if symbol_regime is not None and getattr(symbol_regime, "value", None):
        return str(symbol_regime.value)
    market_regime = getattr(tech, "market_regime", None)
    return str(getattr(market_regime, "value", "") or "")


def metrics_for_observations(
    observations: list[ObservationRecord],
    *,
    exit_arm: ExitArm,
    include_bootstrap_ci: bool = True,
) -> dict[str, Any]:
    trade_observations = cluster_trade_representatives(observations)
    horizon = _exit_horizon(exit_arm)
    return_key = _return_label_key(exit_arm, horizon)
    returns = [
        Decimal(str(obs.labels[return_key]))
        for obs in trade_observations
        if return_key in obs.labels
    ]
    net_returns = [ret - ROUND_TRIP_COST_RATE for ret in returns]
    pnls = [DEFAULT_TRADE_NOTIONAL * ret for ret in net_returns]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    monthly = _monthly_pnls(trade_observations, pnls)
    return {
        "event_count": len(observations),
        "duplicate_trade_count": len(observations) - len(trade_observations),
        "trade_count": len(returns),
        "net_pnl": float(sum(pnls, Decimal("0"))),
        "profit_factor": None if gross_loss == 0 else float(gross_win / gross_loss),
        "max_drawdown": float(max_drawdown(pnls)),
        "average_return": None if not net_returns else float(sum(net_returns) / len(net_returns)),
        "median_return": None if not net_returns else float(_median(net_returns)),
        "hit_rate": None
        if not net_returns
        else sum(1 for ret in net_returns if ret > 0) / len(net_returns),
        "average_excess_return_vs_topix": None,
        "average_excess_return_vs_sector": None,
        "positive_month_ratio": None
        if not monthly
        else sum(1 for v in monthly.values() if v > 0) / len(monthly),
        "worst_month": None if not monthly else float(min(monthly.values())),
        "block_stability": block_stability(trade_observations, pnls),
        "clustered_bootstrap_ci": bootstrap_ci(pnls, seed=1)
        if include_bootstrap_ci
        else {"low": None, "high": None, "skipped": True},
    }


def random_baselines(
    observations: list[ObservationRecord],
    *,
    seed_count: int,
    random_date_observations: list[ObservationRecord] | None = None,
) -> dict[str, Any]:
    observations = cluster_trade_representatives(observations)
    selected = _net_for_sample(observations, exit_arm=ExitArm.FIXED_10D)
    pools_by_name, combined, _coverage = _baseline_index_pools(
        observations,
        random_date_observations=random_date_observations,
    )
    pnl_by_observation = [_net_pnl_for_exit_arm(obs, ExitArm.FIXED_10D) for obs in combined]
    baselines: dict[str, list[Decimal]] = {name: [] for name in RANDOM_BASELINE_NAMES}
    for seed in range(1, seed_count + 1):
        rng = random.Random(seed)
        for name, pools in pools_by_name.items():
            total = 0.0
            for obs_idx, pool in enumerate(pools):
                chosen_idx = rng.choice(pool) if pool else obs_idx
                total += pnl_by_observation[chosen_idx]
            baselines[name].append(Decimal(str(total)))
    return {name: random_summary(values, selected) for name, values in baselines.items()}


def random_baselines_for_selection_by_exit(
    observations: list[ObservationRecord],
    *,
    selected_indexes: list[int],
    seed_count: int,
    pools_by_name: dict[str, list[list[int]]] | None = None,
    pnl_by_exit: dict[str, list[float]] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    pools = _baseline_index_pools(observations)[0] if pools_by_name is None else pools_by_name
    pnl_by_exit = _pnl_by_exit_arm(observations) if pnl_by_exit is None else pnl_by_exit
    selected_by_exit = {
        exit_name: Decimal(str(sum(pnls[idx] for idx in selected_indexes)))
        for exit_name, pnls in pnl_by_exit.items()
    }
    values_by_exit_and_name: dict[str, dict[str, list[Decimal]]] = {
        exit_arm.value: {name: [] for name in RANDOM_BASELINE_NAMES}
        for exit_arm in EXIT_ARMS_FOR_REPORT
    }
    for seed in range(1, seed_count + 1):
        rng = random.Random(seed)
        for name in RANDOM_BASELINE_NAMES:
            totals = {exit_arm.value: 0.0 for exit_arm in EXIT_ARMS_FOR_REPORT}
            baseline_pools = pools[name]
            for obs_idx in selected_indexes:
                pool = baseline_pools[obs_idx]
                chosen_idx = rng.choice(pool) if pool else obs_idx
                for exit_name, pnls in pnl_by_exit.items():
                    totals[exit_name] += pnls[chosen_idx]
            for exit_name, total in totals.items():
                values_by_exit_and_name[exit_name][name].append(Decimal(str(total)))
    return {
        exit_name: {
            name: random_summary(values, selected_by_exit[exit_name])
            for name, values in values_by_name.items()
        }
        for exit_name, values_by_name in values_by_exit_and_name.items()
    }


def _pnl_by_exit_arm(observations: list[ObservationRecord]) -> dict[str, list[float]]:
    return {
        exit_arm.value: [_net_pnl_for_exit_arm(obs, exit_arm) for obs in observations]
        for exit_arm in EXIT_ARMS_FOR_REPORT
    }


def sample_random_baseline(
    observations: list[ObservationRecord],
    *,
    name: str,
    rng: random.Random,
) -> list[ObservationRecord]:
    return _sample_random_baseline(
        observations,
        name=name,
        rng=rng,
        indexes=_baseline_indexes(observations),
    )


def _sample_random_baseline(
    observations: list[ObservationRecord],
    *,
    name: str,
    rng: random.Random,
    indexes: dict[str, Any],
) -> list[ObservationRecord]:
    by_symbol = indexes["by_symbol"]
    by_symbol_month = indexes["by_symbol_month"]
    by_symbol_regime = indexes["by_symbol_regime"]
    by_sector_date = indexes["by_sector_date"]
    by_event_type = indexes["by_event_type"]
    out: list[ObservationRecord] = []
    for obs in observations:
        if name in {
            "same_symbol_random_date",
            "same_symbol_same_month_random_date",
            "same_symbol_same_regime_random_date",
        }:
            pool = by_symbol[obs.symbol]
            if name == "same_symbol_same_month_random_date":
                pool = by_symbol_month[(obs.symbol, obs.signal_date[:7])]
            if name == "same_symbol_same_regime_random_date":
                regime = _technical_regime_value(obs.technical_context_v0)
                pool = by_symbol_regime[(obs.symbol, regime)]
        elif name == "same_sector_same_date_random":
            pool = by_sector_date[(obs.sector, obs.signal_date)]
        elif name == "event_type_matched_random":
            pool = by_event_type[obs.event_type]
        else:
            raise ValueError(f"unknown baseline: {name}")
        out.append(rng.choice(pool) if pool else obs)
    return out


def _baseline_indexes(observations: list[ObservationRecord]) -> dict[str, Any]:
    by_symbol: dict[str, list[ObservationRecord]] = defaultdict(list)
    by_symbol_month: dict[tuple[str, str], list[ObservationRecord]] = defaultdict(list)
    by_symbol_regime: dict[tuple[str, Any], list[ObservationRecord]] = defaultdict(list)
    by_sector_date: dict[tuple[str | None, str], list[ObservationRecord]] = defaultdict(list)
    by_event_type: dict[EventType, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        by_symbol[obs.symbol].append(obs)
        by_symbol_month[(obs.symbol, obs.signal_date[:7])].append(obs)
        by_symbol_regime[(obs.symbol, _technical_regime_value(obs.technical_context_v0))].append(
            obs
        )
        by_sector_date[(obs.sector, obs.signal_date)].append(obs)
        by_event_type[obs.event_type].append(obs)
    return {
        "by_symbol": by_symbol,
        "by_symbol_month": by_symbol_month,
        "by_symbol_regime": by_symbol_regime,
        "by_sector_date": by_sector_date,
        "by_event_type": by_event_type,
    }


def _baseline_index_pools(
    observations: list[ObservationRecord],
    *,
    random_date_observations: list[ObservationRecord] | None = None,
) -> tuple[dict[str, list[list[int]]], list[ObservationRecord], dict[str, Any]]:
    random_date_observations = [] if random_date_observations is None else random_date_observations
    combined = [*observations, *random_date_observations]
    random_offset = len(observations)
    by_symbol: dict[str, list[int]] = defaultdict(list)
    by_symbol_month: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_symbol_regime: dict[tuple[str, Any], list[int]] = defaultdict(list)
    by_sector_date: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    by_event_type: dict[EventType, list[int]] = defaultdict(list)
    for idx, obs in enumerate(observations):
        by_symbol[obs.symbol].append(idx)
        by_symbol_month[(obs.symbol, obs.signal_date[:7])].append(idx)
        by_symbol_regime[(obs.symbol, _technical_regime_value(obs.technical_context_v0))].append(
            idx
        )
        by_sector_date[(obs.sector, obs.signal_date)].append(idx)
        by_event_type[obs.event_type].append(idx)
    random_by_symbol: dict[str, list[int]] = defaultdict(list)
    random_by_symbol_month: dict[tuple[str, str], list[int]] = defaultdict(list)
    random_by_symbol_regime: dict[tuple[str, Any], list[int]] = defaultdict(list)
    random_by_sector_date: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    event_dates_by_symbol: dict[str, set[str]] = defaultdict(set)
    for obs in observations:
        event_dates_by_symbol[obs.symbol].add(obs.signal_date)
    for idx, obs in enumerate(random_date_observations, start=random_offset):
        if obs.signal_date in event_dates_by_symbol.get(obs.symbol, set()):
            continue
        random_by_symbol[obs.symbol].append(idx)
        random_by_symbol_month[(obs.symbol, obs.signal_date[:7])].append(idx)
        random_regime = _technical_regime_value(obs.technical_context_v0)
        random_by_symbol_regime[(obs.symbol, random_regime)].append(idx)
        random_by_sector_date[(obs.sector, obs.signal_date)].append(idx)

    pools = {
        "same_symbol_random_date": [
            _pool_or_fallback(random_by_symbol[obs.symbol], by_symbol[obs.symbol], idx)
            for idx, obs in enumerate(observations)
        ],
        "same_symbol_random_event_date": [by_symbol[obs.symbol] for obs in observations],
        "same_symbol_same_month_random_date": [
            _pool_or_fallback(
                random_by_symbol_month[(obs.symbol, obs.signal_date[:7])],
                by_symbol_month[(obs.symbol, obs.signal_date[:7])],
                idx,
            )
            for idx, obs in enumerate(observations)
        ],
        "same_symbol_same_regime_random_date": [
            _pool_or_fallback(
                random_by_symbol_regime[
                    (obs.symbol, _technical_regime_value(obs.technical_context_v0))
                ],
                by_symbol_regime[(obs.symbol, _technical_regime_value(obs.technical_context_v0))],
                idx,
            )
            for idx, obs in enumerate(observations)
        ],
        "same_sector_same_date_random": [
            _pool_or_fallback(
                random_by_sector_date[(obs.sector, obs.signal_date)],
                by_sector_date[(obs.sector, obs.signal_date)],
                idx,
            )
            for idx, obs in enumerate(observations)
        ],
        "event_type_matched_random": [by_event_type[obs.event_type] for obs in observations],
    }
    coverage = {
        name: _pool_coverage(pool_list, random_offset=random_offset)
        for name, pool_list in pools.items()
    }
    return pools, combined, coverage


def _pool_or_fallback(primary: list[int], fallback: list[int], self_idx: int) -> list[int]:
    primary_without_self = [idx for idx in primary if idx != self_idx]
    if primary_without_self:
        return primary_without_self
    fallback_without_self = [idx for idx in fallback if idx != self_idx]
    return fallback_without_self or [self_idx]


def _pool_coverage(pool_list: list[list[int]], *, random_offset: int) -> dict[str, Any]:
    pool_sizes = [len(pool) for pool in pool_list]
    matched = sum(1 for pool in pool_list if any(idx >= random_offset for idx in pool))
    fallback = len(pool_list) - matched
    return {
        "matched": matched,
        "unmatched": 0,
        "fallback": fallback,
        "candidate_pool_size_min": min(pool_sizes) if pool_sizes else 0,
        "candidate_pool_size_median": float(_median([Decimal(size) for size in pool_sizes]))
        if pool_sizes
        else 0,
        "candidate_pool_size_max": max(pool_sizes) if pool_sizes else 0,
        "fallback_rate": None if not pool_list else fallback / len(pool_list),
    }


def random_summary(values: list[Decimal], selected: Decimal) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "random_count": len(values),
        "selected_net_pnl": float(selected),
        "random_mean": float(sum(values, Decimal("0")) / len(values)) if values else None,
        "random_median": float(_quantile(ordered, Decimal("0.50"))) if values else None,
        "random_p75": float(_quantile(ordered, Decimal("0.75"))) if values else None,
        "random_p90": float(_quantile(ordered, Decimal("0.90"))) if values else None,
        "random_p95": float(_quantile(ordered, Decimal("0.95"))) if values else None,
        "random_max": float(max(values)) if values else None,
        "selected_percentile": percentile(values, selected),
    }


def percentile(values: list[Decimal], selected: Decimal) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if value <= selected) / len(values)


def select_observations_for_split(
    observations: list[ObservationRecord],
    *,
    split: str,
    fixed_split_manifest: dict[str, Any] | None = None,
) -> tuple[list[ObservationRecord], dict[str, Any]]:
    if split not in EVALUATION_SPLITS:
        raise ValueError(f"unsupported evaluation split: {split}")
    manifest = split_manifest(observations, fixed_manifest=fixed_split_manifest)
    if not manifest:
        return [], {"requested_split": split, "selected_observation_count": 0}
    if split == "all":
        selected = list(observations)
    else:
        selected = [
            obs
            for obs in observations
            if _observation_split(obs, manifest) in _requested_split_labels(split)
        ]
    counts: dict[str, int] = defaultdict(int)
    for obs in observations:
        counts[_observation_split(obs, manifest)] += 1
    return selected, {
        "requested_split": split,
        "selected_observation_count": len(selected),
        "selected_symbol_count": len({obs.symbol for obs in selected}),
        "split_counts": dict(counts),
        "split_manifest": manifest,
    }


def _requested_split_labels(split: str) -> set[str]:
    if split == "development":
        return {"train", "validation"}
    if split == "train":
        return {"train"}
    if split == "validation":
        return {"validation"}
    if split == "locked-oos":
        return {"locked_oos"}
    raise ValueError(f"unsupported evaluation split: {split}")


def _observation_split(obs: ObservationRecord, manifest: dict[str, Any]) -> str:
    return observation_split_label(obs, manifest)


def observation_split_label(obs: ObservationRecord, manifest: dict[str, Any]) -> str:
    signal_date = date.fromisoformat(obs.signal_date)
    train_end = date.fromisoformat(manifest["train_end"])
    validation_start = date.fromisoformat(manifest["validation_start"])
    validation_end = date.fromisoformat(manifest["validation_end"])
    locked_oos_start = date.fromisoformat(manifest["locked_oos_start"])
    exit_20d = _label_exit_date(obs, 20)
    if signal_date <= train_end:
        if exit_20d is not None and exit_20d >= validation_start:
            return "purge_train_validation"
        return "train"
    if signal_date < validation_start:
        return "purge_train_validation"
    if signal_date <= validation_end:
        if exit_20d is not None and exit_20d >= locked_oos_start:
            return "purge_validation_locked_oos"
        return "validation"
    if signal_date < locked_oos_start:
        return "purge_validation_locked_oos"
    return "locked_oos"


def _label_exit_date(obs: ObservationRecord, horizon: int) -> date | None:
    raw = obs.labels.get(f"exit_date_{horizon}d")
    if raw in (None, ""):
        return None
    return date.fromisoformat(str(raw))


def split_manifest(
    observations: list[ObservationRecord],
    *,
    fixed_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dates = sorted({date.fromisoformat(obs.signal_date) for obs in observations})
    if not dates:
        return {}
    if fixed_manifest is None:
        train_end = dates[int(len(dates) * 0.60)]
        validation_start = _shift_trading_date(dates, train_end, PURGE_TRADING_DAYS)
        validation_end = dates[int(len(dates) * 0.80)]
        oos_start = _shift_trading_date(dates, validation_end, PURGE_TRADING_DAYS)
        purge_days = PURGE_TRADING_DAYS
        fixed_meta: dict[str, Any] = {"fixed_split_manifest": False}
    else:
        train_end = date.fromisoformat(str(fixed_manifest["train_end"]))
        validation_start = date.fromisoformat(str(fixed_manifest["validation_start"]))
        validation_end = date.fromisoformat(str(fixed_manifest["validation_end"]))
        oos_start = date.fromisoformat(str(fixed_manifest["locked_oos_start"]))
        purge_days = int(fixed_manifest.get("purge_days") or PURGE_TRADING_DAYS)
        fixed_meta = {
            "fixed_split_manifest": True,
            "fixed_split_manifest_dataset_hash": fixed_manifest.get("dataset_hash"),
            "fixed_split_manifest_observation_count": fixed_manifest.get("split_observation_count"),
        }
    dataset_hash = hashlib.sha256(
        "\n".join(sorted(obs.model_dump_json() for obs in observations)).encode("utf-8")
    ).hexdigest()
    return {
        "train_start": dates[0].isoformat(),
        "train_end": train_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
        "locked_oos_start": oos_start.isoformat(),
        "locked_oos_end": dates[-1].isoformat(),
        "purge_days": purge_days,
        "dataset_hash": dataset_hash,
        "split_observation_count": len(observations),
        "split_symbol_count": len({obs.symbol for obs in observations}),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        **fixed_meta,
    }


def next_trading_date(trading_dates: list[date], current: date) -> date:
    for item in trading_dates:
        if item > current:
            return item
    raise ValueError(f"no trading date after {current}")


def next_tse_business_date(current: date) -> date:
    """Resolve the next entry session without consulting future OHLCV rows."""

    return next_business_day(current)


def disclosed_datetime(disclosed_date: date, disclosed_time: str | None) -> datetime:
    if disclosed_time:
        parts = [int(part) for part in disclosed_time.split(":")]
        local = datetime.combine(disclosed_date, time(parts[0], parts[1], parts[2]), tzinfo=TOKYO)
        return local.astimezone(UTC)
    local = datetime.combine(disclosed_date, time(0, 0, 0), tzinfo=TOKYO)
    return local.astimezone(UTC)


def financial_row_fetched_at(raw: dict[str, Any], *, fallback: datetime) -> datetime:
    """Read exporter receipt provenance, falling back for legacy research archives."""

    value = raw.get("_roboinvest_fetched_at")
    if value in (None, ""):
        return fallback
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("_roboinvest_fetched_at must include a timezone offset")
    return parsed


def daily_bar_available_at(bar_date: date) -> datetime:
    return datetime.combine(bar_date, DAILY_BAR_AVAILABLE_TIME_JST, tzinfo=TOKYO).astimezone(UTC)


def required_ohlcv_session_date(feature_cutoff_at: datetime) -> date:
    """Return the one daily session permitted for a frozen feature cutoff.

    A same-day bar is usable only at or after its fixed 15:30 JST availability
    time.  Before then (and on non-business days), the immediately preceding
    TSE business session is required; callers must not silently substitute an
    even older row when it is absent.
    """

    if feature_cutoff_at.tzinfo is None:
        raise ValueError("feature_cutoff_at must be timezone-aware")
    cutoff_date = feature_cutoff_at.astimezone(TOKYO).date()
    if is_tse_business_day(cutoff_date) and feature_cutoff_at >= daily_bar_available_at(
        cutoff_date
    ):
        return cutoff_date
    return previous_business_day(cutoff_date)


def event_cluster_id(symbol: str, disclosed_at: datetime) -> str:
    raw = f"{symbol}:{disclosed_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def event_record_id(symbol: str, disclosed_at: datetime, raw_id: str, event_type: EventType) -> str:
    raw = f"{symbol}:{disclosed_at.isoformat()}:{raw_id}:{event_type.value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def observation_id(event_id: str, execution_mode: ExecutionMode) -> str:
    return hashlib.sha256(f"{event_id}:{execution_mode.value}".encode()).hexdigest()[:24]


def trade_group_id(event: EventRecord) -> str:
    raw = f"{event.symbol}:{event.event_cluster_id}:{event.entry_date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_symbol(raw: str) -> str:
    return raw.strip()[:4]


def group_ohlcv_by_symbol(rows: list[OhlcvRow]) -> dict[str, list[OhlcvRow]]:
    out: dict[str, list[OhlcvRow]] = defaultdict(list)
    for row in rows:
        out[row.symbol].append(row)
    return {symbol: sorted(items, key=lambda item: item.date) for symbol, items in out.items()}


def index_on_or_before(bars: list[OhlcvRow], target: date) -> int | None:
    found: int | None = None
    for idx, bar in enumerate(bars):
        if bar.date <= target:
            found = idx
        else:
            break
    return found


def fiscal_target(raw: dict[str, Any]) -> str | None:
    cur = _first(raw, "CurFYEn", "CurrentFiscalYearEnd")
    nxt = _first(raw, "NxtFYEn", "NextFiscalYearEnd")
    target = cur or nxt
    return None if target in (None, "") else str(target)


def consolidation_type(raw: dict[str, Any]) -> str:
    doc = str(_first(raw, "DocType", "TypeOfDocument", "DocumentType") or "").lower()
    if "nonconsolidated" in doc or "non_consolidated" in doc:
        return "non_consolidated"
    return "consolidated"


def forecast_snapshot_from_event(event: EventRecord) -> ForecastSnapshot | None:
    raw = event.raw
    eps = _decimal(_first(raw, "FEPS", "ForecastEarningsPerShare", "ForecastEPS"))
    op = _decimal(_first(raw, "FOP", "ForecastOperatingProfit"))
    profit = _decimal(_first(raw, "FNP", "ForecastProfit"))
    sales = _decimal(_first(raw, "FSales", "ForecastNetSales", "ForecastSales"))
    dividend = _decimal(
        _first(raw, "FDivAnn", "ForecastDividendPerShareAnnual", "ForecastDividend")
    )
    if all(value is None for value in (eps, op, profit, sales, dividend)):
        return None
    return ForecastSnapshot(
        source_record_id=event.raw_source_identifier,
        available_at=event.data_available_at,
        eps=eps,
        operating_profit=op,
        profit=profit,
        sales=sales,
        dividend=dividend,
    )


def previous_forecast_snapshots(events: list[EventRecord]) -> dict[str, ForecastSnapshot]:
    out: dict[str, ForecastSnapshot] = {}
    latest: dict[tuple[str, str | None, str | None], ForecastSnapshot] = {}
    for event in sorted(events, key=lambda item: (item.disclosed_at, item.event_id)):
        key = (event.symbol, event.fiscal_target, event.consolidation_type)
        previous = latest.get(key)
        if previous is not None and previous.available_at < event.data_available_at:
            out[event.event_id] = previous
        current = forecast_snapshot_from_event(event)
        if current is not None:
            latest[key] = current
    return out


def index_by_date(bars: list[OhlcvRow], target: date) -> int | None:
    for idx, bar in enumerate(bars):
        if bar.date == target:
            return idx
    return None


def feature(
    value: Any,
    valid: bool,
    *,
    source_disclosed_at: datetime,
    available_at: datetime,
    feature_cutoff_at: datetime,
    age_days: int,
    source_record_id: str,
) -> FeatureValue:
    return FeatureValue(
        value=value,
        valid=valid,
        source_disclosed_at=source_disclosed_at,
        available_at=available_at,
        feature_cutoff_at=feature_cutoff_at,
        age_days=age_days,
        source_record_id=source_record_id,
    )


def _feature_meta(event: EventRecord) -> dict[str, Any]:
    return {
        "source_disclosed_at": event.disclosed_at,
        "available_at": event.data_available_at,
        "feature_cutoff_at": event.feature_cutoff_at,
        "age_days": 0,
        "source_record_id": event.raw_source_identifier,
    }


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _has_any(raw: dict[str, Any], *keys: str) -> bool:
    return any(raw.get(key) not in (None, "") for key in keys)


def _parse_date_field(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _time_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text.split(":")) == 2:
        return f"{text}:00"
    return text


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    return _decimal(value)


def _pct_delta(previous: Decimal | None, revised: Decimal | None) -> Decimal | None:
    if previous is None or revised is None or abs(previous) < Decimal("0.000001"):
        return None
    if _sign(previous) != _sign(revised):
        return None
    return (revised - previous) / abs(previous)


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _per(price: Decimal, eps: Decimal | None) -> Decimal | None:
    if eps is None or eps <= 0 or price <= 0:
        return None
    return price / eps


def _sma(values: list[Decimal], idx: int, period: int) -> Decimal | None:
    if idx < 0 or idx + 1 < period:
        return None
    return sum(values[idx + 1 - period : idx + 1], Decimal("0")) / Decimal(period)


def _return(values: list[Decimal], idx: int, period: int) -> Decimal | None:
    if idx < period or values[idx - period] <= 0:
        return None
    return (values[idx] / values[idx - period]) - Decimal("1")


def _ratio(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None or right == 0:
        return None
    return (left / right) - Decimal("1")


def _atr(bars: list[OhlcvRow], idx: int, period: int) -> Decimal | None:
    if idx < period:
        return None
    ranges: list[Decimal] = []
    for item_idx in range(idx + 1 - period, idx + 1):
        previous_close = bars[item_idx - 1].close
        bar = bars[item_idx]
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return sum(ranges, Decimal("0")) / Decimal(period)


def _realized_vol(values: list[Decimal], idx: int, period: int) -> Decimal | None:
    if idx < period:
        return None
    returns = [_return(values, item_idx, 1) for item_idx in range(idx + 1 - period, idx + 1)]
    clean = [float(item) for item in returns if item is not None]
    if len(clean) < 2:
        return None
    avg = sum(clean) / len(clean)
    variance = sum((item - avg) ** 2 for item in clean) / (len(clean) - 1)
    return Decimal(str(math.sqrt(variance)))


def _distance_from_high(bars: list[OhlcvRow], idx: int, period: int) -> Decimal | None:
    if idx < period:
        return None
    high = max(bar.high for bar in bars[idx + 1 - period : idx + 1])
    if high <= 0:
        return None
    return (bars[idx].close / high) - Decimal("1")


def _pre_event_gap(bars: list[OhlcvRow], idx: int) -> Decimal | None:
    if idx < 1 or bars[idx - 1].close <= 0:
        return None
    return (bars[idx].open / bars[idx - 1].close) - Decimal("1")


def classify_market_regime(closes: list[Decimal], idx: int) -> str:
    ret20 = _return(closes, idx, 20)
    ret60 = _return(closes, idx, 60)
    if ret20 is None or ret60 is None:
        return "transition_chop"
    if ret20 > Decimal("0.03") and ret60 > Decimal("0.05"):
        return "broad_uptrend"
    if ret20 < Decimal("-0.03") and ret60 < Decimal("-0.05"):
        return "broad_downtrend"
    return "transition_chop"


def sector_medians_for_date(
    *,
    by_symbol: dict[str, list[OhlcvRow]],
    sector_by_symbol: dict[str, str | None],
    signal_date: date,
) -> dict[str, Decimal]:
    # Placeholder for point-in-time sector snapshots. The dataset builder keeps
    # this explicit instead of backfilling future financial statements.
    return {}


def _exit_horizon(exit_arm: ExitArm) -> int:
    if exit_arm in (ExitArm.FIXED_2D,):
        return 2
    if exit_arm in (ExitArm.FIXED_5D,):
        return 5
    if exit_arm in (ExitArm.FIXED_10D, ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP):
        return 10
    if exit_arm in (ExitArm.FIXED_20D, ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP):
        return 20
    raise ValueError(f"unsupported exit arm: {exit_arm}")


def _return_label_key(exit_arm: ExitArm, horizon: int) -> str:
    if exit_arm in (
        ExitArm.FIXED_10D_PLUS_CATASTROPHIC_STOP,
        ExitArm.FIXED_20D_PLUS_CATASTROPHIC_STOP,
    ):
        return f"catastrophic_stop_return_{horizon}d"
    return f"forward_return_{horizon}d"


def _catastrophic_stop_label(
    path: list[OhlcvRow],
    *,
    entry_price: Decimal,
    fixed_return: Decimal,
    fixed_exit_date: str,
) -> dict[str, Decimal | str]:
    stop_price = entry_price * (Decimal("1") + CAT_STOP_PCT)
    for bar in path:
        if bar.open <= stop_price:
            return {
                "return": (bar.open / entry_price) - Decimal("1"),
                "exit_date": bar.date.isoformat(),
                "exit_reason": "gap_through_catastrophic_stop",
            }
        if bar.low <= stop_price:
            return {
                "return": CAT_STOP_PCT,
                "exit_date": bar.date.isoformat(),
                "exit_reason": "catastrophic_stop",
            }
    return {
        "return": fixed_return,
        "exit_date": fixed_exit_date,
        "exit_reason": "fixed_exit",
    }


def _monthly_pnls(observations: list[ObservationRecord], pnls: list[Decimal]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for obs, pnl in zip(observations, pnls, strict=False):
        out[obs.entry_date[:7]] += pnl
    return out


def max_drawdown(pnls: list[Decimal]) -> Decimal:
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def block_stability(observations: list[ObservationRecord], pnls: list[Decimal]) -> dict[str, Any]:
    by_month: dict[str, Decimal] = _monthly_pnls(observations, pnls)
    counts: dict[str, int] = defaultdict(int)
    for obs in observations:
        counts[obs.entry_date[:7]] += 1
    values = list(by_month.values())
    return {
        "positive_block_ratio": None
        if not values
        else sum(1 for value in values if value > 0) / len(values),
        "worst_block_pnl": None if not values else float(min(values)),
        "median_block_pnl": None if not values else float(_median(values)),
        "event_count_per_block": dict(counts),
    }


def bootstrap_ci(pnls: list[Decimal], *, seed: int, samples: int = 200) -> dict[str, float | None]:
    if not pnls:
        return {"low": None, "high": None}
    rng = random.Random(seed)
    totals: list[Decimal] = []
    for _ in range(samples):
        totals.append(sum((rng.choice(pnls) for _ in pnls), Decimal("0")))
    ordered = sorted(totals)
    return {
        "low": float(_quantile(ordered, Decimal("0.025"))),
        "high": float(_quantile(ordered, Decimal("0.975"))),
    }


def _median(values: list[Decimal]) -> Decimal:
    return _quantile(sorted(values), Decimal("0.50"))


def _quantile(sorted_values: list[Decimal], q: Decimal) -> Decimal:
    if not sorted_values:
        return Decimal("0")
    idx = int((Decimal(len(sorted_values) - 1) * q).to_integral_value())
    return sorted_values[idx]


def _net_for_sample(observations: list[ObservationRecord], *, exit_arm: ExitArm) -> Decimal:
    return sum((_net_pnl_for_exit_arm_decimal(obs, exit_arm) for obs in observations), Decimal("0"))


def _net_pnl_10d(obs: ObservationRecord) -> float:
    return _net_pnl_for_exit_arm(obs, ExitArm.FIXED_10D)


def _net_pnl_for_exit_arm(obs: ObservationRecord, exit_arm: ExitArm) -> float:
    horizon = _exit_horizon(exit_arm)
    value = obs.labels.get(_return_label_key(exit_arm, horizon))
    if value is None:
        return 0.0
    return float(DEFAULT_TRADE_NOTIONAL) * (float(value) - float(ROUND_TRIP_COST_RATE))


def _net_pnl_10d_decimal(obs: ObservationRecord) -> Decimal:
    return _net_pnl_for_exit_arm_decimal(obs, ExitArm.FIXED_10D)


def _net_pnl_for_exit_arm_decimal(obs: ObservationRecord, exit_arm: ExitArm) -> Decimal:
    horizon = _exit_horizon(exit_arm)
    value = obs.labels.get(_return_label_key(exit_arm, horizon))
    if value is None:
        return Decimal("0")
    return DEFAULT_TRADE_NOTIONAL * (Decimal(str(value)) - ROUND_TRIP_COST_RATE)


def _shift_trading_date(dates: list[date], start: date, days: int) -> date:
    idx = dates.index(start)
    return dates[min(idx + days, len(dates) - 1)]
