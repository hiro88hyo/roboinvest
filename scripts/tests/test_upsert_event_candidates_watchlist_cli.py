from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx


def _load_module():
    path = Path(__file__).resolve().parents[1] / "upsert-event-candidates-watchlist.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("upsert_event_candidates_watchlist", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


upsert_event_candidates_watchlist = _load_module()


def _payload() -> dict[str, Any]:
    return {
        "candidate_id": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
        "candidates": [
            {
                "candidate_id": (
                    "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
                ),
                "cluster_id": "cluster-1",
                "symbol": "7203",
                "symbol_name": "Toyota",
                "signal_date": "2026-01-20",
                "entry_date": "2026-01-21",
            },
            {
                "candidate_id": (
                    "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
                ),
                "cluster_id": "cluster-2",
                "symbol": "9984",
                "symbol_name": "SoftBank",
                "signal_date": "2026-01-20",
                "entry_date": "2026-01-21",
            },
        ],
    }


def test_build_watchlist_rows_uses_entry_date_and_event_capture_reason() -> None:
    rows = upsert_event_candidates_watchlist.build_watchlist_rows(
        _payload(),
        valid_date=None,
        max_symbols=10,
    )

    assert [(row["valid_date"], row["symbol"]) for row in rows] == [
        ("2026-01-21", "7203"),
        ("2026-01-21", "9984"),
    ]
    assert rows[0]["score"] == 0
    assert rows[0]["selected_reasons"]["reasons"] == ["event_capture"]
    assert rows[0]["selected_reasons"]["event_capture"] is True


def test_build_watchlist_rows_can_override_valid_date_and_cap_symbols() -> None:
    rows = upsert_event_candidates_watchlist.build_watchlist_rows(
        _payload(),
        valid_date=date(2026, 1, 22),
        max_symbols=1,
    )

    assert [(row["valid_date"], row["symbol"]) for row in rows] == [("2026-01-22", "7203")]


def test_upsert_missing_watchlist_rows_skips_existing_symbol() -> None:
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/rest/v1/watchlist"
            assert request.url.params["valid_date"] == "eq.2026-01-21"
            return httpx.Response(200, json=[{"symbol": "7203"}])
        if request.method == "POST":
            assert request.url.path == "/rest/v1/watchlist"
            return httpx.Response(201, json=[])
        return httpx.Response(404)

    rows = upsert_event_candidates_watchlist.build_watchlist_rows(
        _payload(),
        valid_date=None,
        max_symbols=10,
    )
    with httpx.Client(
        base_url="https://example.supabase.co",
        transport=httpx.MockTransport(_handler),
    ) as client:
        inserted, skipped = upsert_event_candidates_watchlist.upsert_missing_watchlist_rows(
            client,
            rows,
        )

    assert [row["symbol"] for row in inserted] == ["9984"]
    assert [row["symbol"] for row in skipped] == ["7203"]
    post_requests = [request for request in requests if request.method == "POST"]
    assert len(post_requests) == 1
    assert post_requests[0].url.params["on_conflict"] == "symbol,valid_date"
    assert post_requests[0].headers["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert json.loads(post_requests[0].content.decode())[0]["symbol"] == "9984"


def test_cli_dry_run_writes_plan(tmp_path: Path, monkeypatch) -> None:
    candidates_json = tmp_path / "candidates.json"
    output_json = tmp_path / "watchlist.json"
    candidates_json.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "upsert-event-candidates-watchlist.py",
            "--candidates-json",
            str(candidates_json),
            "--output-json",
            str(output_json),
            "--dry-run",
        ],
    )

    assert upsert_event_candidates_watchlist.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["planned_count"] == 2
    assert payload["inserted_count"] == 0
