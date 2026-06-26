from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "export-jquants-financial-summaries-jsonl.py"
    spec = importlib.util.spec_from_file_location(
        "export_jquants_financial_summaries_jsonl",
        path,
    )
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


def test_read_existing_disclosed_dates_returns_unique_dates(tmp_path: Path) -> None:
    path = tmp_path / "fins.jsonl"
    path.write_text(
        '{"Code":"72030","DisclosedDate":"2026-01-05","DisclosureNumber":"a"}\n'
        '{"Code":"67580","DisclosedDate":"2026-01-05","DisclosureNumber":"b"}\n'
        '{"Code":"99840","Date":"2026-01-06","DisclosureNumber":"c"}\n'
        '{"Code":"31860","DiscDate":"2026-01-07","DiscNo":"d"}\n',
        encoding="utf-8",
    )

    assert exporter.read_existing_disclosed_dates(path) == {
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
    }


def test_read_existing_disclosed_dates_tolerates_missing_file(tmp_path: Path) -> None:
    assert exporter.read_existing_disclosed_dates(tmp_path / "missing.jsonl") == set()
