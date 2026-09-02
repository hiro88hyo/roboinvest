from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from trade_contracts.scanner_gate import ScannerGateThresholds

from scripts.sync_oms_live_allowed_symbols import (
    gate_pass_symbols,
    update_env_file,
    validate_watchlist_rows,
)


def test_gate_pass_symbols_filters_by_shared_scanner_gate() -> None:
    thresholds = ScannerGateThresholds(
        max_risk_penalty=Decimal("1.5"),
        max_volume_surge=Decimal("2.1"),
        max_momentum=Decimal("0.4"),
    )
    rows = [
        {
            "symbol": "166A",
            "selected_reasons": {"risk_penalty": 1.0, "volume_surge": 1.5, "momentum": 0.1},
        },
        {
            "symbol": "7203",
            "selected_reasons": {"risk_penalty": 2.0, "volume_surge": 1.5, "momentum": 0.1},
        },
        {
            "symbol": "9984",
            "selected_reasons": {"risk_penalty": 1.0, "volume_surge": 1.5, "momentum": 0.5},
        },
        {
            "symbol": "5074",
            "selected_reasons": {"event_capture": True},
        },
    ]

    assert gate_pass_symbols(rows, thresholds) == ["166A"]


def test_validate_watchlist_rows_accepts_scanner_plus_event_capture() -> None:
    rows = [{"symbol": str(index), "selected_reasons": {"risk_penalty": 0}} for index in range(20)]
    rows.append({"symbol": "5074", "selected_reasons": {"event_capture": True}})

    validate_watchlist_rows(date(2026, 9, 3), rows)


def test_validate_watchlist_rows_rejects_empty_business_date() -> None:
    with pytest.raises(RuntimeError, match="watchlist empty"):
        validate_watchlist_rows(date(2026, 9, 3), [])


def test_validate_watchlist_rows_rejects_too_few_scanner_symbols() -> None:
    rows = [{"symbol": str(index), "selected_reasons": {"risk_penalty": 0}} for index in range(19)]

    with pytest.raises(RuntimeError, match="outside 20-50"):
        validate_watchlist_rows(date(2026, 9, 3), rows)


def test_update_env_file_updates_allowed_symbols(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / "env.production"
    env_file.write_text("A=1\nOMS_LIVE_ALLOWED_SYMBOLS=7203\nB=2\n", encoding="utf-8")

    result = update_env_file(env_file, ["166A", "3399"])

    assert result.status == "updated"
    assert env_file.read_text(encoding="utf-8") == (
        "A=1\nOMS_LIVE_ALLOWED_SYMBOLS=166A,3399\nB=2\n"
    )


def test_update_env_file_reports_unchanged(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / "env.production"
    env_file.write_text("OMS_LIVE_ALLOWED_SYMBOLS=166A,3399\n", encoding="utf-8")

    result = update_env_file(env_file, ["166A", "3399"])

    assert result.status == "unchanged"
    assert env_file.read_text(encoding="utf-8") == "OMS_LIVE_ALLOWED_SYMBOLS=166A,3399\n"


def test_update_env_file_requires_existing_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / "env.production"
    env_file.write_text("A=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="OMS_LIVE_ALLOWED_SYMBOLS"):
        update_env_file(env_file, ["166A"])
