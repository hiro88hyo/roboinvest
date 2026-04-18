import math
from datetime import date, timedelta
from typing import Any, cast

import polars as pl
from universe_scanner.filters.dynamic import (
    DynamicScoringConfig,
    score_candidates,
    to_watchlist_rows,
)


def _series(symbol: str, closes: list[float], volumes: list[int]) -> list[dict[str, Any]]:
    start = date(2026, 4, 20) - timedelta(days=len(closes) - 1)
    return [
        {
            "symbol": symbol,
            "date": start + timedelta(days=i),
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": volumes[i],
            "turnover": c * volumes[i],
        }
        for i, c in enumerate(closes)
    ]


def _candidates(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": s,
                "symbol_name": f"Name {s}",
                "market_segment": "プライム",
                "sector": None,
                "is_active": True,
            }
            for s in symbols
        ]
    )


def test_score_ranks_high_momentum_higher_than_flat():
    # A: 強い上昇モメンタム、B: 横ばい
    closes_a = [100.0 + i * 2 for i in range(25)]  # monotonically up
    closes_b = [100.0 for _ in range(25)]
    vols = [1000] * 25
    ohlcv = pl.DataFrame(_series("A", closes_a, vols) + _series("B", closes_b, vols))
    config = DynamicScoringConfig(top_n=2)

    result = score_candidates(candidates=_candidates(["A", "B"]), ohlcv=ohlcv, config=config)
    symbols = result.get_column("symbol").to_list()
    assert symbols[0] == "A"
    assert symbols[1] == "B"


def test_score_top_n_truncates():
    ohlcv_frames = []
    for i, sym in enumerate(["A", "B", "C", "D"]):
        closes = [100.0 + i + j for j in range(25)]
        ohlcv_frames += _series(sym, closes, [1000] * 25)
    ohlcv = pl.DataFrame(ohlcv_frames)

    result = score_candidates(
        candidates=_candidates(["A", "B", "C", "D"]),
        ohlcv=ohlcv,
        config=DynamicScoringConfig(top_n=2),
    )
    assert result.height == 2


def test_score_returns_empty_for_empty_candidates():
    ohlcv = pl.DataFrame(_series("A", [100.0] * 25, [1000] * 25))
    result = score_candidates(
        candidates=_candidates([]),
        ohlcv=ohlcv,
        config=DynamicScoringConfig(top_n=10),
    )
    assert result.is_empty()


def test_to_watchlist_rows_shape():
    scored = pl.DataFrame(
        [
            {
                "symbol": "7203",
                "symbol_name": "Toyota",
                "score": 1.23,
                "volatility": 0.02,
                "volume_surge": 1.5,
                "momentum": 0.1,
            }
        ]
    )
    rows = to_watchlist_rows(scored, valid_date_iso="2026-04-20")
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "7203"
    assert row["valid_date"] == "2026-04-20"
    assert math.isclose(cast(float, row["score"]), 1.23)
    reasons = row["selected_reasons"]
    assert isinstance(reasons, dict)
    assert set(reasons.keys()) == {"volatility", "volume_surge", "momentum"}
