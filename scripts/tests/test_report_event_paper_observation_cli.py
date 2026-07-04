from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx


def _load_module():
    path = Path(__file__).resolve().parents[1] / "report-event-paper-observation.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("report_event_paper_observation", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report_event_paper_observation = _load_module()


def _payload(*, published: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_id": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
        "mode": "paper_publish" if published else "dry_run",
        "candidates": [
            {
                "candidate_id": (
                    "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
                ),
                "cluster_id": "cluster-1",
                "observation_id": "obs-1",
                "event_id": "event-1",
                "event_ids": ["event-1", "event-2"],
                "symbol": "7203",
                "signal_date": "2026-01-20",
                "entry_date": "2026-01-21",
                "entry_price_assumption": "1013",
                "stop_loss_price": "911.70",
                "max_hold_days": 20,
                "min_forecast_per": "8.425",
                "publish_ready": published,
            }
        ],
        "published": [],
    }
    if published:
        payload["published"] = [
            {
                "message_id": "pub-1",
                "signal_id": "11111111-1111-1111-1111-111111111111",
                "symbol": "7203",
                "topic": "strategy-signals-a",
            }
        ]
    return payload


def test_build_report_candidate_only_marks_dry_run() -> None:
    report = report_event_paper_observation.build_report(_payload(published=False), rows=None)

    assert report["summary"]["with_supabase"] is False
    assert report["summary"]["status_counts"] == {"dry_run_only": 1}
    row = report["rows"][0]
    assert row["symbol"] == "7203"
    assert row["strategy_signal_id"] is None
    assert row["reconciliation_status"] == "dry_run_only"


def test_fetch_and_build_report_reconciles_open_position() -> None:
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        params = dict(request.url.params)
        if path == "/rest/v1/strategy_logs":
            assert params["signal_id"] == "in.(11111111-1111-1111-1111-111111111111)"
            return httpx.Response(
                200,
                json=[
                    {
                        "signal_id": "11111111-1111-1111-1111-111111111111",
                        "source": "RULE",
                        "symbol": "7203",
                        "action": "BUY",
                        "confidence": 0.51,
                        "reasoning": "{}",
                        "created_at": "2026-01-21T00:01:00+00:00",
                    }
                ],
            )
        if path == "/rest/v1/aggregator_logs":
            assert params["strategy_signal_id_a"] == "in.(11111111-1111-1111-1111-111111111111)"
            return httpx.Response(
                200,
                json=[
                    {
                        "signal_id": "22222222-2222-2222-2222-222222222222",
                        "symbol": "7203",
                        "action": "BUY",
                        "confidence": 0.51,
                        "signal_source": "RULE",
                        "strategy_signal_id_a": "11111111-1111-1111-1111-111111111111",
                        "strategy_signal_id_b": None,
                        "created_at": "2026-01-21T00:01:01+00:00",
                    }
                ],
            )
        if path == "/rest/v1/trades_paper":
            if "unified_signal_id" in params:
                assert params["unified_signal_id"] == "in.(22222222-2222-2222-2222-222222222222)"
            return httpx.Response(
                200,
                json=[
                    {
                        "trade_id": "trade-buy",
                        "symbol": "7203",
                        "side": "BUY",
                        "quantity": 100,
                        "price": "1023",
                        "signal_source": "RULE",
                        "unified_signal_id": "22222222-2222-2222-2222-222222222222",
                        "executed_at": "2026-01-21T00:02:00+00:00",
                    }
                ],
            )
        if path == "/rest/v1/positions":
            assert params["trade_type"] == "eq.paper"
            assert params["symbol"] == "in.(7203)"
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "7203",
                        "trade_type": "paper",
                        "side": "LONG",
                        "quantity": 100,
                        "entry_price": "1023",
                        "current_price": "1030",
                        "unrealized_pnl": "700",
                        "holding_type": "swing",
                        "stop_loss_price": "911.70",
                        "max_hold_days": 20,
                        "scheduled_exit_date": "2026-02-18",
                        "opened_at": "2026-01-21T00:02:00+00:00",
                    }
                ],
            )
        return httpx.Response(404)

    with httpx.Client(
        base_url="https://example.supabase.co",
        transport=httpx.MockTransport(_handler),
    ) as client:
        rows = report_event_paper_observation.fetch_supabase_rows(
            client,
            payload=_payload(published=True),
        )

    report = report_event_paper_observation.build_report(_payload(published=True), rows=rows)

    assert [request.url.path for request in requests].count("/rest/v1/trades_paper") == 2
    assert report["summary"]["status_counts"] == {"open_position": 1}
    row = report["rows"][0]
    assert row["strategy_log_found"] is True
    assert row["aggregator_log_found"] is True
    assert row["buy_trade_id"] == "trade-buy"
    assert row["position_open"] is True
    assert row["position_scheduled_exit_date"] == "2026-02-18"
    assert row["entry_slippage_bps"] == "98.71668311944718657453110000"


def test_cli_writes_candidate_only_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidates_json = tmp_path / "candidates.json"
    output_json = tmp_path / "report.json"
    output_csv = tmp_path / "report.csv"
    candidates_json.write_text(json.dumps(_payload(published=False)), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report-event-paper-observation.py",
            "--candidates-json",
            str(candidates_json),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--skip-supabase",
        ],
    )

    assert report_event_paper_observation.main() == 0

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["summary"]["status_counts"] == {"dry_run_only": 1}
    assert "reconciliation_status" in output_csv.read_text(encoding="utf-8")
