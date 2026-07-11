from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "check-profitability-review.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_profitability_review", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profitability_review_package_is_consistent() -> None:
    module = _load_script()
    assert module.validate_review_package() == []
