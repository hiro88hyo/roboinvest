import asyncio
import base64
import csv
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from trade_contracts.enums import Action, SignalSource, TradingStyle
from trade_contracts.signal import StrategySignal


def _load_module():
    path = Path(__file__).resolve().parents[1] / "detect-event-cluster-paper-candidates.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("detect_event_cluster_paper_candidates", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detect_event_cluster_paper_candidates = _load_module()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_ohlcv(path: Path) -> None:
    start = date(2026, 1, 1)
    rows = []
    for idx in range(55):
        current = start + timedelta(days=idx)
        close = 1000 + idx
        rows.append(
            {
                "symbol": "7203",
                "date": current.isoformat(),
                "open": close + 1,
                "high": close + 10,
                "low": close - 10,
                "close": close,
                "volume": 500000,
                "turnover": close * 500000,
            }
        )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_one_week_ohlcv(path: Path) -> None:
    trading_dates = [
        date(2026, 1, 5) + timedelta(days=idx)
        for idx in range(40)
        if (date(2026, 1, 5) + timedelta(days=idx)).weekday() < 5
    ]
    rows = []
    for symbol_idx, symbol in enumerate(("7203", "6501", "9984")):
        base = 1000 + symbol_idx * 250
        for idx, current in enumerate(trading_dates):
            close = base + idx
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.isoformat(),
                    "open": close + 1,
                    "high": close + 10,
                    "low": close - 10,
                    "close": close,
                    "volume": 500000,
                    "turnover": close * 500000,
                }
            )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _summary_row(
    *,
    code: str,
    disc_date: str,
    disc_no: str,
    doc_type: str,
    feps: str = "100",
    fdiv: str = "40",
) -> dict[str, object]:
    return {
        "Code": code,
        "DiscDate": disc_date,
        "DiscTime": "15:30:00",
        "DiscNo": disc_no,
        "DocType": doc_type,
        "CurFYEn": "2026-03-31",
        "FEPS": feps,
        "FOP": "1000",
        "FNP": "900",
        "FSales": "10000",
        "EPS": "90",
        "BPS": "1000",
        "FDivAnn": fdiv,
    }


def _summary_rows() -> list[dict[str, object]]:
    return [
        {
            "Code": "72030",
            "DiscDate": "2026-01-20",
            "DiscTime": "15:30:00",
            "DiscNo": "prior-forecast",
            "DocType": "ForecastRevision_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "100",
            "FOP": "1000",
            "FNP": "900",
            "FSales": "10000",
            "EPS": "90",
            "BPS": "1000",
            "FDivAnn": "40",
        },
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "DiscTime": "15:30:00",
            "DiscNo": "earnings-current",
            "DocType": "3QFinancialStatements_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "120",
            "FOP": "1200",
            "FNP": "1000",
            "FSales": "11000",
            "EPS": "95",
            "BPS": "1000",
            "FDivAnn": "50",
        },
        {
            "Code": "72030",
            "DiscDate": "2026-01-21",
            "DiscTime": "15:30:00",
            "DiscNo": "dividend-current",
            "DocType": "DividendRevision_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FEPS": "120",
            "FOP": "1200",
            "FNP": "1000",
            "FSales": "11000",
            "EPS": "95",
            "BPS": "1000",
            "FDivAnn": "50",
        },
    ]


def _one_week_summary_rows() -> list[dict[str, object]]:
    return [
        _summary_row(
            code="72030",
            disc_date="2026-01-19",
            disc_no="7203-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="40",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-20",
            disc_no="7203-earnings",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="120",
            fdiv="50",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-20",
            disc_no="7203-dividend",
            doc_type="DividendRevision_Consolidated_JP",
            feps="120",
            fdiv="50",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-20",
            disc_no="6501-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="30",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-21",
            disc_no="6501-earnings-high-per",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="30",
            fdiv="45",
        ),
        _summary_row(
            code="65010",
            disc_date="2026-01-21",
            disc_no="6501-dividend-high-per",
            doc_type="DividendRevision_Consolidated_JP",
            feps="30",
            fdiv="45",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-21",
            disc_no="9984-prior",
            doc_type="ForecastRevision_Consolidated_JP",
            feps="100",
            fdiv="10",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-22",
            disc_no="9984-earnings-missing-per",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="",
            fdiv="20",
        ),
        _summary_row(
            code="99840",
            disc_date="2026-01-22",
            disc_no="9984-dividend-missing-per",
            doc_type="DividendRevision_Consolidated_JP",
            feps="",
            fdiv="20",
        ),
        _summary_row(
            code="72030",
            disc_date="2026-01-23",
            disc_no="7203-earnings-only",
            doc_type="3QFinancialStatements_Consolidated_JP",
            feps="130",
            fdiv="50",
        ),
    ]


