from typing import Any

from universe_scanner.ingest.daily_ohlcv import daily_quotes_to_frame
from universe_scanner.ingest.master_stocks import listed_info_to_frame


def test_listed_info_to_frame_normalizes_segment():
    rows = [
        {
            "Code": "7203",
            "CompanyName": "トヨタ自動車",
            "MarketCodeName": "プライム（内国株式）",  # noqa: RUF001
            "Sector17CodeName": "自動車・輸送機",
        },
        {
            "Code": "4385",
            "CompanyName": "メルカリ",
            "MarketCodeName": "グロース（内国株式）",  # noqa: RUF001
            "Sector17CodeName": "商業・サービス",
        },
    ]
    df = listed_info_to_frame(rows)
    assert df.height == 2
    segs = df.get_column("market_segment").to_list()
    assert segs == ["プライム", "グロース"]
    assert df.get_column("is_active").to_list() == [True, True]


def test_listed_info_to_frame_empty():
    df = listed_info_to_frame([])
    assert df.is_empty()
    assert set(df.columns) == {"symbol", "symbol_name", "market_segment", "sector", "is_active"}


def test_daily_quotes_skips_null_close():
    rows: list[dict[str, Any]] = [
        {
            "Code": "7203",
            "Date": "2026-04-20",
            "Open": 1000.0,
            "High": 1010.0,
            "Low": 990.0,
            "Close": 1005.0,
            "Volume": 100000,
            "TurnoverValue": 100500000.0,
        },
        {
            "Code": "9999",
            "Date": "2026-04-20",
            "Open": None,
            "High": None,
            "Low": None,
            "Close": None,  # 売買停止日など
            "Volume": 0,
            "TurnoverValue": 0.0,
        },
    ]
    df = daily_quotes_to_frame(rows)
    assert df.height == 1
    assert df.get_column("symbol").to_list() == ["7203"]


def test_daily_quotes_empty():
    df = daily_quotes_to_frame([])
    assert df.is_empty()
    assert "symbol" in df.columns
    assert "date" in df.columns
