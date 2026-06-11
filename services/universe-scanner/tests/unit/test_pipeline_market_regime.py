from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest
from universe_scanner.config import ScannerSettings
from universe_scanner.pipeline import market_regime_row, run_pipeline
from universe_scanner.regime import MarketRegime, RegimeDecision, RegimeMetrics


def _settings(**overrides: Any) -> ScannerSettings:
    base: dict[str, Any] = {
        "supabase_url": "https://example.supabase.co",
        "supabase_secret_key": "k",
        "jquants_api_key": "jquants-key",
    }
    base.update(overrides)
    return ScannerSettings(**base)


def test_market_regime_row_maps_decision_to_supabase_payload() -> None:
    decision = RegimeDecision(
        market_regime=MarketRegime.RISK_OFF,
        confidence=0.86,
        buy_enabled=False,
        position_size_multiplier=0.25,
        rationale=("down_ratio=0.820",),
        metrics=RegimeMetrics(
            symbol_count=30,
            usable_symbol_count=28,
            missing_ratio=0.066,
            avg_return_1d=-0.031,
            down_ratio=0.82,
            big_down_ratio=0.25,
            below_ma25_ratio=0.70,
            high_volume_ratio=0.40,
        ),
    )

    row = market_regime_row(decision=decision, valid_date_iso="2026-06-11")

    assert row["valid_date"] == "2026-06-11"
    assert row["regime"] == "RISK_OFF"
    assert row["confidence"] == 0.86
    assert row["buy_enabled"] is False
    assert row["position_size_multiplier"] == 0.25
    assert row["rationale"] == ["down_ratio=0.820"]
    assert row["source"] == "universe_scanner_daily_ohlcv"
    assert row["metrics"] == {
        "symbol_count": 30,
        "usable_symbol_count": 28,
        "missing_ratio": 0.066,
        "avg_return_1d": -0.031,
        "down_ratio": 0.82,
        "big_down_ratio": 0.25,
        "below_ma25_ratio": 0.70,
        "high_volume_ratio": 0.40,
    }


@pytest.mark.parametrize(
    ("write_enabled", "expected_regime_writes"),
    [(False, 0), (True, 1)],
)
async def test_run_pipeline_scores_market_regime_log_only_by_default(
    monkeypatch: pytest.MonkeyPatch,
    write_enabled: bool,
    expected_regime_writes: int,
) -> None:
    writes: list[tuple[str, list[dict[str, Any]], str]] = []
    deletes: list[tuple[str, dict[str, str]]] = []

    class _FakeJQuants:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeJQuants:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _FakeSupabase:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeSupabase:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def upsert(
            self,
            table: str,
            rows: list[dict[str, Any]],
            *,
            on_conflict: str,
        ) -> None:
            writes.append((table, rows, on_conflict))

        async def delete_where(self, table: str, *, filters: dict[str, str]) -> None:
            deletes.append((table, filters))

    async def _fake_master(*_: Any, **__: Any) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["7203"], "symbol_name": ["Toyota"]})

    async def _fake_ohlcv(*_: Any, **__: Any) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["7203"], "date": ["2026-06-10"], "close": [2500]})

    def _fake_static_filter(*_: Any, **__: Any) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["7203"], "symbol_name": ["Toyota"]})

    def _fake_score_candidates(*_: Any, **__: Any) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": ["7203"],
                "symbol_name": ["Toyota"],
                "score": [1.0],
                "volatility": [0.1],
                "volume_surge": [1.2],
                "momentum": [0.03],
            }
        )

    def _fake_regime(*_: Any, **__: Any) -> RegimeDecision:
        return RegimeDecision(
            market_regime=MarketRegime.RISK_OFF,
            confidence=0.86,
            buy_enabled=False,
            position_size_multiplier=0.25,
            rationale=("test risk-off",),
            metrics=RegimeMetrics(
                symbol_count=1,
                usable_symbol_count=1,
                missing_ratio=0.0,
                avg_return_1d=-0.04,
                down_ratio=1.0,
                big_down_ratio=1.0,
                below_ma25_ratio=1.0,
                high_volume_ratio=0.0,
            ),
        )

    import universe_scanner.pipeline as pipeline

    monkeypatch.setattr(pipeline, "JQuantsClient", _FakeJQuants)
    monkeypatch.setattr(pipeline, "SupabaseWriter", _FakeSupabase)
    monkeypatch.setattr(pipeline, "ingest_master_stocks", _fake_master)
    monkeypatch.setattr(pipeline, "ingest_daily_ohlcv", _fake_ohlcv)
    monkeypatch.setattr(pipeline, "apply_static_filter", _fake_static_filter)
    monkeypatch.setattr(pipeline, "score_candidates", _fake_score_candidates)
    monkeypatch.setattr(pipeline, "score_market_regime", _fake_regime)

    result = await run_pipeline(
        settings=_settings(market_regime_write_enabled=write_enabled),
        target_date=date(2026, 6, 11),
    )

    assert result.watchlist_size == 1
    assert deletes == [("watchlist", {"valid_date": "eq.2026-06-11"})]
    assert [table for table, _, _ in writes].count("market_regime") == expected_regime_writes
    assert [table for table, _, _ in writes].count("watchlist") == 1
    if write_enabled:
        regime_write = next(write for write in writes if write[0] == "market_regime")
        assert regime_write[2] == "valid_date"
        assert regime_write[1][0]["regime"] == "RISK_OFF"
