from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from trade_contracts.logging import event_extra

from .calendar import is_tse_business_day
from .clients.jquants import JQuantsApiVersion as ClientApiVersion
from .clients.jquants import JQuantsClient
from .clients.supabase import SupabaseWriter
from .config import ScannerSettings
from .filters.dynamic import DynamicScoringConfig, score_candidates, to_watchlist_rows
from .filters.static import StaticFilterConfig, apply_static_filter
from .ingest.daily_ohlcv import ingest_daily_ohlcv
from .ingest.master_stocks import ingest_master_stocks
from .regime import RegimeDecision, score_market_regime

logger = logging.getLogger(__name__)


class EmptyWatchlistError(RuntimeError):
    """スコアリング後に候補が 0 件になった場合に送出。

    CLAUDE.md 方針: 空の watchlist は絶対に書かない。前日リストが保持される。
    """


@dataclass(frozen=True, slots=True)
class PipelineResult:
    valid_date: date
    watchlist_size: int


async def run_pipeline(
    *,
    settings: ScannerSettings,
    target_date: date,
) -> PipelineResult:
    """Universe Scanner の 1 回分を実行する。

    - `target_date` が非営業日なら `SkipNonBusinessDay` を送出せず早期 return する
    - J-Quants から master/ohlcv を取得 → Supabase に upsert
    - 2段階フィルタを適用 → watchlist に upsert
    """
    if not is_tse_business_day(target_date):
        logger.info("skip: %s is not a TSE business day", target_date)
        return PipelineResult(valid_date=target_date, watchlist_size=0)

    static_config = StaticFilterConfig(
        min_turnover_jpy=settings.scan_static_min_turnover_jpy,
        price_min=settings.scan_static_price_min,
        price_max=settings.scan_static_price_max,
        allowed_segments=settings.scan_static_market_segments,
    )
    scoring_config = DynamicScoringConfig(
        top_n=settings.scan_dynamic_top_n,
        weight_volatility=settings.scan_weight_volatility,
        weight_volume_surge=settings.scan_weight_volume_surge,
        weight_momentum=settings.scan_weight_momentum,
    )

    async with (
        JQuantsClient(
            refresh_token=settings.jquants_refresh_token,
            api_key=settings.jquants_api_key,
            api_version=ClientApiVersion(settings.jquants_api_version),
            base_url=settings.jquants_api_base,
        ) as jquants,
        SupabaseWriter(
            url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
        ) as supabase,
    ):
        master = await ingest_master_stocks(jquants, supabase, as_of=target_date)
        ohlcv = await ingest_daily_ohlcv(
            jquants,
            supabase,
            as_of=target_date,
            lookback_days=settings.scan_lookback_days,
        )

        candidates = apply_static_filter(master=master, ohlcv=ohlcv, config=static_config)
        logger.info("static filter: candidates=%d", candidates.height)

        scored = score_candidates(
            candidates=candidates,
            ohlcv=ohlcv,
            config=scoring_config,
            as_of=target_date,
        )
        if scored.is_empty():
            raise EmptyWatchlistError(
                f"watchlist would be empty for {target_date}; keeping previous day's list"
            )

        rows = to_watchlist_rows(scored, valid_date_iso=target_date.isoformat())
        if settings.market_regime_enabled:
            symbols = [str(row["symbol"]) for row in rows]
            regime = score_market_regime(ohlcv=ohlcv, symbols=symbols)
            regime_row = market_regime_row(
                decision=regime,
                valid_date_iso=target_date.isoformat(),
            )
            logger.info(
                "market_regime_candidate: valid_date=%s regime=%s confidence=%.3f "
                "buy_enabled=%s source=daily_ohlcv",
                target_date,
                regime.market_regime.value,
                regime.confidence,
                regime.buy_enabled,
                extra=event_extra(
                    "market_regime_candidate",
                    valid_date=target_date.isoformat(),
                    market_regime=regime.market_regime.value,
                    confidence=regime.confidence,
                    buy_enabled=regime.buy_enabled,
                    position_size_multiplier=regime.position_size_multiplier,
                    source="universe_scanner_daily_ohlcv",
                    metrics=regime_row["metrics"],
                    rationale=regime_row["rationale"],
                    write_enabled=settings.market_regime_write_enabled,
                ),
            )
            if settings.market_regime_write_enabled:
                await supabase.upsert(
                    "market_regime",
                    [regime_row],
                    on_conflict="valid_date",
                )
            else:
                logger.info(
                    "market_regime write skipped: valid_date=%s regime=%s write_enabled=false",
                    target_date,
                    regime.market_regime.value,
                )
        await supabase.delete_where(
            "watchlist",
            filters={"valid_date": f"eq.{target_date.isoformat()}"},
        )
        await supabase.upsert("watchlist", rows, on_conflict="symbol,valid_date")

    return PipelineResult(valid_date=target_date, watchlist_size=len(rows))


def market_regime_row(*, decision: RegimeDecision, valid_date_iso: str) -> dict[str, object]:
    metrics = decision.metrics
    return {
        "valid_date": valid_date_iso,
        "regime": decision.market_regime.value,
        "confidence": decision.confidence,
        "buy_enabled": decision.buy_enabled,
        "position_size_multiplier": decision.position_size_multiplier,
        "metrics": {
            "symbol_count": metrics.symbol_count,
            "usable_symbol_count": metrics.usable_symbol_count,
            "missing_ratio": metrics.missing_ratio,
            "avg_return_1d": metrics.avg_return_1d,
            "down_ratio": metrics.down_ratio,
            "big_down_ratio": metrics.big_down_ratio,
            "below_ma25_ratio": metrics.below_ma25_ratio,
            "high_volume_ratio": metrics.high_volume_ratio,
        },
        "rationale": list(decision.rationale),
        "source": "universe_scanner_daily_ohlcv",
    }
