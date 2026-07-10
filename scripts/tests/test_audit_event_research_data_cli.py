from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "audit-event-research-data.py"
    spec = importlib.util.spec_from_file_location("audit_event_research_data", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_event_research_data = _load_module()


def test_financial_row_count_excludes_fetch_metadata(tmp_path: Path) -> None:
    path = tmp_path / "financial.jsonl"
    path.write_text(
        '{"Code":"72030","DiscDate":"2026-01-21"}\n'
        '{"_roboinvest_record_type":"fetch_metadata",'
        '"_roboinvest_target_date":"2026-01-21",'
        '"_roboinvest_row_count":1}\n',
        encoding="utf-8",
    )

    assert audit_event_research_data._count_financial_jsonl(path) == (1, 1)
