from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

import polars as pl


class MarketRegime(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK_OFF = "RISK_OFF"
    CRASH = "CRASH"


@dataclass(frozen=True, slots=True)
class RegimeScoringConfig:
    min_usable_symbols: int = 20
    big_down_return_threshold: float = -0.03
    moving_average_window: int = 25
    volume_window: int = 20
    high_volume_ratio_threshold: float = 1.5
    caution_avg_return_threshold: float = -0.01
    caution_down_ratio_threshold: float = 0.65
    caution_below_ma_ratio_threshold: float = 0.65
    caution_missing_ratio_threshold: float = 0.30
    risk_off_avg_return_threshold: float = -0.025
    risk_off_down_ratio_threshold: float = 0.75
    risk_off_big_down_ratio_threshold: float = 0.20
    crash_avg_return_threshold: float = -0.05
    crash_down_ratio_threshold: float = 0.90
    crash_big_down_ratio_threshold: float = 0.40


@dataclass(frozen=True, slots=True)
class RegimeMetrics:
    symbol_count: int
    usable_symbol_count: int
    missing_ratio: float
    avg_return_1d: float
    down_ratio: float
    big_down_ratio: float
    below_ma25_ratio: float
    high_volume_ratio: float


@dataclass(frozen=True, slots=True)
class RegimeDecision:
    market_regime: MarketRegime
    confidence: float
    buy_enabled: bool
    position_size_multiplier: float
    rationale: tuple[str, ...]
    metrics: RegimeMetrics


@dataclass(frozen=True, slots=True)
class _SymbolMetric:
    return_1d: float
    below_ma: bool
    high_volume: bool


def score_market_regime(
    *,
    ohlcv: pl.DataFrame,
    symbols: list[str] | None = None,
    config: RegimeScoringConfig | None = None,
) -> RegimeDecision:
    """Score market breadth from recent daily OHLCV.

    This intentionally uses only historical daily data. It is useful for
    "pre-existing weakness" but cannot detect same-morning futures/news shocks
    such as the 2026-06-08 risk-off paper day.
    """
    config = config or RegimeScoringConfig()
    target_symbols = _target_symbols(ohlcv=ohlcv, symbols=symbols)
    metrics = _collect_symbol_metrics(
        ohlcv=ohlcv,
        target_symbols=target_symbols,
        config=config,
    )
    aggregate = _aggregate_metrics(
        symbol_count=len(target_symbols),
        symbol_metrics=metrics,
        big_down_return_threshold=config.big_down_return_threshold,
    )
    regime, rationale = _classify(metrics=aggregate, config=config)
    confidence = _confidence(regime=regime, metrics=aggregate, config=config)
    return RegimeDecision(
        market_regime=regime,
        confidence=confidence,
        buy_enabled=regime not in {MarketRegime.RISK_OFF, MarketRegime.CRASH},
        position_size_multiplier=_position_size_multiplier(regime),
        rationale=tuple(rationale),
        metrics=aggregate,
    )


def _target_symbols(*, ohlcv: pl.DataFrame, symbols: list[str] | None) -> list[str]:
    if symbols is not None:
        return list(dict.fromkeys(symbols))
    if ohlcv.is_empty() or "symbol" not in ohlcv.columns:
        return []
    return [str(symbol) for symbol in ohlcv.get_column("symbol").unique().to_list()]


def _collect_symbol_metrics(
    *,
    ohlcv: pl.DataFrame,
    target_symbols: list[str],
    config: RegimeScoringConfig,
) -> list[_SymbolMetric]:
    if not target_symbols or ohlcv.is_empty():
        return []

    min_rows = max(config.moving_average_window, config.volume_window, 2)
    symbol_set = set(target_symbols)
    metrics: list[_SymbolMetric] = []
    for _symbol, rows in _rows_by_symbol(ohlcv=ohlcv, symbol_set=symbol_set).items():
        if len(rows) < min_rows:
            continue
        closes = [_safe_float(row["close"]) for row in rows]
        volumes = [_safe_float(row.get("volume", 0.0)) or 0.0 for row in rows]
        if any(value is None for value in closes[-min_rows:]):
            continue
        usable_closes = [value for value in closes if value is not None]
        if len(usable_closes) != len(closes):
            continue
        prev_close = closes[-2]
        latest_close = closes[-1]
        if prev_close is None or latest_close is None or prev_close <= 0:
            continue

        ma_closes = usable_closes[-config.moving_average_window :]
        volume_window = volumes[-config.volume_window :]
        average_volume = sum(volume_window) / len(volume_window) if volume_window else 0.0
        latest_volume = volume_window[-1] if volume_window else 0.0
        metrics.append(
            _SymbolMetric(
                return_1d=(latest_close / prev_close) - 1.0,
                below_ma=latest_close < (sum(ma_closes) / len(ma_closes)),
                high_volume=(
                    average_volume > 0
                    and latest_volume / average_volume >= config.high_volume_ratio_threshold
                ),
            )
        )
    return metrics


def _rows_by_symbol(
    *, ohlcv: pl.DataFrame, symbol_set: set[str]
) -> dict[str, list[dict[str, object]]]:
    rows_by_symbol: dict[str, list[dict[str, object]]] = {}
    for row in ohlcv.sort(["symbol", "date"]).to_dicts():
        symbol = str(row.get("symbol"))
        if symbol not in symbol_set:
            continue
        rows_by_symbol.setdefault(symbol, []).append(row)
    return rows_by_symbol


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed):
        return None
    return parsed