def test_detect_cluster_v1_candidates_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "candidates.json"
    output_csv = tmp_path / "candidates.csv"
    _write_jsonl(financial_path, _summary_rows())
    _write_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
            "--fetched-at",
            "2026-01-21T07:00:00+00:00",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["publish_enabled"] is False
    assert payload["rule"]["catastrophic_stop_pct"] == "-0.10"
    assert payload["summary"]["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["candidate_id"] == (
        "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research"
    )
    assert candidate["symbol"] == "7203"
    assert candidate["has_earnings_result"] is True
    assert candidate["has_dividend_increase"] is True
    assert candidate["max_hold_days"] == 20
    assert candidate["stop_loss_price"] == "919.80"
    assert candidate["publish_ready"] is False
    csv_text = output_csv.read_text(encoding="utf-8")
    assert "publish_ready" in csv_text
    assert "False" in csv_text


def test_one_week_detection_fixture_matches_research_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-one-week.jsonl"
    ohlcv_path = tmp_path / "ohlcv-one-week.csv"
    output_json = tmp_path / "candidates-one-week.json"
    output_csv = tmp_path / "candidates-one-week.csv"
    _write_jsonl(financial_path, _one_week_summary_rows())
    _write_one_week_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--fetched-at",
            "2026-01-23T07:00:00+00:00",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["publish_enabled"] is False
    assert payload["paper_live_enabled"] is False
    assert payload["rule"]["catastrophic_stop_pct"] == "-0.10"
    assert payload["summary"] == {
        "observation_count": 10,
        "candidate_count": 2,
        "exclusion_count": 1,
        "published_count": 0,
    }
    assert [(row["symbol"], row["signal_date"]) for row in payload["candidates"]] == [
        ("7203", "2026-01-20"),
        ("9984", "2026-01-22"),
    ]
    assert payload["candidates"][0]["min_forecast_per"] == "8.425"
    assert payload["candidates"][0]["stop_loss_price"] == "911.70"
    assert payload["candidates"][1]["min_forecast_per"] is None
    assert [
        (row["symbol"], row["signal_date"], row["reason"], row["min_forecast_per"])
        for row in payload["exclusions"]
    ] == [("6501", "2026-01-21", "forecast_per_value_guard", "42.06666666666666666666666667")]


def test_signal_date_filter_keeps_one_day_from_one_week_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial-one-week.jsonl"
    ohlcv_path = tmp_path / "ohlcv-one-week.csv"
    output_json = tmp_path / "candidates-filtered.json"
    output_csv = tmp_path / "candidates-filtered.csv"
    _write_jsonl(financial_path, _one_week_summary_rows())
    _write_one_week_ohlcv(ohlcv_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-20",
            "--fetched-at",
            "2026-01-23T07:00:00+00:00",
        ],
    )

    assert detect_event_cluster_paper_candidates.main() == 0

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["signal_date"] == "2026-01-20"
    assert payload["summary"] == {
        "observation_count": 3,
        "candidate_count": 1,
        "exclusion_count": 0,
        "published_count": 0,
    }
    assert [(row["symbol"], row["signal_date"]) for row in payload["candidates"]] == [
        ("7203", "2026-01-20")
    ]


def _publish_candidate() -> dict[str, object]:
    return {
        "candidate_id": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
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
        "publish_ready": True,
    }


def test_publish_requires_paper_trade_mode() -> None:
    async def _supabase_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/system_status"
        return httpx.Response(200, json=[{"trade_mode": "live"}])

    async def _pubsub_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("publish must not be called when trade_mode is live")

    with pytest.raises(detect_event_cluster_paper_candidates.PreflightError):
        asyncio.run(
            detect_event_cluster_paper_candidates.publish_paper_candidates(
                [_publish_candidate()],
                settings=detect_event_cluster_paper_candidates.PublishSettings(
                    supabase_url="https://example.supabase.co",
                    supabase_secret_key="k",
                    pubsub_project_id="trade-ai-dev",
                ),
                supabase_transport=httpx.MockTransport(_supabase_handler),
                pubsub_transport=httpx.MockTransport(_pubsub_handler),
            )
        )


