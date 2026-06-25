from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

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

FEATURE_SCHEMA_VERSION = "event_research_v0"
PURGE_TRADING_DAYS = 20
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            if hasattr(row, "model_dump_json"):
                f.write(row.model_dump_json() + "\n")
            else:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_ohlcv_csv(path: Path) -> list[OhlcvRow]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out = [
        OhlcvRow(
            symbol=str(row["symbol"]),
            date=date.fromisoformat(str(row["date"])),
            open=_decimal(row["open"]) or Decimal("0"),
            high=_decimal(row["high"]) or Decimal("0"),
            low=_decimal(row["low"]) or Decimal("0"),
            close=_decimal(row["close"]) or Decimal("0"),
            volume=int(float(row.get("volume") or 0)),
            turnover=_decimal(row.get("turnover")) or Decimal("0"),
        )
        for row in rows
        if row.get("close") not in (None, "")
    ]
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
) -> list[EventRecord]:
    trading_dates = sorted({row.date for row in ohlcv_rows})
    events: list[EventRecord] = []
    cluster_counts: dict[str, int] = defaultdict(int)
    raw_events: list[tuple[dict[str, Any], EventType, datetime, str, str, date]] = []
    for raw in rows:
        event_type = classify_event_type(raw)
        if event_type is None:
            continue
        symbol = normalize_symbol(str(raw.get("Code") or raw.get("code") or ""))
        disclosed_date = _parse_date_field(raw.get("DisclosedDate") or raw.get("Date"))
        disclosed_time = _time_str(raw.get("DisclosedTime"))
        disclosed_at = disclosed_datetime(disclosed_date, disclosed_time)
        entry_date = next_trading_date(trading_dates, disclosed_date)
        cluster_id = event_cluster_id(symbol, disclosed_at)
        cluster_counts[cluster_id] += 1
        raw_events.append((raw, event_type, disclosed_at, disclosed_time or "", symbol, entry_date))

    for raw, event_type, disclosed_at, disclosed_time, symbol, entry_date in raw_events:
        raw_id = str(
            raw.get("DisclosureNumber")
            or raw.get("disclosure_number")
            or raw.get("LocalCode")
            or raw.get("Code")
            or hashlib.sha256(json.dumps(raw, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        )
        cluster_id = event_cluster_id(symbol, disclosed_at)
        disclosed_date = disclosed_at.date()
        event_id = event_record_id(symbol, disclosed_at, raw_id, event_type)
        events.append(
            EventRecord(
                event_id=event_id,
                event_cluster_id=cluster_id,
                symbol=symbol,
                source=EventSource.JQUANTS_FINS_SUMMARY,
                raw_document_type=str(raw.get("TypeOfDocument") or raw.get("DocumentType") or ""),
                event_type=event_type,
                event_subtype=str(raw.get("TypeOfDocument") or "") or None,
                disclosed_date=disclosed_date.isoformat(),
                disclosed_time=disclosed_time or None,
                disclosed_at=disclosed_at,
                data_available_at=disclosed_at,
                signal_date=disclosed_date.isoformat(),
                entry_date=entry_date.isoformat(),
                feature_cutoff_at=disclosed_at,
                raw_source_identifier=raw_id,
                fetched_at=fetched_at,
                cluster_member_count=cluster_counts[cluster_id],
                raw=raw,
            )
        )
    return sorted(events, key=lambda item: (item.disclosed_at, item.symbol, item.event_id))


def build_observations(
    events: list[EventRecord],
    *,
    ohlcv_rows: list[OhlcvRow],
    master: dict[str, MasterRow] | None = None,
) -> list[ObservationRecord]:
    master = {} if master is None else master
    by_symbol = group_ohlcv_by_symbol(ohlcv_rows)
    sector_by_symbol = {symbol: row.sector for symbol, row in master.items()}
    observations: list[ObservationRecord] = []
    for event in events:
        bars = by_symbol.get(event.symbol, [])
        if not bars:
            continue
        signal_day = date.fromisoformat(event.signal_date)
        entry_day = date.fromisoformat(event.entry_date)
        signal_idx = index_on_or_before(bars, signal_day)
        entry_idx = index_by_date(bars, entry_day)
        if signal_idx is None or entry_idx is None:
            continue
        signal_bar = bars[signal_idx]
        entry_bar = bars[entry_idx]
        raw = event.raw
        fundamental = build_fundamental_features(raw, event)
        valuation = build_valuation_features(
            raw,
            event,
            valuation_price=signal_bar.close,
            sector_medians=sector_medians_for_date(
                by_symbol=by_symbol,
                sector_by_symbol=sector_by_symbol,
                signal_date=signal_day,
            ),
            sector=sector_by_symbol.get(event.symbol),
        )
        technical = build_technical_context(
            bars=bars,
            signal_idx=signal_idx,
            feature_cutoff_at=event.feature_cutoff_at,
            source_record_id=event.raw_source_identifier,
        )
        labels = build_forward_labels(bars, entry_idx=entry_idx, entry_price=entry_bar.open)
        observations.append(
            ObservationRecord(
                observation_id=observation_id(
                    event.event_id,
                    ExecutionMode.NEXT_OPEN_UNCONDITIONAL,
                ),
                event_id=event.event_id,
                event_cluster_id=event.event_cluster_id,
                symbol=event.symbol,
                sector=sector_by_symbol.get(event.symbol),
                event_type=event.event_type,
                execution_mode=ExecutionMode.NEXT_OPEN_UNCONDITIONAL,
                signal_date=event.signal_date,
                entry_date=event.entry_date,
                feature_cutoff_at=event.feature_cutoff_at,
                data_available_at=event.data_available_at,
                entry_price=entry_bar.open,
                valuation_price=signal_bar.close,
                source_record_id=event.raw_source_identifier,
                fundamental_features_v0=fundamental,
                valuation_features_v0=valuation,
                technical_context_v0=technical,
                labels=labels,
            )
        )
    return observations


def classify_event_type(raw: dict[str, Any]) -> EventType | None:
    doc = str(raw.get("TypeOfDocument") or raw.get("DocumentType") or "").lower()
    if "buyback" in doc or "repurchase" in doc:
        return EventType.BUYBACK_ANNOUNCEMENT
    if "dividend" in doc:
        return EventType.DIVIDEND_REVISION
    if "forecast" in doc or _has_any(raw, "PreviousForecastProfit", "PreviousForecastEPS"):
        return EventType.FORECAST_REVISION
    if "earnings" in doc or "financialstatements" in doc or "financial statements" in doc:
        return EventType.EARNINGS_RESULT
    return None


def build_fundamental_features(raw: dict[str, Any], event: EventRecord) -> FundamentalFeaturesV0:
    prev_eps = _decimal(_first(raw, "PreviousForecastEarningsPerShare", "PreviousForecastEPS"))
    revised_eps = _decimal(_first(raw, "ForecastEarningsPerShare", "ForecastEPS"))
    eps_latest = _decimal(_first(raw, "EarningsPerShare", "EPS"))
    prev_op = _decimal(_first(raw, "PreviousForecastOperatingProfit"))
    revised_op = _decimal(_first(raw, "ForecastOperatingProfit"))
    prev_profit = _decimal(_first(raw, "PreviousForecastProfit"))
    revised_profit = _decimal(_first(raw, "ForecastProfit"))
    prev_sales = _decimal(_first(raw, "PreviousForecastNetSales", "PreviousForecastSales"))
    revised_sales = _decimal(_first(raw, "ForecastNetSales", "ForecastSales"))

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
    return FundamentalFeaturesV0(
        eps_latest=feature(eps_latest, eps_latest is not None, **common),
        eps_ttm=feature(_decimal(raw.get("EpsTtm")), raw.get("EpsTtm") is not None, **common),
        previous_forecast_eps=feature(prev_eps, prev_eps is not None, **common),
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
            raw.get("AccountingStandard"),
            raw.get("AccountingStandard") is not None,
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
    valuation_price: Decimal,
    sector_medians: dict[str, Decimal],
    sector: str | None,
) -> ValuationFeaturesV0:
    eps_latest = _decimal(_first(raw, "EarningsPerShare", "EPS"))
    forecast_eps = _decimal(_first(raw, "ForecastEarningsPerShare", "ForecastEPS"))
    bps = _decimal(_first(raw, "BookValuePerShare", "BPS"))
    roe = _decimal(raw.get("ROE"))
    dividend = _decimal(_first(raw, "ForecastDividendPerShareAnnual", "ForecastDividend"))
    trailing_per = _per(valuation_price, eps_latest)
    forecast_per = _per(valuation_price, forecast_eps)
    pbr = None if bps is None or bps <= 0 else valuation_price / bps
    dividend_yield = (
        None if dividend is None or valuation_price <= 0 else dividend / valuation_price
    )
    earnings_yield = (
        None if forecast_eps is None or forecast_eps <= 0 else forecast_eps / valuation_price
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
    signal_idx: int,
    feature_cutoff_at: datetime,
    source_record_id: str,
) -> TechnicalContextV0:
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
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for event_type in sorted({obs.event_type for obs in observations}, key=str):
        subset = [obs for obs in observations if obs.event_type == event_type]
        for entry_arm in (
            EntryArm.EVENT_ONLY,
            EntryArm.EVENT_PLUS_FUNDAMENTAL,
            EntryArm.EVENT_PLUS_TECHNICAL,
            EntryArm.EVENT_PLUS_FUNDAMENTAL_PLUS_TECHNICAL,
        ):
            selected = [obs for obs in subset if entry_arm_allows(obs, entry_arm)]
            for exit_arm in EXIT_ARMS_FOR_REPORT:
                rows.append(
                    {
                        "event_type": event_type.value,
                        "entry_arm": entry_arm.value,
                        "exit_arm": exit_arm.value,
                        **metrics_for_observations(selected, exit_arm=exit_arm),
                    }
                )
    baselines = random_baselines(observations, seed_count=random_seed_count)
    return {"rows": rows, "random_baselines": baselines}


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


def fundamental_rule_allows(obs: ObservationRecord) -> bool:
    features = obs.fundamental_features_v0
    profit_pct = features.profit_revision_pct.value
    op_pct = features.operating_profit_revision_pct.value
    eps_abs = features.forecast_eps_revision_absolute.value
    revisions = [_as_decimal(value) for value in (profit_pct, op_pct, eps_abs)]
    return any(value is not None and value > 0 for value in revisions) or (
        obs.event_type == EventType.DIVIDEND_REVISION
    )


def technical_veto_allows(obs: ObservationRecord) -> bool:
    tech = obs.technical_context_v0
    avg_turnover = _as_decimal(tech.avg_turnover_20d.value)
    atr_pct = _as_decimal(tech.atr_pct_14d.value)
    return_20d = _as_decimal(tech.return_20d.value)
    regime = str(tech.market_regime.value or "")
    return (
        avg_turnover is not None
        and avg_turnover >= Decimal("200000000")
        and atr_pct is not None
        and Decimal("0.005") <= atr_pct <= Decimal("0.08")
        and (return_20d is None or return_20d < Decimal("0.30"))
        and regime != "broad_downtrend"
    )


def metrics_for_observations(
    observations: list[ObservationRecord],
    *,
    exit_arm: ExitArm,
) -> dict[str, Any]:
    horizon = _exit_horizon(exit_arm)
    return_key = _return_label_key(exit_arm, horizon)
    returns = [
        Decimal(str(obs.labels[return_key])) for obs in observations if return_key in obs.labels
    ]
    net_returns = [ret - ROUND_TRIP_COST_RATE for ret in returns]
    pnls = [DEFAULT_TRADE_NOTIONAL * ret for ret in net_returns]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    monthly = _monthly_pnls(observations, pnls)
    return {
        "event_count": len(observations),
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
        "block_stability": block_stability(observations, pnls),
        "clustered_bootstrap_ci": bootstrap_ci(pnls, seed=1),
    }


def random_baselines(observations: list[ObservationRecord], *, seed_count: int) -> dict[str, Any]:
    selected = _net_for_sample(observations, seed=0)
    baselines: dict[str, list[Decimal]] = {
        "same_symbol_random_date": [],
        "same_symbol_same_month_random_date": [],
        "same_symbol_same_regime_random_date": [],
        "same_sector_same_date_random": [],
        "event_type_matched_random": [],
    }
    for seed in range(1, seed_count + 1):
        rng = random.Random(seed)
        for name in baselines:
            sample = sample_random_baseline(observations, name=name, rng=rng)
            baselines[name].append(_net_for_sample(sample, seed=seed))
    return {name: random_summary(values, selected) for name, values in baselines.items()}


def sample_random_baseline(
    observations: list[ObservationRecord],
    *,
    name: str,
    rng: random.Random,
) -> list[ObservationRecord]:
    out: list[ObservationRecord] = []
    by_symbol: dict[str, list[ObservationRecord]] = defaultdict(list)
    by_sector_date: dict[tuple[str | None, str], list[ObservationRecord]] = defaultdict(list)
    by_event_type: dict[EventType, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        by_symbol[obs.symbol].append(obs)
        by_sector_date[(obs.sector, obs.signal_date)].append(obs)
        by_event_type[obs.event_type].append(obs)
    for obs in observations:
        if name in {
            "same_symbol_random_date",
            "same_symbol_same_month_random_date",
            "same_symbol_same_regime_random_date",
        }:
            pool = by_symbol[obs.symbol]
            if name == "same_symbol_same_month_random_date":
                pool = [item for item in pool if item.signal_date[:7] == obs.signal_date[:7]]
            if name == "same_symbol_same_regime_random_date":
                regime = obs.technical_context_v0.market_regime.value
                pool = [
                    item for item in pool if item.technical_context_v0.market_regime.value == regime
                ]
        elif name == "same_sector_same_date_random":
            pool = by_sector_date[(obs.sector, obs.signal_date)]
        elif name == "event_type_matched_random":
            pool = by_event_type[obs.event_type]
        else:
            raise ValueError(f"unknown baseline: {name}")
        out.append(rng.choice(pool) if pool else obs)
    return out


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


def split_manifest(observations: list[ObservationRecord]) -> dict[str, Any]:
    dates = sorted({date.fromisoformat(obs.signal_date) for obs in observations})
    if not dates:
        return {}
    train_end = dates[int(len(dates) * 0.60)]
    validation_start = _shift_trading_date(dates, train_end, PURGE_TRADING_DAYS)
    validation_end = dates[int(len(dates) * 0.80)]
    oos_start = _shift_trading_date(dates, validation_end, PURGE_TRADING_DAYS)
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
        "purge_days": PURGE_TRADING_DAYS,
        "dataset_hash": dataset_hash,
        "event_count": len(observations),
        "symbol_count": len({obs.symbol for obs in observations}),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def next_trading_date(trading_dates: list[date], current: date) -> date:
    for item in trading_dates:
        if item > current:
            return item
    raise ValueError(f"no trading date after {current}")


def disclosed_datetime(disclosed_date: date, disclosed_time: str | None) -> datetime:
    if disclosed_time:
        parts = [int(part) for part in disclosed_time.split(":")]
        return datetime.combine(disclosed_date, time(parts[0], parts[1], parts[2]), tzinfo=UTC)
    return datetime.combine(disclosed_date, time(23, 59, 59), tzinfo=UTC)


def event_cluster_id(symbol: str, disclosed_at: datetime) -> str:
    raw = f"{symbol}:{disclosed_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def event_record_id(symbol: str, disclosed_at: datetime, raw_id: str, event_type: EventType) -> str:
    raw = f"{symbol}:{disclosed_at.isoformat()}:{raw_id}:{event_type.value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def observation_id(event_id: str, execution_mode: ExecutionMode) -> str:
    return hashlib.sha256(f"{event_id}:{execution_mode.value}".encode()).hexdigest()[:24]


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
    values = list(by_month.values())
    return {
        "positive_block_ratio": None
        if not values
        else sum(1 for value in values if value > 0) / len(values),
        "worst_block_pnl": None if not values else float(min(values)),
        "median_block_pnl": None if not values else float(_median(values)),
        "event_count_per_block": {
            key: sum(1 for obs in observations if obs.entry_date[:7] == key) for key in by_month
        },
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


def _net_for_sample(observations: list[ObservationRecord], *, seed: int) -> Decimal:
    metrics = metrics_for_observations(observations, exit_arm=ExitArm.FIXED_10D)
    return Decimal(str(metrics["net_pnl"]))


def _shift_trading_date(dates: list[date], start: date, days: int) -> date:
    idx = dates.index(start)
    return dates[min(idx + days, len(dates) - 1)]
