from __future__ import annotations

import argparse
import importlib.util
import json
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

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


class _WatchlistResponse:
    status_code = 200
    text = ""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def json(self) -> list[dict[str, Any]]:
        return self._rows


class _WatchlistClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get(self, *_args, **_kwargs) -> _WatchlistResponse:
        return _WatchlistResponse(self._rows)


def test_watchlist_shape_counts_event_capture_but_excludes_it_from_oms(
    monkeypatch,
) -> None:
    scanner_rows = [
        {
            "symbol": f"{index:04d}",
            "selected_reasons": {
                "risk_penalty": 0,
                "volume_surge": 0,
                "momentum": 0,
            },
        }
        for index in range(20)
    ]
    event_row = {
        "symbol": "5074",
        "selected_reasons": {"event_capture": True},
    }
    received_symbols: list[str] = []
    monkeypatch.setattr(
        preopen,
        "_check_oms_live_allowed_symbols",
        lambda _reporter, _args, symbols: received_symbols.extend(symbols),
    )
    reporter = preopen.Reporter(quiet=True)
    args = argparse.Namespace(target_date=date(2026, 9, 3))

    preopen._check_watchlist_gate(
        reporter,
        args,
        _WatchlistClient([*scanner_rows, event_row]),
    )

    assert reporter.counts["NG"] == 0
    assert len(received_symbols) == 20
    assert "5074" not in received_symbols


def test_watchlist_shape_rejects_partial_scanner_result(monkeypatch) -> None:
    rows = [
        {
            "symbol": f"{index:04d}",
            "selected_reasons": {
                "risk_penalty": 0,
                "volume_surge": 0,
                "momentum": 0,
            },
        }
        for index in range(19)
    ]
    monkeypatch.setattr(preopen, "_check_oms_live_allowed_symbols", lambda *_args: None)
    reporter = preopen.Reporter(quiet=True)

    preopen._check_watchlist_gate(
        reporter,
        argparse.Namespace(target_date=date(2026, 9, 3)),
        _WatchlistClient(rows),
    )

    assert reporter.counts["NG"] == 1


def test_resolve_default_gcp_credentials_repairs_empty_directory_and_persists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "credentials" / "gcp.json"
    destination.mkdir(parents=True)
    monkeypatch.setattr(preopen, "DEFAULT_HOST_GCP_CREDENTIALS", destination)
    monkeypatch.setattr(
        preopen,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["op", "read"],
            returncode=0,
            stdout=json.dumps({"type": "service_account"}),
            stderr="",
        ),
    )
    reporter = preopen.Reporter(quiet=True)

    resolved, cleanup = preopen._resolve_gcp_credentials(
        reporter,
        argparse.Namespace(gcp_credentials=destination, timeout=30),
    )

    assert resolved == destination
    assert cleanup is None
    assert destination.is_file()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert reporter.counts["NG"] == 0