def test_publish_paper_candidates_publishes_strategy_signal() -> None:
    published: list[httpx.Request] = []
    strategy_log_requests: list[httpx.Request] = []

    async def _supabase_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/rest/v1/system_status":
            return httpx.Response(200, json=[{"trade_mode": "paper"}])
        if request.method == "POST" and request.url.path == "/rest/v1/strategy_logs":
            strategy_log_requests.append(request)
            return httpx.Response(201, json=[])
        return httpx.Response(404)

    async def _pubsub_handler(request: httpx.Request) -> httpx.Response:
        published.append(request)
        return httpx.Response(200, json={"messageIds": ["pub-1"]})

    result = asyncio.run(
        detect_event_cluster_paper_candidates.publish_paper_candidates(
            [_publish_candidate()],
            settings=detect_event_cluster_paper_candidates.PublishSettings(
                supabase_url="https://example.supabase.co",
                supabase_secret_key="k",
                pubsub_project_id="trade-ai-dev",
                pubsub_topic_signals="strategy-signals-a",
                pubsub_emulator_host="pubsub:8085",
                confidence=0.51,
            ),
            now=datetime(2026, 1, 21, 0, 1, tzinfo=UTC),
            supabase_transport=httpx.MockTransport(_supabase_handler),
            pubsub_transport=httpx.MockTransport(_pubsub_handler),
        )
    )

    assert result == [
        {
            "message_id": "pub-1",
            "signal_id": result[0]["signal_id"],
            "symbol": "7203",
            "topic": "strategy-signals-a",
        }
    ]
    assert len(strategy_log_requests) == 1
    log_request = strategy_log_requests[0]
    assert log_request.url.params["on_conflict"] == "signal_id"
    assert log_request.headers["Prefer"] == "resolution=merge-duplicates,return=minimal"
    log_rows = json.loads(log_request.content.decode())
    assert len(log_rows) == 1
    assert log_rows[0]["source"] == "RULE"
    assert log_rows[0]["symbol"] == "7203"
    assert log_rows[0]["action"] == "BUY"
    assert log_rows[0]["reasoning"]
    assert len(published) == 1
    request = published[0]
    assert request.url.path == "/v1/projects/trade-ai-dev/topics/strategy-signals-a:publish"
    body = json.loads(request.content.decode())
    message = body["messages"][0]
    assert message["attributes"] == {
        "symbol": "7203",
        "source": "RULE",
        "candidate_id": "event_cluster_earnings_dividend_value_guard_fixed20_stop_v1_research",
        "mode": "paper",
    }
    signal = StrategySignal.model_validate_json(base64.b64decode(message["data"]))
    assert log_rows[0]["signal_id"] == str(signal.signal_id)
    assert signal.source is SignalSource.RULE
    assert signal.action is Action.BUY
    assert signal.symbol == "7203"
    assert signal.price == Decimal("1013")
    assert signal.confidence == 0.51
    assert signal.holding_type is TradingStyle.SWING
    assert signal.stop_loss_price == Decimal("911.70")
    assert signal.max_hold_days == 20
    assert signal.created_at == datetime(2026, 1, 21, 0, 1, tzinfo=UTC)
    assert json.loads(signal.reasoning or "{}")["mode"] == "paper_observation"


def test_main_publish_requires_env_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    financial_path = tmp_path / "financial.jsonl"
    ohlcv_path = tmp_path / "ohlcv.csv"
    output_json = tmp_path / "candidates.json"
    output_csv = tmp_path / "candidates.csv"
    _write_jsonl(financial_path, _summary_rows())
    _write_ohlcv(ohlcv_path)
    monkeypatch.delenv("EVENT_CLUSTER_PAPER_PUBLISH_ENABLED", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "detect-event-cluster-paper-candidates.py",
            "--financial-summary-jsonl",
            str(financial_path),
            "--ohlcv",
            str(ohlcv_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--signal-date",
            "2026-01-21",
            "--publish-paper",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        detect_event_cluster_paper_candidates.main()

    assert "EVENT_CLUSTER_PAPER_PUBLISH_ENABLED=true" in str(exc_info.value)
    assert not output_json.exists()
