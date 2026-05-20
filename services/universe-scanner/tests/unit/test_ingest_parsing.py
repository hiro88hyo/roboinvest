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


def test_listed_info_to_frame_accepts_v2_master_columns():
    rows = [
        {
            "Code": "72030",
            "CoName": "トヨタ自動車",
            "MktNm": "プライム",
            "S17Nm": "自動車・輸送機",
        }
    ]
    df = listed_info_to_frame(rows)
    assert df.get_column("symbol").to_list() == ["7203"]
    assert df.get_column("symbol_name").to_list() == ["トヨタ自動車"]
    assert df.get_column("market_segment").to_list() == ["プライム"]
    assert df.get_column("sector").to_list() == ["自動車・輸送機"]


def test_listed_info_to_frame_deduplicates_legacy_and_normalized_symbols():
    rows = [
        {"Code": "72030", "CoName": "トヨタA", "MktNm": "プライム", "S17Nm": "自動車・輸送機"},
        {"Code": "7203", "CoName": "トヨタB", "MktNm": "プライム", "S17Nm": "自動車・輸送機"},
    ]
    df = listed_info_to_frame(rows)
    assert df.height == 1
    assert df.get_column("symbol").to_list() == ["7203"]


def test_daily_quotes_skips_null_close():
    rows: list[dict[str, Any]] = [
        {
            "Code": "72030",
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
            "Close": None,
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


def test_daily_quotes_to_frame_accepts_v2_bar_columns():
    rows: list[dict[str, Any]] = [
        {
            "Code": "278A0",
            "Date": "2026-04-20",
            "O": 1000.0,
            "H": 1010.0,
            "L": 990.0,
            "C": 1005.0,
            "Vo": 100000,
            "Va": 100500000.0,
        }
    ]
    df = daily_quotes_to_frame(rows)
    assert df.height == 1
    assert df.get_column("symbol").to_list() == ["278A"]
    assert df.get_column("open").to_list() == [1000.0]
    assert df.get_column("close").to_list() == [1005.0]
    assert df.get_column("volume").to_list() == [100000]
    assert df.get_column("turnover").to_list() == [100500000.0]


def test_daily_quotes_to_frame_deduplicates_legacy_and_normalized_symbols():
    rows: list[dict[str, Any]] = [
        {
            "Code": "278A0",
            "Date": "2026-04-20",
            "Close": 1005.0,
            "Volume": 100000,
            "TurnoverValue": 100500000.0,
        },
        {
            "Code": "278A",
            "Date": "2026-04-20",
            "Close": 1006.0,
            "Volume": 100100,
            "TurnoverValue": 100600000.0,
        },
    ]
    df = daily_quotes_to_frame(rows)
    assert df.height == 1
    assert df.get_column("symbol").to_list() == ["278A"]
