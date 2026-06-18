from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import polars as pl
from universe_scanner.filters.static import StaticFilterConfig, apply_static_filter


def _ohlcv_rows(symbol: str, *, days: int, close: float, turnover: float) -> list[dict[str, Any]]:
    base = date(2026, 4, 20)
    return [
        {
            "symbol": symbol,
            "date": base - timedelta(days=days - 1 - i),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "turnover": turnover,
        }
        for i in range(days)
    ]


def _make_master() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": "1111",
                "symbol_name": "Liquid Co",
                "market_segment": "プライム",
                "sector": "情報通信",
                "is_active": True,
            },
            {
                "symbol": "2222",
                "symbol_name": "Illiquid Co",
                "market_segment": "プライム",
                "sector": "情報通信",
                "is_active": True,
            },
            {
                "symbol": "3333",
                "symbol_name": "Cheap Co",
                "market_segment": "プライム",
                "sector": "情報通信",
                "is_active": True,
            },
            {
                "symbol": "4444",
                "symbol_name": "Delisted Co",
                "market_segment": "プライム",
                "sector": "情報通信",
                "is_active": False,
            },
            {
                "symbol": "5555",
                "symbol_name": "Tokyo Pro Co",
                "market_segment": "TOKYO PRO",
                "sector": "情報通信",
                "is_active": True,
            },
            {
                "symbol": "6666",
                "symbol_name": "Expensive Co",
                "market_segment": "プライム",
                "sector": "情報通信",
                "is_active": True,
            },
        ]
    )


def _make_ohlcv() -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    rows += _ohlcv_rows("1111", days=20, close=1000.0, turnover=5e8)  # pass
    rows += _ohlcv_rows("2222", days=20, close=1000.0, turnover=1e7)  # fail: low turnover
    rows += _ohlcv_rows("3333", days=20, close=100.0, turnover=5e8)  # fail: price < min
    rows += _ohlcv_rows("4444", days=20, close=1000.0, turnover=5e8)  # fail: inactive
    rows += _ohlcv_rows("5555", days=20, close=1000.0, turnover=5e8)  # fail: segment
    rows += _ohlcv_rows("6666", days=20, close=6000.0, turnover=5e8)  # fail: min lot notional
    return pl.DataFrame(rows)


def _config() -> StaticFilterConfig:
    return StaticFilterConfig(
        min_turnover_jpy=Decimal("100000000"),
        price_min=Decimal("300"),
        price_max=Decimal("5000"),
        allowed_segments=("プライム", "スタンダード", "グロース"),
        min_lot_size=100,
        max_min_lot_notional_jpy=Decimal("500000"),
    )


def test_static_filter_retains_only_qualifying_symbols():
    result = apply_static_filter(master=_make_master(), ohlcv=_make_ohlcv(), config=_config())
    assert result.get_column("symbol").to_list() == ["1111"]


def test_static_filter_empty_inputs():
    master = _make_master()
    empty = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "turnover": pl.Float64,
        }
    )
    assert apply_static_filter(master=master, ohlcv=empty, config=_config()).is_empty()


def test_static_filter_requires_latest_date_present():
    # `2222` だけ最新日 (4/20) の行を欠落させ、古い日付のみ持つ → フィルタ対象外
    rows = _ohlcv_rows("1111", days=20, close=1000.0, turnover=5e8)
    rows += _ohlcv_rows("2222", days=20, close=1000.0, turnover=5e8)
    df = pl.DataFrame(rows)
    df = df.filter(~((pl.col("symbol") == "2222") & (pl.col("date") == date(2026, 4, 20))))
    result = apply_static_filter(master=_make_master(), ohlcv=df, config=_config())
    assert result.get_column("symbol").to_list() == ["1111"]