def _aggregate_metrics(
    *,
    symbol_count: int,
    symbol_metrics: list[_SymbolMetric],
    big_down_return_threshold: float,
) -> RegimeMetrics:
    usable_count = len(symbol_metrics)
    missing_ratio = 1.0 if symbol_count == 0 else 1.0 - (usable_count / symbol_count)
    if usable_count == 0:
        return RegimeMetrics(
            symbol_count=symbol_count,
            usable_symbol_count=0,
            missing_ratio=missing_ratio,
            avg_return_1d=0.0,
            down_ratio=0.0,
            big_down_ratio=0.0,
            below_ma25_ratio=0.0,
            high_volume_ratio=0.0,
        )

    return RegimeMetrics(
        symbol_count=symbol_count,
        usable_symbol_count=usable_count,
        missing_ratio=missing_ratio,
        avg_return_1d=sum(metric.return_1d for metric in symbol_metrics) / usable_count,
        down_ratio=sum(1 for metric in symbol_metrics if metric.return_1d < 0) / usable_count,
        big_down_ratio=sum(
            1 for metric in symbol_metrics if metric.return_1d <= big_down_return_threshold
        )
        / usable_count,
        below_ma25_ratio=sum(1 for metric in symbol_metrics if metric.below_ma) / usable_count,
        high_volume_ratio=sum(1 for metric in symbol_metrics if metric.high_volume) / usable_count,
    )


def _classify(
    *, metrics: RegimeMetrics, config: RegimeScoringConfig
) -> tuple[MarketRegime, list[str]]:
    rationale: list[str] = []

    if metrics.usable_symbol_count < config.min_usable_symbols:
        rationale.append(
            f"usable_symbol_count={metrics.usable_symbol_count} < {config.min_usable_symbols}"
        )
        return MarketRegime.CAUTION, rationale

    if metrics.avg_return_1d <= config.crash_avg_return_threshold or (
        metrics.down_ratio >= config.crash_down_ratio_threshold
        and metrics.big_down_ratio >= config.crash_big_down_ratio_threshold
    ):
        _append_threshold_rationale(rationale=rationale, metrics=metrics, config=config)
        return MarketRegime.CRASH, rationale

    if metrics.avg_return_1d <= config.risk_off_avg_return_threshold or (
        metrics.down_ratio >= config.risk_off_down_ratio_threshold
        and metrics.big_down_ratio >= config.risk_off_big_down_ratio_threshold
    ):
        _append_threshold_rationale(rationale=rationale, metrics=metrics, config=config)
        return MarketRegime.RISK_OFF, rationale

    if (
        metrics.avg_return_1d <= config.caution_avg_return_threshold
        or metrics.down_ratio >= config.caution_down_ratio_threshold
        or metrics.below_ma25_ratio >= config.caution_below_ma_ratio_threshold
        or metrics.missing_ratio >= config.caution_missing_ratio_threshold
    ):
        _append_threshold_rationale(rationale=rationale, metrics=metrics, config=config)
        return MarketRegime.CAUTION, rationale

    rationale.append("breadth not weak enough for caution")
    return MarketRegime.NORMAL, rationale


def _append_threshold_rationale(
    *, rationale: list[str], metrics: RegimeMetrics, config: RegimeScoringConfig
) -> None:
    if metrics.avg_return_1d <= config.caution_avg_return_threshold:
        rationale.append(f"avg_return_1d={metrics.avg_return_1d:.4f}")
    if metrics.down_ratio >= config.caution_down_ratio_threshold:
        rationale.append(f"down_ratio={metrics.down_ratio:.3f}")
    if metrics.big_down_ratio >= config.risk_off_big_down_ratio_threshold:
        rationale.append(f"big_down_ratio={metrics.big_down_ratio:.3f}")
    if metrics.below_ma25_ratio >= config.caution_below_ma_ratio_threshold:
        rationale.append(f"below_ma25_ratio={metrics.below_ma25_ratio:.3f}")
    if metrics.missing_ratio >= config.caution_missing_ratio_threshold:
        rationale.append(f"missing_ratio={metrics.missing_ratio:.3f}")


def _confidence(
    *, regime: MarketRegime, metrics: RegimeMetrics, config: RegimeScoringConfig
) -> float:
    if regime is MarketRegime.NORMAL:
        return 0.5
    if regime is MarketRegime.CAUTION:
        severity = max(
            metrics.down_ratio / config.caution_down_ratio_threshold,
            abs(metrics.avg_return_1d / config.caution_avg_return_threshold)
            if metrics.avg_return_1d < 0
            else 0.0,
            metrics.below_ma25_ratio / config.caution_below_ma_ratio_threshold,
            metrics.missing_ratio / config.caution_missing_ratio_threshold,
        )
    elif regime is MarketRegime.RISK_OFF:
        severity = max(
            metrics.down_ratio / config.risk_off_down_ratio_threshold,
            metrics.big_down_ratio / config.risk_off_big_down_ratio_threshold,
            abs(metrics.avg_return_1d / config.risk_off_avg_return_threshold)
            if metrics.avg_return_1d < 0
            else 0.0,
        )
    else:
        severity = max(
            metrics.down_ratio / config.crash_down_ratio_threshold,
            metrics.big_down_ratio / config.crash_big_down_ratio_threshold,
            abs(metrics.avg_return_1d / config.crash_avg_return_threshold)
            if metrics.avg_return_1d < 0
            else 0.0,
        )
    return min(1.0, max(0.5, severity * 0.5))


def _position_size_multiplier(regime: MarketRegime) -> float:
    if regime is MarketRegime.NORMAL:
        return 1.0
    if regime is MarketRegime.CAUTION:
        return 0.5
    return 0.0
