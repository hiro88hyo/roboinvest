from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "production-preopen-check.py"
    spec = importlib.util.spec_from_file_location("production_preopen_check_calendar", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preopen = _load_module()


def test_scheduled_preopen_skips_non_business_day(monkeypatch, capsys) -> None:
    args = argparse.Namespace(
        quiet=True,
        skip_non_business_day=True,
        target_date=date(2026, 8, 8),
    )
    monkeypatch.setattr(preopen, "parse_args", lambda: args)

    def unexpected_check(*_args, **_kwargs) -> None:
        pytest.fail("production checks must not run on a non-business day")

    monkeypatch.setattr(preopen, "check_expected_env", unexpected_check)

    assert preopen.main() == 0
    output = capsys.readouterr().out
    assert "non-business-day target_date=2026-08-08" in output
    assert "SKIP 1" in output
