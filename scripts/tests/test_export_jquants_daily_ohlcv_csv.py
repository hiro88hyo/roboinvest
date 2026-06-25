from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "export-jquants-daily-ohlcv-csv.py"
    spec = importlib.util.spec_from_file_location("export_jquants_daily_ohlcv_csv", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load_module()


def test_iter_dates_includes_bounds() -> None:
    assert list(exporter.iter_dates(date(2026, 1, 1), date(2026, 1, 3))) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]


def test_read_existing_dates_returns_unique_csv_dates(tmp_path: Path) -> None:
    path = tmp_path / "daily.csv"
    path.write_text(
        "symbol,date,open,high,low,close,volume,turnover\n"
        "7203,2026-01-05,100,110,95,105,1000,105000\n"
        "6758,2026-01-05,200,210,195,205,1000,205000\n"
        "9984,2026-01-06,300,310,295,305,1000,305000\n",
        encoding="utf-8",
    )

    assert exporter.read_existing_dates(path) == {"2026-01-05", "2026-01-06"}


def test_read_existing_dates_tolerates_missing_file(tmp_path: Path) -> None:
    assert exporter.read_existing_dates(tmp_path / "missing.csv") == set()
