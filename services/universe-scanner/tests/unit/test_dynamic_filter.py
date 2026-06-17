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


def test_risk_penalty_demotes_negative_momentum() -> None:
    closes_a = [100.0 + i for i in range(25)]
    closes_b = [150.0 - i for i in range(25)]
    vols = [1000] * 25
    ohlcv = pl.DataFrame(_series("A", closes_a, vols) + _series("B", closes_b, vols))

    result = score_candidates(
        candidates=_candidates(["A", "B"]),
        ohlcv=ohlcv,
        config=DynamicScoringConfig(
            top_n=2,
            weight_volatility=0.0,
            weight_volume_surge=0.0,
            weight_momentum=0.0,
            weight_risk_penalty=1.0,
            risk_negative_momentum_z_weight=2.0,
            risk_volatility_z_weight=0.0,
        ),
    )

    symbols = result.get_column("symbol").to_list()
    penalties = dict(zip(symbols, result.get_column("risk_penalty").to_list(), strict=True))
    assert symbols[0] == "A"
    assert penalties["B"] > penalties["A"]


def test_score_ignores_as_of_date_to_avoid_lookahead():
    ohlcv = pl.DataFrame(
        [
            {
                "symbol": "A",
                "date": d,
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000,
                "turnover": c * 1000,
            }
            for d, c in [
                (date(2026, 4, 13), 100.0),
                (date(2026, 4, 14), 100.0),
                (date(2026, 4, 15), 100.0),
                (date(2026, 4, 16), 100.0),
                (date(2026, 4, 17), 100.0),
                (date(2026, 4, 20), 10000.0),
            ]
        ]
    )

    result = score_candidates(
        candidates=_candidates(["A"]),
        ohlcv=ohlcv,
        config=DynamicScoringConfig(top_n=1, momentum_window=5),
        as_of=date(2026, 4, 20),
    )

    assert result.get_column("momentum").to_list() == [0.0]


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
                "opportunity_score": 1.5,
                "risk_penalty": 0.27,
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
    assert set(reasons.keys()) == {
        "opportunity_score",
        "risk_penalty",
        "volatility",
        "volume_surge",
        "momentum",
    }
